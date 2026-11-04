"""Viva orchestration shared by the API and the worker."""
from __future__ import annotations

import base64
import html
import hashlib
import hmac
import json
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
from . import ai, visq

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
    """The questions, frozen as sent (reviewer, template). Visq 2 adds its own as it releases them."""
    for pos, q in enumerate(questions, start=1):
        qid = conn.execute("SELECT gen_random_uuid() AS id").fetchone()["id"]
        note = (q.get("reviewer_note") or "").strip()
        conn.execute(
            "INSERT INTO viva_questions (id, institution_id, invitation_id, position, text_ciphertext, "
            "passage_ciphertext, passage_start, passage_end, source, qtype, reviewer_note_ciphertext) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (qid, institution_id, invitation_id, pos,
             crypto.encrypt(dek, q["text"].encode(), f"viva_question:{qid}"),
             crypto.encrypt(dek, q["passage"].encode(), f"viva_passage:{qid}") if q.get("passage") else None,
             q.get("passage_start"), q.get("passage_end"), q["source"], q.get("qtype"),
             crypto.encrypt(dek, note.encode(), f"viva_question_note:{qid}") if note else None))


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
        _visq2_after_transcript(conn, iid, rec_id)


def on_transcribe_failed(conn: Connection, job: dict, message: str) -> None:
    record(conn, None, "viva.transcription_failed", "viva_recording", job["viva_recording_id"], {"error": message},
           institution_id=job["institution_id"], actor_label="worker")
    _visq2_after_transcript(conn, job["institution_id"], job["viva_recording_id"])   # go on with no text


def _visq2_after_transcript(conn: Connection, iid: UUID, recording_id: UUID) -> None:
    """A spoken answer in a Visq 2 viva: feed it into Visq once its transcript exists (or transcription failed)."""
    row = conn.execute("SELECT r.question_id FROM viva_recordings r JOIN viva_invitations v ON v.id = r.invitation_id "
                       "WHERE r.id = %s AND v.flow = 'visq2' AND v.status = 'in_progress'", (recording_id,)).fetchone()
    if row:
        queue_visq2_step(conn, iid, row["question_id"])


# ------------------------------------------------------------------ Visq 2 (world-tree flow)
#
# Visq's state lives in viva_visq2_states, encrypted with the institution's key. Steps that may call a model run in
# the worker: `visq2_prepare` when the invitation is created, `visq2_step` after each answer. The student page asks
# for the next question; while a step is pending the server answers "waiting", and after viva_followup_wait_seconds
# it applies the step itself offline (Visq's heuristics, no model), so a slow model never traps a student.
# Writes are versioned: a slow worker can't overwrite a newer state.

STAGE_QTYPE = {"wv_clarify": "clarify", "wv_extension": "extension", "wv_viva": "viva"}


def state_aad(invitation_id) -> str:
    return f"viva_visq2_state:{invitation_id}"


def load_visq2(conn: Connection, dek: bytes, invitation_id: UUID, lock: bool = False) -> tuple[dict, dict | None]:
    row = conn.execute("SELECT * FROM viva_visq2_states WHERE invitation_id = %s" + (" FOR UPDATE" if lock else ""),
                       (invitation_id,)).fetchone()
    state = None
    if row and row["state_ciphertext"]:
        state = json.loads(crypto.decrypt(dek, row["state_ciphertext"], state_aad(invitation_id)))
    return row, state


def save_visq2(conn: Connection, dek: bytes, invitation_id: UUID, state: dict, *, expect_version: int,
               applied_question_id: UUID | None = None, status: str = "ready", engine: str | None = None,
               error: str | None = None) -> bool:
    row = conn.execute(
        "UPDATE viva_visq2_states SET state_ciphertext = %s, version = version + 1, status = %s, "
        "applied_question_id = COALESCE(%s, applied_question_id), engine = COALESCE(%s, engine), error = %s, "
        "updated_at = now() WHERE invitation_id = %s AND version = %s RETURNING version",
        (crypto.encrypt(dek, json.dumps(state).encode(), state_aad(invitation_id)), status, applied_question_id, engine,
         error, invitation_id, expect_version)).fetchone()
    return row is not None


def _essay_and_topic(conn: Connection, dek: bytes, invitation_id: UUID) -> tuple[str, str]:
    row = conn.execute(
        "SELECT d.id AS document_id, t.text_enc, a.title, a.prompt FROM viva_invitations v JOIN cases c ON c.id = v.case_id "
        "JOIN documents d ON d.id = c.document_id LEFT JOIN document_texts t ON t.document_id = d.id "
        "LEFT JOIN assignments a ON a.id = d.assignment_id WHERE v.id = %s", (invitation_id,)).fetchone()
    if row is None or row["text_enc"] is None:
        raise VivaJobError("the submission's text is not available", permanent=True)
    essay = crypto.decrypt(dek, row["text_enc"], f"document_text:{row['document_id']}").decode()
    return essay, (row["prompt"] or row["title"] or "")


def _done(conn: Connection, job: dict) -> None:
    conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now() WHERE id = %s", (job["id"],))


def _current_job(conn: Connection, job: dict) -> bool:
    current = conn.execute("SELECT attempts FROM jobs WHERE id = %s FOR UPDATE", (job["id"],)).fetchone()
    return current is not None and current["attempts"] == job["attempts"]


def run_visq2_prepare(job: dict) -> None:
    iid, inv_id = job["institution_id"], job["viva_invitation_id"]
    with tenant_tx(iid) as conn:
        inv = conn.execute("SELECT v.status, v.extension_seconds, v.visq2_full, s.status AS prep, s.version "
                           "FROM viva_invitations v JOIN viva_visq2_states s ON s.invitation_id = v.id WHERE v.id = %s",
                           (inv_id,)).fetchone()
        if inv is None or inv["prep"] != "preparing" or inv["status"] in ("revoked", "expired", "failed"):
            _done(conn, job)
            return
        dek = institution_dek(conn, iid)
        essay, topic = _essay_and_topic(conn, dek, inv_id)
    try:
        state = visq.prepare(essay, topic, inv["extension_seconds"], inv["visq2_full"])
    except visq.VisqUnavailable as e:
        raise VivaJobError(str(e), permanent=True) from e
    engine = visq.engine_label() + ("" if state.get("llm") else "; structure built offline (model unavailable)")
    with tenant_tx(iid) as conn:
        if not _current_job(conn, job):
            return
        if save_visq2(conn, dek, inv_id, state, expect_version=inv["version"], engine=engine):
            record(conn, None, "viva.visq2_prepared", "viva_invitation", inv_id,
                   {"engine": engine, "model_used": bool(state.get("llm"))}, institution_id=iid, actor_label="worker")
        _done(conn, job)


def on_visq2_prepare_failed(conn: Connection, job: dict, message: str) -> None:
    conn.execute("UPDATE viva_visq2_states SET status = 'failed', error = %s, version = version + 1, updated_at = now() "
                 "WHERE invitation_id = %s AND status = 'preparing'", (message[:300], job["viva_invitation_id"]))
    record(conn, None, "viva.visq2_prepare_failed", "viva_invitation", job["viva_invitation_id"], {"error": message[:300]},
           institution_id=job["institution_id"], actor_label="worker")


def answer_text(conn: Connection, dek: bytes, question_id: UUID) -> str:
    """The typed answer, or the newest transcript of a spoken one ("" when there is none)."""
    resp = conn.execute("SELECT id, kind, answer_ciphertext FROM viva_responses WHERE question_id = %s",
                        (question_id,)).fetchone()
    if resp is None or resp["kind"] == "skipped":
        return ""
    if resp["kind"] == "typed":
        return crypto.decrypt(dek, resp["answer_ciphertext"], f"viva_response:{resp['id']}").decode()
    t = conn.execute(
        "SELECT t.id, t.text_ciphertext FROM viva_transcripts t JOIN viva_recordings r ON r.id = t.recording_id "
        "WHERE r.question_id = %s ORDER BY t.created_at DESC LIMIT 1", (question_id,)).fetchone()
    return crypto.decrypt(dek, t["text_ciphertext"], f"viva_transcript:{t['id']}").decode() if t else ""


def queue_visq2_step(conn: Connection, institution_id: UUID, question_id: UUID) -> None:
    conn.execute("INSERT INTO jobs (institution_id, kind, viva_question_id) VALUES (%s, 'visq2_step', %s)",
                 (institution_id, question_id))


def run_visq2_step(job: dict) -> None:
    """Feed one answer (or the empty answer of a timed-out question) into Visq's flow."""
    iid, qid = job["institution_id"], job["viva_question_id"]
    with tenant_tx(iid) as conn:
        q = conn.execute("SELECT q.invitation_id, q.qtype, v.status FROM viva_questions q "
                         "JOIN viva_invitations v ON v.id = q.invitation_id WHERE q.id = %s", (qid,)).fetchone()
        dek = institution_dek(conn, iid)
        row, state = load_visq2(conn, dek, q["invitation_id"]) if q else (None, None)
        if q is None or row is None or state is None or row["applied_question_id"] == qid or q["status"] != "in_progress":
            _done(conn, job)
            return
        text = answer_text(conn, dek, qid)
        essay, _ = _essay_and_topic(conn, dek, q["invitation_id"])
    try:
        new = visq.take_answer(state, q["qtype"], text, essay)
    except visq.VisqStateError as e:
        raise VivaJobError(f"Visq did not take the answer: {e}", permanent=True) from e
    with tenant_tx(iid) as conn:
        if not _current_job(conn, job):
            return
        conn.execute("SELECT 1 FROM viva_invitations WHERE id = %s FOR NO KEY UPDATE", (q["invitation_id"],))
        if save_visq2(conn, dek, q["invitation_id"], new, expect_version=row["version"], applied_question_id=qid):
            record(conn, None, "viva.visq2_step", "viva_invitation", q["invitation_id"],
                   {"question_id": str(qid), "stage": q["qtype"], "next": new.get("status")},
                   institution_id=iid, actor_label="worker")
        _done(conn, job)


def on_visq2_step_failed(conn: Connection, job: dict, message: str) -> None:
    """The page moves on through the offline fallback when it next asks; just record why."""
    record(conn, None, "viva.visq2_step_failed", "viva_question", job["viva_question_id"], {"error": message[:300]},
           institution_id=job["institution_id"], actor_label="worker")


def apply_offline(conn: Connection, dek: bytes, iid: UUID, inv_id: UUID, row: dict, state: dict, cur: dict) -> dict:
    """The fallback when the worker's step is late or failed: Visq's code-only path. Caller holds the invitation lock."""
    text = answer_text(conn, dek, cur["id"])
    essay, _ = _essay_and_topic(conn, dek, inv_id)
    try:
        new = visq.take_answer(state, cur["qtype"], text, essay, offline=True)
    except visq.VisqStateError:
        new = state
    save_visq2(conn, dek, inv_id, new, expect_version=row["version"], applied_question_id=cur["id"])
    return new
