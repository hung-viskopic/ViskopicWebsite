"""Viva, student side. No staff login: the link's token is the only credential.

Rules enforced here, on the server, so a modified browser can't get round them:
- One question at a time. The next question is released only after the current one is answered or its time is
  up, so questions can't be read ahead and there is no going back.
- Follow-ups (Visq): after a main question is answered, the next release waits until the worker has decided on
  one optional follow-up, at most `viva_followup_wait_seconds`, then moves on without one.
- Each question's deadline is measured on the server from its release (preparation + answer time). Answers
  arriving after the deadline plus a short grace period are rejected and recorded as a late write.
- One browser session at a time. Opening the link on another device takes the session over; the first device
  can no longer write, and the switch is recorded.

Responses never include case ids, the student's pseudonym, document data or anything about the analysis.
"""
import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .. import audit, crypto
from ..config import get_settings
from ..db import system_tx, tenant_tx
from ..deps import institution_dek
from ..storage import get_storage
from ..viva import ai
from ..viva import service as viva

router = APIRouter(prefix="/public/viva", tags=["viva-public"])

# ------------------------------------------------------------------ rate limit (per process, per client IP)

_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limit(request: Request) -> None:
    limit = get_settings().viva_rate_limit_per_minute
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _hits_lock:
        q = _hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "too many requests; please wait a moment")
        q.append(now)


def reset_rate_limits() -> None:
    with _hits_lock:
        _hits.clear()


# ------------------------------------------------------------------ token → tenant


def _resolve(token: str) -> tuple[UUID, UUID]:
    if not (20 <= len(token) <= 100):
        raise HTTPException(404, "this link is not valid")
    with system_tx() as conn:
        row = conn.execute("SELECT * FROM viva_resolve_token(%s)", (viva.token_hash(token),)).fetchone()
    if row is None:
        raise HTTPException(404, "this link is not valid")
    return row["institution_id"], row["invitation_id"]


def _event(conn: Connection, iid: UUID, inv_id: UUID, kind: str, question_id=None, detail: dict | None = None,
           source: str = "server") -> None:
    conn.execute("INSERT INTO viva_session_events (institution_id, invitation_id, question_id, kind, source, detail) "
                 "VALUES (%s,%s,%s,%s,%s,%s)", (iid, inv_id, question_id, kind, source, Jsonb(detail or {})))


def _load(conn: Connection, inv_id: UUID, lock: bool = False) -> dict:
    # NO KEY UPDATE (not UPDATE) so an event written from a separate transaction can still reference the row.
    inv = conn.execute("SELECT * FROM viva_invitations WHERE id = %s" + (" FOR NO KEY UPDATE" if lock else ""),
                       (inv_id,)).fetchone()
    return viva.expire_if_due(conn, inv)


CLOSED = {"revoked": "This link has been withdrawn.", "expired": "This link has expired.",
          "failed": "This link is not active.", "submitted": "You have already submitted your answers. Thank you."}


def _session(conn: Connection, inv: dict, nonce: str | None) -> None:
    if inv["status"] != "in_progress":
        raise HTTPException(409, CLOSED.get(inv["status"], "The viva has not been started."))
    if not viva.nonce_matches(inv["session_nonce_sha256"], nonce):
        raise HTTPException(409, "This viva was opened in another window or device. Continue there, or reload "
                                 "this page to continue here.")


# ------------------------------------------------------------------ intro and start


@router.get("/{token}", dependencies=[Depends(rate_limit)])
def intro(token: str):
    iid, inv_id = _resolve(token)
    with tenant_tx(iid) as conn:
        inv = _load(conn, inv_id, lock=True)
        if inv["status"] in ("queued", "sent"):
            conn.execute("UPDATE viva_invitations SET status = 'opened', opened_at = now() WHERE id = %s", (inv_id,))
            _event(conn, iid, inv_id, "open")
            audit.record(conn, None, "viva.opened", "viva_invitation", inv_id, institution_id=iid, actor_label="student")
            inv["status"] = "opened"
        name = conn.execute("SELECT name FROM institutions WHERE id = %s", (iid,)).fetchone()["name"]
        n = conn.execute("SELECT count(*) AS n FROM viva_questions WHERE invitation_id = %s AND parent_id IS NULL",
                         (inv_id,)).fetchone()["n"]
    return {
        "institution_name": name, "status": inv["status"], "message": CLOSED.get(inv["status"]),
        "kind": inv["viva_kind"], "answer_format": inv["answer_format"], "question_count": n,
        "prep_seconds": inv["prep_seconds"], "answer_seconds": inv["answer_seconds"],
        "deadline_at": inv["deadline_at"], "is_test": inv["is_test"], "camera_required": inv["camera_required"],
        "followups": inv["followups_enabled"],
        "transcribed": inv["answer_format"] == "spoken" and ai.enabled(),
    }


@router.post("/{token}/start", dependencies=[Depends(rate_limit)])
def start(token: str):
    """Start, or take the session over on this device. Returns the session key the page sends with each write."""
    iid, inv_id = _resolve(token)
    nonce = secrets.token_urlsafe(32)
    with tenant_tx(iid) as conn:
        inv = _load(conn, inv_id, lock=True)
        if inv["status"] in CLOSED:
            raise HTTPException(409, CLOSED[inv["status"]])
        if inv["status"] == "in_progress":
            conn.execute("UPDATE viva_invitations SET session_nonce_sha256 = %s WHERE id = %s",
                         (viva.nonce_hash(nonce), inv_id))
            _event(conn, iid, inv_id, "second_session")
        else:
            conn.execute("UPDATE viva_invitations SET status = 'in_progress', started_at = now(), "
                         "session_nonce_sha256 = %s, token_ciphertext = NULL WHERE id = %s",
                         (viva.nonce_hash(nonce), inv_id))
            _event(conn, iid, inv_id, "start")
            audit.record(conn, None, "viva.started", "viva_invitation", inv_id, institution_id=iid,
                         actor_label="student")
    return {"session": nonce}


# ------------------------------------------------------------------ questions


def _current(conn: Connection, inv_id: UUID) -> dict | None:
    """The most recently released question (main or follow-up), with its answer state."""
    return conn.execute(
        "SELECT q.id, q.position, q.parent_id, q.text_ciphertext, q.passage_ciphertext, rl.released_at, rl.due_at, "
        "a.id AS answered FROM viva_question_releases rl JOIN viva_questions q ON q.id = rl.question_id "
        "LEFT JOIN viva_responses a ON a.question_id = q.id WHERE rl.invitation_id = %s "
        "ORDER BY rl.released_at DESC, q.parent_id IS NOT NULL DESC LIMIT 1", (inv_id,)).fetchone()


def _question_out(conn: Connection, iid: UUID, inv: dict, q: dict, total: int) -> dict:
    dek = institution_dek(conn, iid)
    return {
        "done": False,
        "question": {
            "id": q["id"], "position": q["position"], "total": total,
            "followup": q.get("parent_id") is not None,
            "text": crypto.decrypt(dek, q["text_ciphertext"], f"viva_question:{q['id']}").decode(),
            "passage": crypto.decrypt(dek, q["passage_ciphertext"], f"viva_passage:{q['id']}").decode()
                       if q["passage_ciphertext"] else None,
        },
        "answer_format": inv["answer_format"],
        "prep_seconds": inv["prep_seconds"], "answer_seconds": inv["answer_seconds"],
        "released_at": q["released_at"], "due_at": q["due_at"], "server_now": viva.now(),
    }


@router.post("/{token}/next", dependencies=[Depends(rate_limit)])
def next_question(token: str, x_viva_session: str | None = Header(default=None)):
    iid, inv_id = _resolve(token)
    with tenant_tx(iid) as conn:
        inv = _load(conn, inv_id, lock=True)
        _session(conn, inv, x_viva_session)
        total = conn.execute("SELECT count(*) AS n FROM viva_questions WHERE invitation_id = %s AND parent_id IS NULL",
                             (inv_id,)).fetchone()["n"]
        cur = _current(conn, inv_id)
        if cur and not cur["answered"]:
            if viva.now() <= cur["due_at"]:
                return _question_out(conn, iid, inv, cur, total)        # resume the same question
            conn.execute("INSERT INTO viva_responses (institution_id, invitation_id, question_id, kind) "
                         "VALUES (%s,%s,%s,'skipped')", (iid, inv_id, cur["id"]))
            _event(conn, iid, inv_id, "question_timed_out", cur["id"])
        if cur:
            follow = viva.followup_state(conn, iid, inv, cur)
            if follow == "wait":
                if not conn.execute("SELECT 1 FROM viva_session_events WHERE question_id = %s AND kind = "
                                    "'followup_waiting'", (cur["id"],)).fetchone():
                    _event(conn, iid, inv_id, "followup_waiting", cur["id"])
                return {"done": False, "waiting": True, "retry_after_ms": 2000, "total": total}
            if follow is not None:
                return _release(conn, iid, inv, follow, total)
        pos = (cur["position"] if cur else 0) + 1
        q = conn.execute("SELECT id, position, parent_id, text_ciphertext, passage_ciphertext FROM viva_questions "
                         "WHERE invitation_id = %s AND position = %s AND parent_id IS NULL", (inv_id, pos)).fetchone()
        if q is None:
            return {"done": True, "total": total}
        return _release(conn, iid, inv, q, total)


def _release(conn: Connection, iid: UUID, inv: dict, q: dict, total: int) -> dict:
    rel = conn.execute(
        "INSERT INTO viva_question_releases (question_id, institution_id, invitation_id, due_at) "
        "VALUES (%s,%s,%s, now() + make_interval(secs => %s)) RETURNING released_at, due_at",
        (q["id"], iid, inv["id"], inv["prep_seconds"] + inv["answer_seconds"])).fetchone()
    _event(conn, iid, inv["id"], "question_released", q["id"])
    return _question_out(conn, iid, inv, {**q, **rel}, total)


def _check_answerable(conn: Connection, iid: UUID, inv: dict, question_id: UUID, fmt: str) -> dict:
    if inv["answer_format"] != fmt:
        raise HTTPException(422, f"this viva takes {inv['answer_format']} answers")
    cur = _current(conn, inv["id"])
    if cur is None or cur["id"] != question_id:
        raise HTTPException(409, "this question is not open")
    if cur["answered"]:
        raise HTTPException(409, "this question has already been answered")
    grace = get_settings().viva_answer_grace_seconds
    late = (viva.now() - cur["due_at"]).total_seconds()
    if late > grace:
        # Its own transaction: the request's transaction is rolled back by the error below.
        with tenant_tx(iid) as log_conn:
            _event(log_conn, iid, inv["id"], "late_write", question_id, {"seconds_late": round(late, 1)})
        raise HTTPException(409, "the time for this question is up")
    return cur


@router.put("/{token}/answers/{question_id}/audio", dependencies=[Depends(rate_limit)])
async def save_audio(token: str, question_id: UUID, request: Request,
                     x_viva_session: str | None = Header(default=None)):
    s = get_settings()
    declared = request.headers.get("content-length")
    limit = max(s.viva_max_audio_bytes, s.viva_max_video_bytes)   # the per-invitation limit is checked below
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "the recording is too large")
    ids = await run_in_threadpool(_resolve, token)     # check the token before reading the body
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise HTTPException(413, "the recording is too large")
    if not data:
        raise HTTPException(422, "the recording is empty")
    mime = viva.sniff_audio(bytes(data))
    if mime is None:
        raise HTTPException(415, "unsupported audio format")
    # Database and storage calls are blocking: run them off the event loop.
    return await run_in_threadpool(_store_audio, ids, question_id, bytes(data), mime, x_viva_session)


def _store_audio(ids: tuple[UUID, UUID], question_id: UUID, data: bytes, mime: str, x_viva_session: str | None):
    iid, inv_id = ids
    with tenant_tx(iid) as conn:
        inv = _load(conn, inv_id, lock=True)
        _session(conn, inv, x_viva_session)
        cur = _check_answerable(conn, iid, inv, question_id, "spoken")
        s = get_settings()
        if len(data) > (s.viva_max_video_bytes if inv["camera_required"] else s.viva_max_audio_bytes):
            raise HTTPException(413, "the recording is too large")
        video = inv["camera_required"] and viva.has_video_track(data, mime)
        if inv["camera_required"] and not video:
            _event(conn, iid, inv_id, "camera_unavailable", question_id)
        mime = viva.media_type(mime, video)
        dek = institution_dek(conn, iid)
        rec_id = conn.execute("SELECT gen_random_uuid() AS id").fetchone()["id"]
        key = viva.audio_key(iid, rec_id)
        get_storage().put(key, crypto.encrypt(dek, data, f"viva_audio:{rec_id}"))
        conn.execute("INSERT INTO viva_recordings (id, institution_id, invitation_id, question_id, storage_key, sha256, "
                     "mime_type, size_bytes, has_video) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     (rec_id, iid, inv_id, question_id, key, hashlib.sha256(data).hexdigest(), mime, len(data), video))
        taken = int((viva.now() - cur["released_at"]).total_seconds())
        conn.execute("INSERT INTO viva_responses (institution_id, invitation_id, question_id, kind, seconds_taken) "
                     "VALUES (%s,%s,%s,'spoken',%s)", (iid, inv_id, question_id, taken))
        _event(conn, iid, inv_id, "answer_saved", question_id, {"bytes": len(data), "seconds_taken": taken})
        if ai.enabled():
            conn.execute("INSERT INTO jobs (institution_id, kind, viva_recording_id) "
                         "VALUES (%s, 'transcribe_viva_answer', %s)", (iid, rec_id))
    return {"ok": True}


class TypedIn(BaseModel):
    text: str = Field(min_length=1)


@router.put("/{token}/answers/{question_id}/text", dependencies=[Depends(rate_limit)])
def save_text(token: str, question_id: UUID, body: TypedIn, x_viva_session: str | None = Header(default=None)):
    if len(body.text) > get_settings().viva_max_typed_chars:
        raise HTTPException(413, "the answer is too long")
    iid, inv_id = _resolve(token)
    with tenant_tx(iid) as conn:
        inv = _load(conn, inv_id, lock=True)
        _session(conn, inv, x_viva_session)
        cur = _check_answerable(conn, iid, inv, question_id, "typed")
        dek = institution_dek(conn, iid)
        rid = conn.execute("SELECT gen_random_uuid() AS id").fetchone()["id"]
        taken = int((viva.now() - cur["released_at"]).total_seconds())
        conn.execute("INSERT INTO viva_responses (id, institution_id, invitation_id, question_id, kind, "
                     "answer_ciphertext, seconds_taken) VALUES (%s,%s,%s,%s,'typed',%s,%s)",
                     (rid, iid, inv_id, question_id, crypto.encrypt(dek, body.text.encode(), f"viva_response:{rid}"),
                      taken))
        _event(conn, iid, inv_id, "answer_saved", question_id, {"chars": len(body.text), "seconds_taken": taken})
        viva.queue_followup(conn, iid, question_id)
    return {"ok": True}


# ------------------------------------------------------------------ browser signals and submit

BrowserKind = Literal["focus_lost", "focus_back", "paste_blocked", "fullscreen_exit", "mic_unavailable",
                      "camera_unavailable"]


class EventIn(BaseModel):
    kind: BrowserKind
    question_id: UUID | None = None
    detail: dict[str, int | float | bool] = Field(default_factory=dict, max_length=5)


class EventsIn(BaseModel):
    events: list[EventIn] = Field(max_length=50)


@router.post("/{token}/events", dependencies=[Depends(rate_limit)])
def events(token: str, body: EventsIn, x_viva_session: str | None = Header(default=None)):
    iid, inv_id = _resolve(token)
    with tenant_tx(iid) as conn:
        inv = _load(conn, inv_id)
        _session(conn, inv, x_viva_session)
        valid = {r["id"] for r in conn.execute("SELECT id FROM viva_questions WHERE invitation_id = %s",
                                               (inv_id,)).fetchall()}
        for e in body.events:
            _event(conn, iid, inv_id, e.kind, e.question_id if e.question_id in valid else None,
                   {k[:30]: v for k, v in e.detail.items()}, source="browser")
    return {"ok": True}


@router.post("/{token}/submit", dependencies=[Depends(rate_limit)])
def submit(token: str, x_viva_session: str | None = Header(default=None)):
    iid, inv_id = _resolve(token)
    with tenant_tx(iid) as conn:
        inv = _load(conn, inv_id, lock=True)
        _session(conn, inv, x_viva_session)
        for q in conn.execute(
                "SELECT rl.question_id FROM viva_question_releases rl LEFT JOIN viva_responses a "
                "ON a.question_id = rl.question_id WHERE rl.invitation_id = %s AND a.id IS NULL AND rl.due_at <= now()",
                (inv_id,)).fetchall():
            conn.execute("INSERT INTO viva_responses (institution_id, invitation_id, question_id, kind) "
                         "VALUES (%s,%s,%s,'skipped')", (iid, inv_id, q["question_id"]))
            _event(conn, iid, inv_id, "question_timed_out", q["question_id"])
        open_q = conn.execute(
            "SELECT count(*) AS n FROM viva_questions q LEFT JOIN viva_responses a ON a.question_id = q.id "
            "LEFT JOIN viva_question_releases rl ON rl.question_id = q.id WHERE q.invitation_id = %s "
            "AND a.id IS NULL AND (rl.question_id IS NULL OR rl.due_at > now())", (inv_id,)).fetchone()["n"]
        if open_q:
            raise HTTPException(409, "there are questions left to answer")
        conn.execute("UPDATE viva_invitations SET status = 'submitted', submitted_at = now(), "
                     "session_nonce_sha256 = NULL WHERE id = %s", (inv_id,))
        _event(conn, iid, inv_id, "submit")
        audit.record(conn, None, "viva.submitted", "viva_invitation", inv_id, institution_id=iid,
                     actor_label="student")
    return {"ok": True}
