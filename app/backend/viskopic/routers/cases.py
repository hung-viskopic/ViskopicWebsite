"""Cases as a reviewer works through them: the case list behind the home page, and the texts still to check.

Read-only views over existing tables. They return ids, statuses, counts and pseudonyms only: no essay text, and
nothing the platform operator can reach (tenant connections only, RLS applies as everywhere else).
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection

from .. import access
from ..analysis import consistency, service
from ..auth import Principal, require_user
from ..deps import tenant_conn

router = APIRouter(tags=["cases"])

# What the reviewer should do next, in the order the home page shows them.
NEXT_STEPS = (
    "review_evidence",    # a check flagged: a person decides whether a viva is needed
    "check_texts",        # the writing style check waits for quality reviews
    "rate_viva",          # the student answered; ratings are missing
    "viva_problem",       # the invitation email failed or the link expired unused
    "viva_waiting",       # sent, opened or in progress
    "await_baseline",     # not enough past essays for the writing style check
    "analysing",
    "failed",
    "viva_rated",
    "no_flag",
    "withdrawn",
)


def _next_step(case: dict, combined: dict | None, viva: dict | None) -> str:
    status = case["status"]
    if status == "withdrawn":
        return "withdrawn"
    if status == "analysing":
        return "analysing"
    if status == "failed":
        return "failed"
    if viva:
        if viva["status"] == "submitted":
            if not viva["overall_rated"]:
                return "rate_viva"
        elif viva["status"] in ("queued", "sent", "opened", "in_progress"):
            return "viva_waiting"
        elif viva["status"] in ("failed", "expired"):
            return "viva_problem"
    if combined and combined["outcome"] == "review_recommended" and not (viva and viva["status"] == "submitted"):
        return "review_evidence"
    if status == "awaiting_review":
        return "check_texts"
    if status == "awaiting_baseline":
        return "await_baseline"
    if viva and viva["status"] == "submitted":
        return "viva_rated"
    return "no_flag"


def texts_for_case(conn: Connection, institution_id: UUID, case: dict) -> list[dict]:
    """The submission and the student's usable past essays, each with its current quality review state."""
    docs = conn.execute(
        "SELECT d.id, d.role, d.provenance, d.original_filename, d.uploaded_at, t.word_count "
        "FROM documents d JOIN document_texts t ON t.document_id = d.id "
        "WHERE d.status = 'completed' AND ((d.id = %s) OR (d.student_id = %s AND d.role = 'baseline' "
        "AND t.word_count >= %s)) ORDER BY (d.role = 'submission') DESC, d.uploaded_at, d.id",
        (case["document_id"], case["student_id"], consistency.MIN_WORDS),
    ).fetchall()
    ids = [d["id"] for d in docs]
    reviews, digests = service.latest_reviews(conn, ids), service.text_digests(conn, institution_id, ids)
    for d in docs:
        r = reviews.get(d["id"])
        current = r is not None and r["text_sha256"] == digests.get(d["id"])
        d["review"] = None if r is None else {
            "language": r["language"], "extraction": r["extraction"], "reviewed_at": r["reviewed_at"],
            "reviewed_by_email": r["reviewed_by_email"], "current": current}
        d["needs_check"] = not current
    return docs


@router.get("/cases")
def list_cases(course_id: UUID | None = None, p: Principal = Depends(require_user),
               conn: Connection = Depends(tenant_conn)):
    ids = access.accessible_course_ids(conn, p)
    if course_id is not None:
        access.require_course(conn, p, course_id)
    flt = None if ids is None else list(ids)
    cases = conn.execute(
        "SELECT k.id, k.status, k.created_at, k.updated_at, k.document_id, k.student_id, k.course_id, "
        "d.original_filename, d.uploaded_at, d.provenance, t.word_count, a.title AS assignment_title, "
        "s.pseudonym AS student_pseudonym, c.code AS course_code, c.title AS course_title "
        "FROM cases k JOIN documents d ON d.id = k.document_id JOIN students s ON s.id = k.student_id "
        "JOIN courses c ON c.id = k.course_id LEFT JOIN assignments a ON a.id = d.assignment_id "
        "LEFT JOIN document_texts t ON t.document_id = d.id "
        "WHERE (%s::uuid[] IS NULL OR k.course_id = ANY(%s)) AND (%s::uuid IS NULL OR k.course_id = %s) "
        "ORDER BY k.updated_at DESC LIMIT 500",
        (flt, flt, course_id, course_id),
    ).fetchall()
    case_ids = [c["id"] for c in cases]
    if not case_ids:
        return {"cases": [], "counts": {}}

    runs: dict = {}
    for r in conn.execute(
        "SELECT DISTINCT ON (r.case_id, r.pipeline) r.case_id, r.pipeline, r.status, r.outcome, r.confidence, "
        "r.details->>'website_status' AS website_status, r.details->'flagged_by' AS flagged_by, "
        "bp.reference_count, bp.supervised_count, bp.source AS profile_source "
        "FROM analysis_runs r LEFT JOIN baseline_profiles bp ON bp.id = r.profile_id "
        "WHERE r.case_id = ANY(%s) AND r.pipeline IN ('consistency', 'interchangeability', 'combined') "
        "ORDER BY r.case_id, r.pipeline, r.created_at DESC", (case_ids,)).fetchall():
        runs.setdefault(r["case_id"], {})[r["pipeline"]] = r

    vivas = {r["case_id"]: r for r in conn.execute(
        "SELECT DISTINCT ON (i.case_id) i.case_id, i.id, i.status, i.deadline_at, i.created_at, i.opened_at, "
        "i.started_at, i.submitted_at FROM viva_invitations i WHERE i.case_id = ANY(%s) "
        "ORDER BY i.case_id, (i.status = 'revoked'), i.created_at DESC", (case_ids,)).fetchall()}
    inv_ids = [v["id"] for v in vivas.values()]
    progress = {r["invitation_id"]: r for r in conn.execute(
        "SELECT q.invitation_id, count(*) AS questions, "
        "count(*) FILTER (WHERE EXISTS (SELECT 1 FROM viva_assessments a WHERE a.question_id = q.id)) AS rated, "
        "EXISTS (SELECT 1 FROM viva_assessments a WHERE a.invitation_id = q.invitation_id AND a.question_id IS NULL) "
        "AS overall_rated "
        "FROM viva_questions q WHERE q.invitation_id = ANY(%s) GROUP BY q.invitation_id", (inv_ids,)).fetchall()
    } if inv_ids else {}

    out, counts = [], {}
    for c in cases:
        r = runs.get(c["id"], {})
        v = vivas.get(c["id"])
        if v:
            pr = progress.get(v["id"], {})
            v = {**v, "questions": pr.get("questions", 0), "rated": pr.get("rated", 0),
                 "overall_rated": bool(pr.get("overall_rated", False))}
            v.pop("case_id")
        delta, p2, comb = r.get("consistency"), r.get("interchangeability"), r.get("combined")
        c["consistency"] = None if delta is None else {
            "status": delta["status"], "outcome": delta["outcome"], "confidence": delta["confidence"],
            "website_status": delta["website_status"], "reference_count": delta["reference_count"],
            "supervised_count": delta["supervised_count"], "demonstration": delta["profile_source"] == "demonstration"}
        c["ai_comparison"] = None if p2 is None else {"status": p2["status"], "outcome": p2["outcome"]}
        c["combined"] = None if comb is None else {"status": comb["outcome"], "flagged_by": comb["flagged_by"] or []}
        c["viva"] = v
        c["next_step"] = _next_step(c, comb, v)
        c["texts_to_check"] = (sum(1 for d in texts_for_case(conn, p.institution_id, c) if d["needs_check"])
                               if c["status"] == "awaiting_review" else 0)
        counts[c["next_step"]] = counts.get(c["next_step"], 0) + 1
        out.append(c)
    out.sort(key=lambda c: NEXT_STEPS.index(c["next_step"]))
    return {"cases": out, "counts": counts}


@router.get("/cases/{case_id}/texts")
def case_texts(case_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    """The texts a reviewer checks before the writing style check can run for this case, in checking order."""
    case = conn.execute(
        "SELECT k.id, k.status, k.document_id, k.student_id, k.course_id, s.pseudonym AS student_pseudonym, "
        "d.original_filename, a.title AS assignment_title FROM cases k JOIN students s ON s.id = k.student_id "
        "JOIN documents d ON d.id = k.document_id LEFT JOIN assignments a ON a.id = d.assignment_id "
        "WHERE k.id = %s", (case_id,)).fetchone()
    if case is None or access.course_access(conn, p, case["course_id"]) is None:
        raise HTTPException(404, "case not found")
    docs = texts_for_case(conn, p.institution_id, case)
    return {"case": case, "can_manage": access.course_access(conn, p, case["course_id"]) == access.MANAGE,
            "documents": docs, "remaining": sum(1 for d in docs if d["needs_check"])}
