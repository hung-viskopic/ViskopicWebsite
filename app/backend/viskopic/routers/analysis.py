"""Analysis: baseline profiles, cases and the evidence report."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection
from pydantic import BaseModel, Field

from .. import access, audit
from ..access import MANAGE
from ..analysis import consistency, service
from ..auth import Principal, require_user
from ..config import get_settings
from ..deps import tenant_conn
from .academic import _visible_student

router = APIRouter(tags=["analysis"])

PIPELINE_ORDER = ["consistency", "language", "authenticity", "document_signals"]
PIPELINE_LABEL = {
    "consistency": "Δ Consistency (writing style)",
    "language": "β Language (regeneration)",
    "authenticity": "α Authenticity (semantic)",
    "document_signals": "Document signals",
}


def _demo_profiles_usable() -> bool:
    """Demonstration profiles can't be quality-reviewed, so they only score with unreviewed scoring on."""
    s = get_settings()
    return s.allow_demo_profiles and s.allow_unreviewed_consistency


def _require_student(conn: Connection, p: Principal, student_id: UUID, need: str = access.VIEW) -> None:
    if not conn.execute("SELECT 1 FROM students WHERE id = %s", (student_id,)).fetchone() \
            or not _visible_student(conn, p, student_id):
        raise HTTPException(404, "student not found")
    if need == MANAGE:
        courses = conn.execute("SELECT course_id FROM enrolments WHERE student_id = %s AND status = 'active'",
                               (student_id,)).fetchall()
        if not any(access.course_access(conn, p, c["course_id"]) == MANAGE for c in courses):
            raise access.forbid("you need reviewer access to one of this student's courses")


# ------------------------------------------------------------------ profiles


@router.get("/students/{student_id}/profiles")
def list_profiles(student_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    _require_student(conn, p, student_id)
    rows = conn.execute(
        "SELECT id, pipeline, version, source, demo_author_id, model_version, status, reference_count, "
        "supervised_count, artifact_sha256, summary, error, created_at, built_at "
        "FROM baseline_profiles WHERE student_id = %s ORDER BY pipeline, version DESC", (student_id,)).fetchall()
    eligible = service.eligible_baseline(conn, student_id)
    ids = [d["id"] for d in eligible]
    reviews, digests = service.latest_reviews(conn, ids), service.text_digests(conn, p.institution_id, ids)
    passed = [d for d in ids if (r := reviews.get(d)) and r["text_sha256"] == digests.get(d)
              and r["language"] == "english" and r["extraction"] == "pass"]
    problems = [d for d in ids if (r := reviews.get(d)) and r["text_sha256"] == digests.get(d)
                and (r["language"] in ("non_english", "mixed") or r["extraction"] == "fail")]
    return {
        "profiles": rows,
        "consistency": {
            "model_version": consistency.MODEL_VERSION,
            "pipeline_version": consistency.PIPELINE_VERSION,
            "min_documents": consistency.MIN_REFERENCE_DOCUMENTS,
            "min_words": consistency.MIN_WORDS,
            "eligible_documents": len(eligible),
            "quality_passed_documents": len(passed),
            "quality_problem_documents": len(problems),
            "demo_profiles_allowed": _demo_profiles_usable(),
            "unreviewed_scoring_allowed": get_settings().allow_unreviewed_consistency,
        },
    }


class DemoProfileIn(BaseModel):
    author_id: str = Field(pattern=r"^author_[0-9]{1,8}$")


@router.get("/analysis/demo-profiles")
def list_demo_profiles(p: Principal = Depends(require_user)):
    if not get_settings().allow_demo_profiles:
        raise HTTPException(404, "demonstration profiles are switched off")
    return consistency.demo_profiles()


@router.post("/students/{student_id}/profiles/demonstration", status_code=201)
def link_demo_profile(student_id: UUID, body: DemoProfileIn, p: Principal = Depends(require_user),
                      conn: Connection = Depends(tenant_conn)):
    if not get_settings().allow_demo_profiles:
        raise HTTPException(404, "demonstration profiles are switched off")
    if not get_settings().allow_unreviewed_consistency:
        raise HTTPException(409, "demonstration profiles can't be quality-reviewed, so they only produce a result "
                                 "with VISKOPIC_ALLOW_UNREVIEWED_CONSISTENCY=true (local development)")
    _require_student(conn, p, student_id, MANAGE)
    if body.author_id not in {d["profile_id"] for d in consistency.demo_profiles()}:
        raise HTTPException(422, "unknown demonstration profile")
    pid = service.link_demonstration_profile(conn, p.institution_id, student_id, body.author_id, p.user_id)
    audit.record(conn, p, "profile.demo_linked", "student", student_id,
                 {"profile_id": str(pid), "demo_author_id": body.author_id})
    return {"id": pid, "status": "ready", "source": "demonstration"}


@router.post("/students/{student_id}/profiles/rebuild", status_code=202)
def rebuild_profile(student_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    """Queue an enrolled profile from the current baseline (e.g. after a failed build was fixed)."""
    _require_student(conn, p, student_id, MANAGE)
    docs = service.eligible_baseline(conn, student_id)
    if len(docs) < consistency.MIN_REFERENCE_DOCUMENTS:
        raise HTTPException(422, f"the model needs {consistency.MIN_REFERENCE_DOCUMENTS} completed baseline documents "
                                 f"of {consistency.MIN_WORDS}+ words; this student has {len(docs)}")
    if conn.execute("SELECT 1 FROM baseline_profiles WHERE student_id = %s AND status IN ('queued','building')",
                    (student_id,)).fetchone():
        raise HTTPException(409, "a profile is already being built for this student")
    ids = [d["id"] for d in docs]
    pid = conn.execute(
        "INSERT INTO baseline_profiles (institution_id, student_id, pipeline, version, source, model_version, "
        "reference_document_ids, reference_count, supervised_count, created_by) "
        "VALUES (%s, %s, 'consistency', %s, 'enrolled', %s, %s, %s, %s, %s) RETURNING id",
        (p.institution_id, student_id, service._next_version(conn, student_id), consistency.MODEL_VERSION, ids,
         len(ids), sum(1 for d in docs if d["provenance"] == "supervised"), p.user_id)).fetchone()["id"]
    conn.execute("INSERT INTO jobs (institution_id, kind, profile_id) VALUES (%s, 'build_profile', %s)",
                 (p.institution_id, pid))
    audit.record(conn, p, "profile.rebuild_requested", "student", student_id, {"profile_id": str(pid)})
    return {"id": pid, "status": "queued"}


# ------------------------------------------------------------------ evidence report


def _report(conn: Connection, case: dict) -> dict:
    runs = conn.execute(
        "SELECT DISTINCT ON (pipeline) id, pipeline, pipeline_version, status, profile_id, score, threshold, outcome, "
        "confidence, reason, details, created_at FROM analysis_runs WHERE case_id = %s "
        "ORDER BY pipeline, created_at DESC", (case["id"],)).fetchall()
    run_ids = [r["id"] for r in runs]
    items = conn.execute(
        "SELECT run_id, ordinal, category, kind, statement, observed, baseline_mean, baseline_sd, z_score, "
        "contribution, spans, details FROM evidence_items WHERE run_id = ANY(%s) ORDER BY run_id, ordinal",
        (run_ids,)).fetchall() if run_ids else []
    by_run: dict = {}
    for it in items:
        by_run.setdefault(it["run_id"], []).append(it)
    profiles = {r["profile_id"] for r in runs if r["profile_id"]}
    prof_rows = {r["id"]: r for r in conn.execute(
        "SELECT id, version, source, demo_author_id, reference_count, supervised_count, status FROM baseline_profiles "
        "WHERE id = ANY(%s)", (list(profiles),)).fetchall()} if profiles else {}
    for r in runs:
        r["label"] = PIPELINE_LABEL.get(r["pipeline"], r["pipeline"])
        r["evidence"] = by_run.get(r["id"], [])
        r["profile"] = prof_rows.get(r["profile_id"])
    runs.sort(key=lambda r: PIPELINE_ORDER.index(r["pipeline"]) if r["pipeline"] in PIPELINE_ORDER else 99)
    history = conn.execute("SELECT count(*) AS n FROM analysis_runs WHERE case_id = %s", (case["id"],)).fetchone()["n"]
    return {"case": case, "runs": runs, "total_runs": history}


@router.get("/documents/{document_id}/analysis")
def get_analysis(document_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    doc = conn.execute("SELECT course_id, role FROM documents WHERE id = %s", (document_id,)).fetchone()
    if doc is None or access.course_access(conn, p, doc["course_id"]) is None:
        raise HTTPException(404, "document not found")
    case = conn.execute("SELECT id, document_id, student_id, course_id, status, created_at, updated_at "
                        "FROM cases WHERE document_id = %s", (document_id,)).fetchone()
    if case is None:
        return {"case": None, "runs": [], "role": doc["role"]}
    out = _report(conn, case)
    out["role"] = doc["role"]
    audit.record(conn, p, "analysis.viewed", "case", case["id"])
    return out


@router.post("/cases/{case_id}/reanalyse", status_code=202)
def reanalyse(case_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    case = conn.execute("SELECT id, course_id, status FROM cases WHERE id = %s", (case_id,)).fetchone()
    if case is None or access.course_access(conn, p, case["course_id"]) is None:
        raise HTTPException(404, "case not found")
    access.require_course(conn, p, case["course_id"], MANAGE)
    if case["status"] == "analysing":
        raise HTTPException(409, "the case is already being analysed")
    if case["status"] == "withdrawn":
        raise HTTPException(409, "the case was withdrawn because its document was removed")
    service.queue_analysis(conn, p.institution_id, case_id)
    audit.record(conn, p, "analysis.requested", "case", case_id)
    return {"ok": True}
