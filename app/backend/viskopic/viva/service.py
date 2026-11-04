"""Viva orchestration shared by the API and the worker."""
from __future__ import annotations

import base64
import html
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timezone
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from .. import crypto
from ..audit import record
from ..config import get_settings
from ..db import tenant_tx
from ..deps import institution_dek
from ..mailer import Message, get_mailer
from ..storage import get_storage
from . import ai

log = logging.getLogger("viskopic.viva")

OPEN_STATUSES = ("queued", "sent", "opened", "in_progress")


class VivaJobError(Exception):
    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


# ------------------------------------------------------------------ tokens


def new_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def nonce_hash(nonce: str) -> bytes:
    return hashlib.sha256(b"viva-session:" + nonce.encode()).digest()


def nonce_matches(stored: bytes | None, nonce: str | None) -> bool:
    return bool(stored and nonce) and hmac.compare_digest(bytes(stored), nonce_hash(nonce))


def viva_link(token: str) -> str:
    return f"{get_settings().web_base_url.rstrip('/')}/viva/{token}"


def now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ audio


def sniff_audio(data: bytes) -> str | None:
    """Container type from magic bytes; the browser's Content-Type is not trusted."""
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    return None


_WEBM_VIDEO = (b"V_VP8", b"V_VP9", b"V_AV1", b"V_MPEG4/ISO/AVC")
_MP4_VIDEO = re.compile(rb"hdlr.{8}vide", re.DOTALL)


def has_video_track(data: bytes, mime: str) -> bool:
    """Whether the recording contains a video track, read from the container's track headers."""
    head = data[:262144]
    if mime == "audio/webm":
        return any(codec in head for codec in _WEBM_VIDEO)
    if mime == "audio/mp4":
        return _MP4_VIDEO.search(head) is not None
    return False


def media_type(mime: str, video: bool) -> str:
    return mime.replace("audio/", "video/") if video else mime


def audio_key(institution_id, recording_id) -> str:
    return f"{institution_id}/viva-audio/{recording_id}"


# ------------------------------------------------------------------ invitations


def create_questions(conn: Connection, dek: bytes, institution_id: UUID, invitation_id: UUID,
                     questions: list[dict]) -> None:
    for pos, q in enumerate(questions, start=1):
        qid = conn.execute("SELECT gen_random_uuid() AS id").fetchone()["id"]
        conn.execute(
            "INSERT INTO viva_questions (id, institution_id, invitation_id, position, text_ciphertext, "
            "passage_ciphertext, passage_start, passage_end, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (qid, institution_id, invitation_id, pos,
             crypto.encrypt(dek, q["text"].encode(), f"viva_question:{qid}"),
             crypto.encrypt(dek, q["passage"].encode(), f"viva_passage:{qid}") if q.get("passage") else None,
             q.get("passage_start"), q.get("passage_end"), q["source"]))


def queue_email(conn: Connection, institution_id: UUID, invitation_id: UUID) -> None:
    conn.execute("INSERT INTO jobs (institution_id, kind, viva_invitation_id) VALUES (%s, 'send_viva_email', %s)",
                 (institution_id, invitation_id))


def revoke(conn: Connection, invitation_id: UUID, reason: str, by: UUID | None) -> bool:
    row = conn.execute(
        "UPDATE viva_invitations SET status = 'revoked', revoked_at = now(), revoked_by = %s, revoke_reason = %s, "
        "token_ciphertext = NULL, session_nonce_sha256 = NULL WHERE id = %s AND status = ANY(%s) RETURNING id",
        (by, reason, invitation_id, list(OPEN_STATUSES))).fetchone()
    return row is not None


def revoke_for_cases(conn: Connection, case_ids: list, reason: str) -> list[str]:
    """Called when a case is withdrawn (its document was removed): open invitations stop working."""
    if not case_ids:
        return []
    rows = conn.execute(
        "UPDATE viva_invitations SET status = 'revoked', revoked_at = now(), revoke_reason = %s, "
        "token_ciphertext = NULL, session_nonce_sha256 = NULL WHERE case_id = ANY(%s) AND status = ANY(%s) "
        "RETURNING id", (reason, case_ids, list(OPEN_STATUSES))).fetchall()
    return [str(r["id"]) for r in rows]


def expire_if_due(conn: Connection, inv: dict) -> dict:
    """Not started by the deadline: the invitation expires. A started viva may finish its questions."""
    if inv["status"] in ("queued", "sent", "opened") and inv["deadline_at"] <= now():
        conn.execute("UPDATE viva_invitations SET status = 'expired', token_ciphertext = NULL WHERE id = %s",
                     (inv["id"],))
        inv = {**inv, "status": "expired"}
    return inv


# ------------------------------------------------------------------ worker: email


def build_email(institution_name: str, link: str, deadline: datetime, is_test: bool) -> Message:
    """Deliberately minimal: no score, no mention of AI, no assignment title."""
    when = deadline.strftime("%d %B %Y, %H:%M UTC")
    name_html, link_html = html.escape(institution_name), html.escape(link, quote=True)
    subject = ("[TEST] " if is_test else "") + f"{institution_name}: a short follow-up about your coursework"
    text = (
        f"Hello,\n\n{institution_name} has asked you to take part in a short follow-up conversation about a recent "
        f"piece of coursework. It takes about 15 minutes and is done in your web browser.\n\n"
        f"Please complete it by {when}:\n{link}\n\n"
        "This link is personal to you. Please don't share it. If you have questions or need adjustments, "
        "contact your module team.\n"
    )
    body_html = (
        f"<p>Hello,</p><p>{name_html} has asked you to take part in a short follow-up conversation about a "
        f"recent piece of coursework. It takes about 15 minutes and is done in your web browser.</p>"
        f"<p>Please complete it by {when}:<br><a href=\"{link_html}\">{link_html}</a></p>"
        "<p>This link is personal to you. Please don't share it. If you have questions or need adjustments, "
        "contact your module team.</p>"
    )
    return Message(to="", subject=subject, text=text, html=body_html)


def run_send_email(job: dict) -> None:
    iid, inv_id = job["institution_id"], job["viva_invitation_id"]
    with tenant_tx(iid) as conn:
        inv = conn.execute(
            "SELECT v.id, v.status, v.deadline_at, v.is_test, v.email_ciphertext, v.token_ciphertext, i.name "
            "FROM viva_invitations v JOIN institutions i ON i.id = v.institution_id WHERE v.id = %s",
            (inv_id,)).fetchone()
        if inv is None or inv["status"] not in ("queued", "opened") or inv["token_ciphertext"] is None:
            conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now() WHERE id = %s",
                         (job["id"],))
            return  # revoked, expired, already started through the test link, or already sent
        dek = institution_dek(conn, iid)
    email = crypto.decrypt(dek, inv["email_ciphertext"], f"viva_invitation_email:{inv_id}").decode()
    token = crypto.decrypt(dek, inv["token_ciphertext"], f"viva_token:{inv_id}").decode()
    msg = build_email(inv["name"], viva_link(token), inv["deadline_at"], inv["is_test"])
    msg.to = email
    try:
        get_mailer().send(msg)
    except Exception as e:
        raise VivaJobError(f"email could not be sent: {type(e).__name__}: {e}"[:300]) from e
    with tenant_tx(iid) as conn:
        conn.execute(
            "UPDATE viva_invitations SET status = CASE WHEN status = 'queued' THEN 'sent' ELSE status END, "
            "sent_at = now(), token_ciphertext = NULL, last_error = NULL WHERE id = %s", (inv_id,))
        conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now() WHERE id = %s",
                     (job["id"],))
        record(conn, None, "viva.email_sent", "viva_invitation", inv_id, institution_id=iid, actor_label="worker")


def on_email_failed(conn: Connection, job: dict, message: str) -> None:
    conn.execute("UPDATE viva_invitations SET status = 'failed', last_error = %s, token_ciphertext = NULL "
                 "WHERE id = %s AND status = 'queued'", (message, job["viva_invitation_id"]))
    record(conn, None, "viva.email_failed", "viva_invitation", job["viva_invitation_id"], {"error": message},
           institution_id=job["institution_id"], actor_label="worker")


# ------------------------------------------------------------------ worker: transcription


def run_transcribe(job: dict) -> None:
    iid, rec_id = job["institution_id"], job["viva_recording_id"]
    with tenant_tx(iid) as conn:
        rec = conn.execute(
            "SELECT r.id, r.storage_key, r.sha256, r.mime_type, r.status, v.status AS inv_status "
            "FROM viva_recordings r JOIN viva_invitations v ON v.id = r.invitation_id WHERE r.id = %s",
            (rec_id,)).fetchone()
        if rec is None or rec["status"] != "stored":
            raise VivaJobError("recording no longer exists", permanent=True)
        if rec["inv_status"] == "revoked":
            raise VivaJobError("invitation revoked", permanent=True)
        dek = institution_dek(conn, iid)
    if not ai.enabled():
        raise VivaJobError("transcription is switched off (VISKOPIC_ALLOW_EXTERNAL_AI)", permanent=True)
    audio = crypto.decrypt(dek, get_storage().get(rec["storage_key"]), f"viva_audio:{rec_id}")
    if hashlib.sha256(audio).hexdigest() != rec["sha256"]:
        raise VivaJobError("checksum mismatch on stored audio", permanent=True)
    try:
        t = ai.transcribe(audio, rec["mime_type"])
    except ai.AIUnavailable as e:
        raise VivaJobError(str(e)) from e
    with tenant_tx(iid) as conn:
        current = conn.execute("SELECT attempts FROM jobs WHERE id = %s FOR UPDATE", (job["id"],)).fetchone()
        if current is None or current["attempts"] != job["attempts"]:
            return
        tid = conn.execute("SELECT gen_random_uuid() AS id").fetchone()["id"]
        conn.execute(
            "INSERT INTO viva_transcripts (id, institution_id, recording_id, text_ciphertext, timeline, provider, model) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (tid, iid, rec_id, crypto.encrypt(dek, t.text.encode(), f"viva_transcript:{tid}"), Jsonb(t.timeline),
             t.provider, t.model))
        conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now() WHERE id = %s",
                     (job["id"],))
        record(conn, None, "viva.transcribed", "viva_recording", rec_id, {"provider": t.provider, "model": t.model},
               institution_id=iid, actor_label="worker")


def on_transcribe_failed(conn: Connection, job: dict, message: str) -> None:
    record(conn, None, "viva.transcription_failed", "viva_recording", job["viva_recording_id"], {"error": message},
           institution_id=job["institution_id"], actor_label="worker")
