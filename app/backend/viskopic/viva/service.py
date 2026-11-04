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
    """The main questions, frozen as sent. Follow-ups are added later by the follow-up job."""
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
        q = conn.execute("SELECT question_id FROM viva_recordings WHERE id = %s", (rec_id,)).fetchone()
        queue_followup(conn, iid, q["question_id"])


def on_transcribe_failed(conn: Connection, job: dict, message: str) -> None:
    record(conn, None, "viva.transcription_failed", "viva_recording", job["viva_recording_id"], {"error": message},
           institution_id=job["institution_id"], actor_label="worker")
    q = conn.execute("SELECT question_id FROM viva_recordings WHERE id = %s", (job["viva_recording_id"],)).fetchone()
    if q:   # no transcript means no follow-up: let the student move on instead of waiting
        decide_without_model(conn, job["institution_id"], q["question_id"], "error")


# ------------------------------------------------------------------ follow-ups (Visq)
#
# After a main question is answered, Visq decides once: ask one follow-up, or move on. The decision is made by
# the worker (typed answer: straight away; spoken: after transcription) and recorded in viva_followup_decisions.
# The student page asks for the next question; until a decision exists the server answers "waiting", and after
# viva_followup_wait_seconds it records "timeout" and moves on, so a slow model never traps a student.
# Every path that records a decision first locks the invitation row, so the API and the worker never both decide.


def _main_question_for_followup(conn: Connection, question_id: UUID) -> dict | None:
    """The question, if it is a main question in an in-progress invitation with follow-ups on."""
    return conn.execute(
        "SELECT q.id, q.invitation_id, q.position, q.parent_id, v.followups_enabled, v.status "
        "FROM viva_questions q JOIN viva_invitations v ON v.id = q.invitation_id WHERE q.id = %s",
        (question_id,)).fetchone()


def queue_followup(conn: Connection, institution_id: UUID, question_id: UUID) -> bool:
    q = _main_question_for_followup(conn, question_id)
    if not q or q["parent_id"] is not None or not q["followups_enabled"] or q["status"] != "in_progress":
        return False
    if conn.execute("SELECT 1 FROM viva_followup_decisions WHERE question_id = %s", (question_id,)).fetchone():
        return False
    conn.execute("INSERT INTO jobs (institution_id, kind, viva_question_id) VALUES (%s, 'viva_followup', %s)",
                 (institution_id, question_id))
    return True


def _insert_decision(conn: Connection, institution_id: UUID, invitation_id: UUID, question_id: UUID, action: str,
                     reason: str, dek: bytes | None = None, why: str | None = None, followup_id: UUID | None = None,
                     engine: str | None = None) -> bool:
    did = conn.execute("SELECT gen_random_uuid() AS id").fetchone()["id"]
    row = conn.execute(
        "INSERT INTO viva_followup_decisions (id, institution_id, invitation_id, question_id, action, reason, "
        "why_ciphertext, followup_question_id, engine) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (question_id) DO NOTHING RETURNING id",
        (did, institution_id, invitation_id, question_id, action, reason,
         crypto.encrypt(dek, why.encode(), f"viva_followup_why:{did}") if (why and dek) else None,
         followup_id, engine)).fetchone()
    return row is not None


def decide_without_model(conn: Connection, institution_id: UUID, question_id: UUID, reason: str) -> bool:
    """Record "move on" for a main question (no answer, timeout, error). Locks the invitation first."""
    q = _main_question_for_followup(conn, question_id)
    if not q or q["parent_id"] is not None or not q["followups_enabled"]:
        return False
    conn.execute("SELECT 1 FROM viva_invitations WHERE id = %s FOR NO KEY UPDATE", (q["invitation_id"],))
    return _insert_decision(conn, institution_id, q["invitation_id"], question_id, "move_on", reason)


def followup_state(conn: Connection, institution_id: UUID, inv: dict, cur: dict) -> dict | str | None:
    """For a released, answered main question: the follow-up to release next (a question row), "wait", or None
    (move on). The caller holds the invitation lock."""
    if not inv["followups_enabled"] or cur.get("parent_id") is not None:
        return None
    dec = conn.execute("SELECT action, followup_question_id FROM viva_followup_decisions WHERE question_id = %s",
                       (cur["id"],)).fetchone()
    if dec is None:
        resp = conn.execute("SELECT kind, saved_at FROM viva_responses WHERE question_id = %s",
                            (cur["id"],)).fetchone()
        if resp is None or resp["kind"] == "skipped":
            _insert_decision(conn, institution_id, inv["id"], cur["id"], "move_on", "no_answer")
            return None
        if (now() - resp["saved_at"]).total_seconds() < get_settings().viva_followup_wait_seconds:
            return "wait"
        _insert_decision(conn, institution_id, inv["id"], cur["id"], "move_on", "timeout")
        return None
    if dec["action"] != "follow_up":
        return None
    fq = conn.execute(
        "SELECT q.id, q.position, q.parent_id, q.text_ciphertext, q.passage_ciphertext FROM viva_questions q "
        "LEFT JOIN viva_question_releases rl ON rl.question_id = q.id WHERE q.id = %s AND rl.question_id IS NULL",
        (dec["followup_question_id"],)).fetchone()
    return fq   # None once it has been released


def _answer_text(conn: Connection, dek: bytes, question_id: UUID) -> str | None:
    """The typed answer, or the newest transcript of the spoken one. None when there is nothing to read."""
    resp = conn.execute("SELECT id, kind, answer_ciphertext FROM viva_responses WHERE question_id = %s",
                        (question_id,)).fetchone()
    if resp is None or resp["kind"] == "skipped":
        return ""
    if resp["kind"] == "typed":
        return crypto.decrypt(dek, resp["answer_ciphertext"], f"viva_response:{resp['id']}").decode()
    t = conn.execute(
        "SELECT t.id, t.text_ciphertext FROM viva_transcripts t JOIN viva_recordings r ON r.id = t.recording_id "
        "WHERE r.question_id = %s ORDER BY t.created_at DESC LIMIT 1", (question_id,)).fetchone()
    return crypto.decrypt(dek, t["text_ciphertext"], f"viva_transcript:{t['id']}").decode() if t else None


def run_followup(job: dict) -> None:
    iid, qid = job["institution_id"], job["viva_question_id"]
    with tenant_tx(iid) as conn:
        q = _main_question_for_followup(conn, qid)
        done = (q is None or q["parent_id"] is not None or not q["followups_enabled"] or q["status"] != "in_progress"
                or conn.execute("SELECT 1 FROM viva_followup_decisions WHERE question_id = %s", (qid,)).fetchone())
        if done:
            conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now() WHERE id = %s",
                         (job["id"],))
            return
        dek = institution_dek(conn, iid)
        answer = _answer_text(conn, dek, qid)
        if answer is None:
            raise VivaJobError("the spoken answer has no transcript", permanent=True)
        row = conn.execute("SELECT text_ciphertext, passage_ciphertext FROM viva_questions WHERE id = %s",
                           (qid,)).fetchone()
        question = crypto.decrypt(dek, row["text_ciphertext"], f"viva_question:{qid}").decode()
        passage = (crypto.decrypt(dek, row["passage_ciphertext"], f"viva_passage:{qid}").decode()
                   if row["passage_ciphertext"] else None)
        asked = conn.execute(
            "SELECT q.id, q.source, q.text_ciphertext FROM viva_questions q JOIN viva_question_releases rl "
            "ON rl.question_id = q.id WHERE q.invitation_id = %s AND q.id <> %s ORDER BY rl.released_at",
            (q["invitation_id"], qid)).fetchall()
        texts = [(a["source"], crypto.decrypt(dek, a["text_ciphertext"], f"viva_question:{a['id']}").decode())
                 for a in asked]

    if visq.is_empty_answer(answer):
        action, reason, fq_text, why = "move_on", "no_answer", None, None
    else:
        try:
            d = visq.decide_followup(question, answer, passage,
                                     earlier_followups=[t for s, t in texts if s == "visq_followup"],
                                     session_asked=[t for _, t in texts])
        except visq.VisqUnavailable as e:
            raise VivaJobError(str(e), permanent=True) from e
        action, reason, why = d["action"], "model", (d.get("why") or "")[:500]
        fq_text = d.get("question") if action == "follow_up" else None
        if action == "follow_up" and not (fq_text and fq_text.strip()):
            action = "move_on"
    engine = visq.engine_label()

    with tenant_tx(iid) as conn:
        current = conn.execute("SELECT attempts FROM jobs WHERE id = %s FOR UPDATE", (job["id"],)).fetchone()
        if current is None or current["attempts"] != job["attempts"]:
            return
        inv = conn.execute("SELECT id, status FROM viva_invitations WHERE id = %s FOR NO KEY UPDATE",
                           (q["invitation_id"],)).fetchone()
        decided = conn.execute("SELECT 1 FROM viva_followup_decisions WHERE question_id = %s", (qid,)).fetchone()
        if inv["status"] == "in_progress" and not decided:
            fid = None
            if action == "follow_up":
                fid = conn.execute("SELECT gen_random_uuid() AS id").fetchone()["id"]
                conn.execute(
                    "INSERT INTO viva_questions (id, institution_id, invitation_id, position, text_ciphertext, "
                    "passage_ciphertext, passage_start, passage_end, source, qtype, parent_id) "
                    "SELECT %s, institution_id, invitation_id, position, %s, NULL, NULL, NULL, 'visq_followup', "
                    "'followup', id FROM viva_questions WHERE id = %s",
                    (fid, crypto.encrypt(dek, fq_text.strip().encode(), f"viva_question:{fid}"), qid))
            _insert_decision(conn, iid, inv["id"], qid, action, reason, dek, why, fid, engine)
            record(conn, None, "viva.followup_decided", "viva_invitation", inv["id"],
                   {"question_id": str(qid), "action": action, "reason": reason, "engine": engine},
                   institution_id=iid, actor_label="worker")
        conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now() WHERE id = %s",
                     (job["id"],))


def on_followup_failed(conn: Connection, job: dict, message: str) -> None:
    decide_without_model(conn, job["institution_id"], job["viva_question_id"], "error")
    record(conn, None, "viva.followup_failed", "viva_question", job["viva_question_id"], {"error": message[:300]},
           institution_id=job["institution_id"], actor_label="worker")
