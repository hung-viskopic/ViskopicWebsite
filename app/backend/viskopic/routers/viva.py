"""Viva, staff side: send invitations (Visq 2, or the reviewer's own questions), read the report, record assessments."""
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import Connection
from pydantic import BaseModel, EmailStr, Field

from .. import access, audit, crypto
from ..access import MANAGE
from ..auth import Principal, require_user
from ..deps import institution_dek, tenant_conn
from ..storage import get_storage
from ..viva import ai, visq
from ..viva import service as viva

router = APIRouter(tags=["viva"])

Kind = Literal["style", "reasoning"]
Format = Literal["spoken", "typed"]


def _case(conn: Connection, p: Principal, case_id: UUID, need: str = access.VIEW) -> dict:
    case = conn.execute("SELECT id, document_id, student_id, course_id, status FROM cases WHERE id = %s",
                        (case_id,)).fetchone()
    if case is None or access.course_access(conn, p, case["course_id"]) is None:
        raise HTTPException(404, "case not found")
    if need == MANAGE:
        access.require_course(conn, p, case["course_id"], MANAGE)
    return case


def _invitation(conn: Connection, p: Principal, invitation_id: UUID, need: str = access.VIEW) -> dict:
    inv = conn.execute("SELECT * FROM viva_invitations WHERE id = %s", (invitation_id,)).fetchone()
    if inv is None:
        raise HTTPException(404, "viva not found")
    case = _case(conn, p, inv["case_id"], need)
    return viva.expire_if_due(conn, {**inv, "course_id": case["course_id"], "document_id": case["document_id"]})


def _submission_text(conn: Connection, institution_id: UUID, document_id: UUID) -> str:
    row = conn.execute("SELECT text_enc FROM document_texts WHERE document_id = %s", (document_id,)).fetchone()
    if row is None:
        raise HTTPException(409, "the submission has no extracted text")
    dek = institution_dek(conn, institution_id)
    return crypto.decrypt(dek, row["text_enc"], f"document_text:{document_id}").decode()


INVITATION_COLS = ("id, viva_kind, answer_format, supervision_mode, camera_required, status, deadline_at, prep_seconds, answer_seconds, "
                   "is_test, last_error, created_at, sent_at, opened_at, started_at, submitted_at, revoked_at, "
                   "revoke_reason, replaces_id, question_engine, flow, visq2_full, extension_seconds, "
                   "fullscreen_required")


# ------------------------------------------------------------------ list and draft


@router.get("/documents/{document_id}/vivas")
def list_vivas(document_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    doc = conn.execute("SELECT course_id, role FROM documents WHERE id = %s", (document_id,)).fetchone()
    if doc is None or access.course_access(conn, p, doc["course_id"]) is None:
        raise HTTPException(404, "document not found")
    case = conn.execute("SELECT id, status FROM cases WHERE document_id = %s", (document_id,)).fetchone()
    invitations = []
    if case:
        rows = conn.execute(f"SELECT {INVITATION_COLS} FROM viva_invitations WHERE case_id = %s ORDER BY created_at DESC",
                            (case["id"],)).fetchall()
        invitations = [viva.expire_if_due(conn, r) for r in rows]
        counts = {r["invitation_id"]: r for r in conn.execute(
            "SELECT q.invitation_id, count(*) AS questions, count(a.id) AS answered FROM viva_questions q "
            "LEFT JOIN viva_responses a ON a.question_id = q.id AND a.kind <> 'skipped' "
            "WHERE q.invitation_id = ANY(%s) AND q.parent_id IS NULL GROUP BY q.invitation_id",
            ([r["id"] for r in rows],)).fetchall()}
        for r in invitations:
            c = counts.get(r["id"], {})
            r["questions"], r["answered"] = c.get("questions", 0), c.get("answered", 0)
    return {
        "case": case,
        "can_manage": access.course_access(conn, p, doc["course_id"]) == MANAGE,
        "ai_enabled": ai.enabled(),              # transcription of spoken answers
        "visq_enabled": visq.enabled(),          # Visq 2 world-tree vivas
        "invitations": invitations,
    }


class DraftIn(BaseModel):
    kind: Kind
    count: int = Field(default=4, ge=1, le=8)


@router.post("/cases/{case_id}/viva-drafts")
def draft(case_id: UUID, body: DraftIn, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    """Template questions for a viva with the reviewer's own questions. Visq 2 writes its own during the viva."""
    _case(conn, p, case_id, MANAGE)
    return {"provider": "templates", "questions": [q.__dict__ for q in ai.template_questions(body.kind, body.count)]}


# ------------------------------------------------------------------ Visq 2 preview


class PreviewIn(BaseModel):
    visq2_full: bool = False
    extension_minutes: int = Field(default=6, ge=1, le=30)


def _preview_out(row: dict, shown: dict | None) -> dict:
    return {"id": row["id"], "status": row["status"], "error": row["error"], "engine": row["engine"],
            "visq2_full": row["visq2_full"], "extension_minutes": row["extension_seconds"] // 60,
            "created_at": row["created_at"], "updated_at": row["updated_at"], "draft": shown}


@router.post("/cases/{case_id}/visq2-previews", status_code=201)
def create_preview(case_id: UUID, body: PreviewIn, p: Principal = Depends(require_user),
                   conn: Connection = Depends(tenant_conn)):
    """Visq reads the essay now, so the reviewer can see its questions before sending the invitation."""
    case = _case(conn, p, case_id, MANAGE)
    if case["status"] == "withdrawn":
        raise HTTPException(409, "the case was withdrawn because its document was removed")
    if not visq.enabled():
        raise HTTPException(422, "Visq is switched off (VISKOPIC_ALLOW_EXTERNAL_AI and a model key)")
    row = conn.execute(
        "INSERT INTO viva_visq2_previews (institution_id, case_id, created_by, visq2_full, extension_seconds) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING *",
        (p.institution_id, case_id, p.user_id, body.visq2_full, body.extension_minutes * 60)).fetchone()
    conn.execute("INSERT INTO jobs (institution_id, kind, visq2_preview_id) VALUES (%s, 'visq2_preview', %s)",
                 (p.institution_id, row["id"]))
    audit.record(conn, p, "viva.visq2_preview_requested", "viva_visq2_preview", row["id"],
                 {"case_id": str(case_id), "full": body.visq2_full, "extension_minutes": body.extension_minutes})
    return _preview_out(row, None)


@router.get("/visq2-previews/{preview_id}")
def get_preview(preview_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    dek = institution_dek(conn, p.institution_id)
    row, shown = viva.load_preview(conn, dek, preview_id)
    if row is None:
        raise HTTPException(404, "preview not found")
    _case(conn, p, row["case_id"], MANAGE)
    return _preview_out(row, shown)


# ------------------------------------------------------------------ invitations


class QuestionIn(BaseModel):
    text: str = Field(min_length=10, max_length=ai.MAX_QUESTION_CHARS)
    passage: str | None = Field(default=None, max_length=ai.MAX_PASSAGE_CHARS)
    source: Literal["template", "reviewer"] = "reviewer"


class InvitationIn(BaseModel):
    kind: Kind
    answer_format: Format = "spoken"
    camera: bool = False          # record the webcam with each spoken answer (watched by a person, never analysed)
    email: EmailStr
    deadline_days: int = Field(default=7, ge=1, le=30)
    prep_seconds: int = Field(default=30, ge=0, le=300)
    answer_seconds: int = Field(default=120, ge=30, le=600)
    # "visq2": Visq writes the questions during the viva (clarifying questions, a timed extension, a short viva).
    # "standard": the reviewer's own (or template) questions, approved before sending.
    flow: Literal["standard", "visq2"] = "standard"
    questions: list[QuestionIn] = Field(default_factory=list, max_length=8)
    visq2_full: bool = False                                    # 7 viva questions instead of 3
    # Laptop/desktop browsers only; leaving full screen covers the question until the student returns.
    # Switch off for students whose assistive tools don't work in full screen.
    fullscreen: bool = True
    extension_minutes: int = Field(default=6, ge=1, le=30)      # time for Visq 2's typed extension
    # A ready Visq 2 preview for this case: the student gets the clarifying questions the reviewer saw.
    visq2_preview_id: UUID | None = None


@router.post("/cases/{case_id}/viva-invitations", status_code=201)
def create_invitation(case_id: UUID, body: InvitationIn, p: Principal = Depends(require_user),
                      conn: Connection = Depends(tenant_conn)):
    case = _case(conn, p, case_id, MANAGE)
    if case["status"] == "withdrawn":
        raise HTTPException(409, "the case was withdrawn because its document was removed")
    if body.camera and body.answer_format != "spoken":
        raise HTTPException(422, "the camera can only be used with spoken answers")
    if body.flow == "visq2":
        if not visq.enabled():
            raise HTTPException(422, "Visq is switched off (VISKOPIC_ALLOW_EXTERNAL_AI and a model key)")
        if body.answer_format == "spoken" and not ai.enabled():
            raise HTTPException(422, "spoken Visq 2 answers need transcription, which is switched off")
        if body.questions:
            raise HTTPException(422, "Visq 2 writes its own questions during the viva")
    elif not body.questions:
        raise HTTPException(422, "add at least one question")
    text = None
    questions = []
    for q in body.questions:
        item = {"text": q.text.strip(), "source": q.source}
        if q.passage and q.passage.strip():
            text = text if text is not None else _submission_text(conn, p.institution_id, case["document_id"])
            span = ai.locate(text, q.passage)
            if span is None:
                raise HTTPException(422, f"this passage is not in the submission: \"{q.passage[:60]}…\"")
            item.update(passage=text[span[0]:span[1]], passage_start=span[0], passage_end=span[1])
        questions.append(item)
    engine = visq.engine_label() if body.flow == "visq2" else "reviewer"
    prepared = None
    if body.flow == "visq2" and body.visq2_preview_id is not None:
        dek = institution_dek(conn, p.institution_id)
        try:
            prepared, engine = viva.take_preview(conn, dek, body.visq2_preview_id, case_id, body.visq2_full,
                                                 body.extension_minutes * 60)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
    out = _issue(conn, p, case_id, body.kind, body.answer_format, str(body.email), body.deadline_days,
                 body.prep_seconds, body.answer_seconds, questions, replaces=None, camera=body.camera,
                 engine=engine, flow=body.flow, visq2_full=body.visq2_full,
                 extension_seconds=body.extension_minutes * 60 if body.flow == "visq2" else None,
                 fullscreen=body.fullscreen, visq2_state=prepared)
    if prepared is not None:
        viva.mark_preview_used(conn, body.visq2_preview_id, out["id"])
    audit.record(conn, p, "viva.invitation_created", "viva_invitation", out["id"],
                 {"case_id": str(case_id), "kind": body.kind, "format": body.answer_format, "camera": body.camera,
                  "questions": len(questions), "sources": sorted({q["source"] for q in questions}),
                  "flow": body.flow, "engine": engine, "fullscreen": body.fullscreen,
                  "visq2_preview_id": str(body.visq2_preview_id) if prepared is not None else None})
    return out


def _issue(conn: Connection, p: Principal, case_id: UUID, kind: str, fmt: str, email: str, deadline_days: int,
           prep: int, answer: int, questions: list[dict], replaces: UUID | None, camera: bool = False,
           engine: str | None = None, flow: str = "standard", visq2_full: bool = False,
           extension_seconds: int | None = None, fullscreen: bool = False,
           visq2_state: dict | None = None) -> dict:
    dek = institution_dek(conn, p.institution_id)
    token = viva.new_token()
    inv_id = conn.execute("SELECT gen_random_uuid() AS id").fetchone()["id"]
    conn.execute(
        "INSERT INTO viva_invitations (id, institution_id, case_id, viva_kind, answer_format, supervision_mode, "
        "camera_required, token_sha256, token_ciphertext, email_ciphertext, deadline_at, prep_seconds, answer_seconds, "
        "created_by, replaces_id, question_engine, flow, visq2_full, extension_seconds, fullscreen_required) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (inv_id, p.institution_id, case_id, kind, fmt,
         "restricted" if fmt == "typed" else "oral_video" if camera else "oral_recorded", camera,
         viva.token_hash(token), crypto.encrypt(dek, token.encode(), f"viva_token:{inv_id}"),
         crypto.encrypt(dek, email.encode(), f"viva_invitation_email:{inv_id}"),
         viva.now() + timedelta(days=deadline_days), prep, answer, p.user_id, replaces, engine, flow, visq2_full,
         extension_seconds, fullscreen))
    if flow == "visq2":
        # A prepared state (from the reviewer's preview) is used as it is; otherwise Visq reads the essay in the
        # worker and the student page waits ("one moment") if they open it before then.
        viva.start_visq2_state(conn, dek, p.institution_id, inv_id, visq2_state, engine)
    else:
        viva.create_questions(conn, dek, p.institution_id, inv_id, questions)
    viva.queue_email(conn, p.institution_id, inv_id)
    # Test vivas only: the reviewer may open the link directly instead of waiting for the email.
    return {"id": inv_id, "status": "queued", "test_link": viva.viva_link(token)}


class RevokeIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/viva-invitations/{invitation_id}/revoke")
def revoke(invitation_id: UUID, body: RevokeIn, p: Principal = Depends(require_user),
           conn: Connection = Depends(tenant_conn)):
    _invitation(conn, p, invitation_id, MANAGE)
    if not viva.revoke(conn, invitation_id, body.reason, p.user_id):
        raise HTTPException(409, "only an open invitation can be revoked")
    audit.record(conn, p, "viva.invitation_revoked", "viva_invitation", invitation_id, {"reason": body.reason})
    return {"ok": True}


@router.post("/viva-invitations/{invitation_id}/resend", status_code=201)
def resend(invitation_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    """Revoke the old link and issue a new one with the same questions (e.g. a lost or failed email)."""
    inv = _invitation(conn, p, invitation_id, MANAGE)
    if inv["status"] in ("in_progress", "submitted"):
        raise HTTPException(409, "the student has already started this viva")
    dek = institution_dek(conn, p.institution_id)
    email = crypto.decrypt(dek, inv["email_ciphertext"], f"viva_invitation_email:{invitation_id}").decode()
    questions = []
    for q in conn.execute("SELECT * FROM viva_questions WHERE invitation_id = %s AND parent_id IS NULL "
                          "AND source <> 'visq2' ORDER BY position", (invitation_id,)).fetchall():
        item = {"text": crypto.decrypt(dek, q["text_ciphertext"], f"viva_question:{q['id']}").decode(),
                "source": q["source"], "passage_start": q["passage_start"], "passage_end": q["passage_end"]}
        if q["passage_ciphertext"]:
            item["passage"] = crypto.decrypt(dek, q["passage_ciphertext"], f"viva_passage:{q['id']}").decode()
        questions.append(item)
    # A Visq 2 viva nobody has started keeps its prepared questions (the ones the reviewer may have previewed).
    prepared = None
    if inv["flow"] == "visq2":
        row, state = viva.load_visq2(conn, dek, invitation_id)
        if row is not None and row["status"] == "ready" and state is not None and not state.get("answers") \
                and not state.get("clar_answers") and state.get("ci", 0) == 0:
            prepared = state
    viva.revoke(conn, invitation_id, "replaced by a new link", p.user_id)
    remaining = max(1, (inv["deadline_at"] - viva.now()).days + 1) if inv["status"] != "expired" else 7
    out = _issue(conn, p, inv["case_id"], inv["viva_kind"], inv["answer_format"], email, min(remaining, 30),
                 inv["prep_seconds"], inv["answer_seconds"], questions, replaces=invitation_id,
                 camera=inv["camera_required"], engine=inv["question_engine"], flow=inv["flow"],
                 visq2_full=inv["visq2_full"], extension_seconds=inv["extension_seconds"],
                 fullscreen=inv["fullscreen_required"], visq2_state=prepared)
    audit.record(conn, p, "viva.invitation_resent", "viva_invitation", out["id"], {"replaces": str(invitation_id)})
    return out


# ------------------------------------------------------------------ report


@router.get("/viva-invitations/{invitation_id}/report")
def report(invitation_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    inv = _invitation(conn, p, invitation_id)
    dek = institution_dek(conn, p.institution_id)
    dec = lambda blob, aad: crypto.decrypt(dek, blob, aad).decode() if blob else None  # noqa: E731
    qs = conn.execute(
        "SELECT q.id, q.position, q.parent_id, q.qtype, q.reviewer_note_ciphertext, "
        "q.text_ciphertext, q.passage_ciphertext, q.passage_start, q.passage_end, q.source, "
        "rl.released_at, rl.due_at, a.kind AS answer_kind, a.answer_ciphertext, a.id AS response_id, a.seconds_taken, "
        "a.saved_at, r.id AS recording_id, r.status AS recording_status, r.size_bytes, r.has_video, r.mime_type "
        "FROM viva_questions q LEFT JOIN viva_question_releases rl ON rl.question_id = q.id "
        "LEFT JOIN viva_responses a ON a.question_id = q.id LEFT JOIN viva_recordings r ON r.question_id = q.id "
        "WHERE q.invitation_id = %s ORDER BY q.position, q.parent_id IS NOT NULL", (invitation_id,)).fetchall()
    decisions = {d["question_id"]: d for d in conn.execute(        # Visq 1 vivas sent before Visq 2 replaced it
        "SELECT id, question_id, action, reason, why_ciphertext, engine, created_at FROM viva_followup_decisions "
        "WHERE invitation_id = %s", (invitation_id,)).fetchall()}
    rec_ids = [q["recording_id"] for q in qs if q["recording_id"]]
    transcripts = {t["recording_id"]: t for t in conn.execute(
        "SELECT DISTINCT ON (recording_id) id, recording_id, text_ciphertext, timeline, provider, model, created_at "
        "FROM viva_transcripts WHERE recording_id = ANY(%s) ORDER BY recording_id, created_at DESC",
        (rec_ids,)).fetchall()} if rec_ids else {}
    pending = {j["viva_recording_id"]: j["status"] for j in conn.execute(
        "SELECT DISTINCT ON (viva_recording_id) viva_recording_id, status FROM jobs WHERE viva_recording_id = ANY(%s) "
        "ORDER BY viva_recording_id, id DESC", (rec_ids,)).fetchall()} if rec_ids else {}
    events = conn.execute("SELECT question_id, kind, source, at, detail FROM viva_session_events "
                          "WHERE invitation_id = %s ORDER BY at, id", (invitation_id,)).fetchall()
    assessments = conn.execute(
        "SELECT DISTINCT ON (coalesce(a.question_id::text, 'overall')) a.id, a.question_id, a.rating, "
        "a.note_ciphertext, a.created_at, u.email AS created_by_email FROM viva_assessments a "
        "JOIN users u ON u.id = a.created_by WHERE a.invitation_id = %s "
        "ORDER BY coalesce(a.question_id::text, 'overall'), a.created_at DESC", (invitation_id,)).fetchall()
    for a in assessments:
        a["note"] = dec(a.pop("note_ciphertext"), f"viva_assessment:{a['id']}")
    by_q = {a["question_id"]: a for a in assessments}

    questions = []
    for q in qs:
        t = transcripts.get(q["recording_id"])
        q_events = [e for e in events if e["question_id"] == q["id"]]
        questions.append({
            "id": q["id"], "position": q["position"], "source": q["source"], "qtype": q["qtype"],
            "parent_id": q["parent_id"],
            "reviewer_note": dec(q["reviewer_note_ciphertext"], f"viva_question_note:{q['id']}"),
            "followup_decision": _decision_out(decisions.get(q["id"]), dec),
            "text": dec(q["text_ciphertext"], f"viva_question:{q['id']}"),
            "passage": dec(q["passage_ciphertext"], f"viva_passage:{q['id']}"),
            "passage_start": q["passage_start"], "passage_end": q["passage_end"],
            "released_at": q["released_at"], "due_at": q["due_at"],
            "answer_kind": q["answer_kind"], "seconds_taken": q["seconds_taken"], "saved_at": q["saved_at"],
            "typed_answer": dec(q["answer_ciphertext"], f"viva_response:{q['response_id']}") if q["response_id"] else None,
            "recording": {"id": q["recording_id"], "status": q["recording_status"], "size_bytes": q["size_bytes"],
                          "has_video": q["has_video"], "mime_type": q["mime_type"]}
                         if q["recording_id"] else None,
            "transcript": {"text": dec(t["text_ciphertext"], f"viva_transcript:{t['id']}"), "timeline": t["timeline"],
                           "provider": t["provider"], "model": t["model"]} if t else None,
            "transcript_job": pending.get(q["recording_id"]),
            "signals": _summarise(q_events),
            "assessment": by_q.get(q["id"]),
        })
    audit.record(conn, p, "viva.report_viewed", "viva_invitation", invitation_id)
    return {
        "invitation": {k: inv[k] for k in INVITATION_COLS.replace(" ", "").split(",")} | {"case_id": inv["case_id"],
                                                                                         "document_id": inv["document_id"]},
        "can_manage": access.course_access(conn, p, inv["course_id"]) == MANAGE,
        "questions": questions,
        "session": _summarise([e for e in events if e["question_id"] is None]) | {
            "second_sessions": sum(1 for e in events if e["kind"] == "second_session")},
        "events": [{k: e[k] for k in ("question_id", "kind", "source", "at")} for e in events],
        "overall": by_q.get(None),
        "visq2": _visq2_report(conn, dek, p, inv) if inv["flow"] == "visq2" else None,
    }


def _previewed(conn: Connection, inv: dict) -> bool:
    """Whether the reviewer saw these questions before sending (this invitation, or the link it replaced)."""
    row = conn.execute(
        "WITH RECURSIVE chain(id, replaces_id, depth) AS ("
        " SELECT id, replaces_id, 0 FROM viva_invitations WHERE id = %s"
        " UNION ALL SELECT v.id, v.replaces_id, c.depth + 1 FROM viva_invitations v JOIN chain c ON v.id = c.replaces_id"
        " WHERE c.depth < 20) "
        "SELECT 1 FROM viva_visq2_previews WHERE invitation_id IN (SELECT id FROM chain) LIMIT 1", (inv["id"],)).fetchone()
    return row is not None


def _visq2_report(conn: Connection, dek: bytes, p: Principal, inv: dict) -> dict:
    """Visq's preparation status and its reviewer note (a reading aid comparing wording; never a score)."""
    row, state = viva.load_visq2(conn, dek, inv["id"])
    out = {"status": row["status"] if row else None, "error": row["error"] if row else None,
           "engine": row["engine"] if row else None, "full": inv["visq2_full"],
           "extension_seconds": inv["extension_seconds"], "note": None,
           "previewed": _previewed(conn, inv)}
    if state is not None and inv["status"] == "submitted":
        essay = _submission_text(conn, p.institution_id, inv["document_id"])
        try:
            out["note"] = visq.note(state, essay)
        except Exception as e:  # the note is a reading aid; the answers above are the record
            out["note_error"] = f"{type(e).__name__}"
    return out


def _decision_out(d: dict | None, dec) -> dict | None:
    if d is None:
        return None
    return {"action": d["action"], "reason": d["reason"], "engine": d["engine"], "created_at": d["created_at"],
            "why": dec(d["why_ciphertext"], f"viva_followup_why:{d['id']}")}


def _summarise(events: list[dict]) -> dict:
    """Neutral counts for the reviewer. Browser-reported events are self-reported and can be suppressed."""
    out = {"focus_lost": 0, "away_seconds": 0.0, "paste_blocked": 0, "copy_blocked": 0, "fullscreen_exit": 0,
           "fullscreen_out_seconds": 0.0, "late_write": 0, "timed_out": False, "mic_unavailable": False,
           "camera_unavailable": False}
    lost_at = None
    for e in events:
        k = e["kind"]
        if k == "focus_lost":
            out["focus_lost"] += 1
            lost_at = e["at"]
        elif k == "focus_back" and lost_at is not None:
            out["away_seconds"] += (e["at"] - lost_at).total_seconds()
            lost_at = None
        elif k in ("paste_blocked", "copy_blocked", "fullscreen_exit", "late_write"):
            out[k] += 1
        elif k == "fullscreen_back":   # the page reports how long it was out of full screen
            out["fullscreen_out_seconds"] += float((e.get("detail") or {}).get("seconds", 0))
        elif k == "question_timed_out":
            out["timed_out"] = True
        elif k in ("mic_unavailable", "camera_unavailable"):
            out[k] = True
    out["away_seconds"] = round(out["away_seconds"], 1)
    out["fullscreen_out_seconds"] = round(out["fullscreen_out_seconds"], 1)
    return out


@router.get("/viva-recordings/{recording_id}/audio")
def audio(recording_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    rec = conn.execute("SELECT id, invitation_id, storage_key, mime_type, status FROM viva_recordings WHERE id = %s",
                       (recording_id,)).fetchone()
    if rec is None:
        raise HTTPException(404, "recording not found")
    _invitation(conn, p, rec["invitation_id"])
    if rec["status"] != "stored":
        raise HTTPException(410, "the recording was deleted")
    dek = institution_dek(conn, p.institution_id)
    data = crypto.decrypt(dek, get_storage().get(rec["storage_key"]), f"viva_audio:{recording_id}")
    audit.record(conn, p, "viva.audio_played", "viva_recording", recording_id)
    return Response(content=data, media_type=rec["mime_type"],
                    headers={"Cache-Control": "no-store", "Content-Disposition": "inline"})


class AssessmentIn(BaseModel):
    question_id: UUID | None = None
    rating: Literal["consistent", "unclear", "concern"]
    note: str | None = Field(default=None, max_length=4000)


@router.post("/viva-invitations/{invitation_id}/assessments", status_code=201)
def assess(invitation_id: UUID, body: AssessmentIn, p: Principal = Depends(require_user),
           conn: Connection = Depends(tenant_conn)):
    inv = _invitation(conn, p, invitation_id, MANAGE)
    if inv["status"] != "submitted":
        raise HTTPException(409, "the viva has not been submitted yet")
    if body.question_id and not conn.execute("SELECT 1 FROM viva_questions WHERE id = %s AND invitation_id = %s",
                                             (body.question_id, invitation_id)).fetchone():
        raise HTTPException(422, "question not in this viva")
    dek = institution_dek(conn, p.institution_id)
    aid = conn.execute("SELECT gen_random_uuid() AS id").fetchone()["id"]
    note = body.note.strip() if body.note else None
    conn.execute(
        "INSERT INTO viva_assessments (id, institution_id, invitation_id, question_id, rating, note_ciphertext, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (aid, p.institution_id, invitation_id, body.question_id, body.rating,
         crypto.encrypt(dek, note.encode(), f"viva_assessment:{aid}") if note else None, p.user_id))
    audit.record(conn, p, "viva.assessment_recorded", "viva_invitation", invitation_id,
                 {"question_id": str(body.question_id) if body.question_id else None, "rating": body.rating})
    return {"id": aid}
