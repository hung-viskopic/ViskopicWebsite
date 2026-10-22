"""Database side of analysis: baseline profiles, cases, analysis runs and evidence items.

Flow
  baseline document extracted ──► enough baseline? ──► build_profile job ──► encrypted profile artifact
  submission extracted ──► case ──► analyse_case job ──► one analysis run per pipeline + evidence items
  profile becomes ready ──► cases waiting for a baseline are analysed again

Baseline protection: only documents uploaded as `baseline` ever go into a profile. A submission
never joins the student's baseline here (that happens only after its case is cleared, later).
"""
from __future__ import annotations

import hashlib
import logging
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from .. import crypto
from ..audit import record
from ..db import tenant_tx
from ..deps import institution_dek
from ..storage import get_storage
from . import consistency
from .base import AnalysisContext, PipelineInputError, PipelineResult, ProfileRef, QualityRecord
from .signals import registered_pipelines

log = logging.getLogger("viskopic.analysis")


class JobError(Exception):
    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def profile_storage_key(institution_id, profile_id) -> str:
    return f"{institution_id}/profiles/{profile_id}"


def _profile_aad(profile_id) -> str:
    return f"baseline_profile:{profile_id}"


# ------------------------------------------------------------------ triggers (run inside the worker's tenant tx)


def on_document_extracted(conn: Connection, institution_id: UUID, document_id: UUID) -> None:
    doc = conn.execute("SELECT role, student_id, course_id FROM documents WHERE id = %s", (document_id,)).fetchone()
    if doc is None:
        return
    if doc["role"] == "baseline":
        maybe_queue_profile(conn, institution_id, doc["student_id"])
    else:
        case = conn.execute(
            "INSERT INTO cases (institution_id, document_id, student_id, course_id) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (document_id) DO UPDATE SET status = 'analysing', updated_at = now() RETURNING id",
            (institution_id, document_id, doc["student_id"], doc["course_id"]),
        ).fetchone()
        queue_analysis(conn, institution_id, case["id"])
        record(conn, None, "case.opened", "case", case["id"], {"document_id": str(document_id)},
               institution_id=institution_id, actor_label="worker")


def queue_analysis(conn: Connection, institution_id: UUID, case_id: UUID) -> None:
    conn.execute("UPDATE cases SET status = 'analysing', updated_at = now() WHERE id = %s", (case_id,))
    conn.execute("INSERT INTO jobs (institution_id, kind, case_id) VALUES (%s, 'analyse_case', %s)",
                 (institution_id, case_id))


def eligible_baseline(conn: Connection, student_id: UUID) -> list[dict]:
    """Completed baseline documents long enough for the consistency model, oldest first."""
    return conn.execute(
        "SELECT d.id, d.provenance, t.word_count FROM documents d JOIN document_texts t ON t.document_id = d.id "
        "WHERE d.student_id = %s AND d.role = 'baseline' AND d.status = 'completed' AND t.word_count >= %s "
        "ORDER BY d.uploaded_at, d.id",
        (student_id, consistency.MIN_WORDS),
    ).fetchall()


def maybe_queue_profile(conn: Connection, institution_id: UUID, student_id: UUID) -> UUID | None:
    """Queue a new profile version when the student's eligible baseline has changed."""
    docs = eligible_baseline(conn, student_id)
    if len(docs) < consistency.MIN_REFERENCE_DOCUMENTS:
        return None
    if conn.execute("SELECT 1 FROM baseline_profiles WHERE student_id = %s AND pipeline = 'consistency' "
                    "AND status IN ('queued','building')", (student_id,)).fetchone():
        return None  # it re-checks when the running build finishes
    ids = [d["id"] for d in docs]
    latest = conn.execute(
        "SELECT reference_document_ids FROM baseline_profiles WHERE student_id = %s AND pipeline = 'consistency' "
        "AND source = 'enrolled' AND status IN ('ready','failed') ORDER BY version DESC LIMIT 1",
        (student_id,),
    ).fetchone()
    if latest and sorted(latest["reference_document_ids"]) == sorted(ids):
        return None
    version = _next_version(conn, student_id)
    pid = conn.execute(
        "INSERT INTO baseline_profiles (institution_id, student_id, pipeline, version, source, model_version, "
        "reference_document_ids, reference_count, supervised_count) "
        "VALUES (%s, %s, 'consistency', %s, 'enrolled', %s, %s, %s, %s) RETURNING id",
        (institution_id, student_id, version, consistency.MODEL_VERSION, ids, len(ids),
         sum(1 for d in docs if d["provenance"] == "supervised")),
    ).fetchone()["id"]
    conn.execute("INSERT INTO jobs (institution_id, kind, profile_id) VALUES (%s, 'build_profile', %s)",
                 (institution_id, pid))
    record(conn, None, "profile.queued", "student", student_id,
           {"profile_id": str(pid), "version": version, "documents": len(ids)},
           institution_id=institution_id, actor_label="worker")
    return pid


def _next_version(conn: Connection, student_id: UUID) -> int:
    return conn.execute(
        "SELECT COALESCE(max(version), 0) + 1 AS v FROM baseline_profiles WHERE student_id = %s AND pipeline = 'consistency'",
        (student_id,),
    ).fetchone()["v"]


def activate_profile(conn: Connection, institution_id: UUID, student_id: UUID, profile_id: UUID) -> int:
    """Make a profile the current one and re-analyse cases that were waiting for it."""
    conn.execute("UPDATE baseline_profiles SET status = 'superseded' WHERE student_id = %s AND pipeline = 'consistency' "
                 "AND status = 'ready' AND id <> %s", (student_id, profile_id))
    conn.execute("UPDATE baseline_profiles SET status = 'ready', built_at = now() WHERE id = %s", (profile_id,))
    waiting = conn.execute("SELECT id FROM cases WHERE student_id = %s AND status = 'awaiting_baseline'",
                           (student_id,)).fetchall()
    for c in waiting:
        queue_analysis(conn, institution_id, c["id"])
    return len(waiting)


def link_demonstration_profile(conn: Connection, institution_id: UUID, student_id: UUID, author_id: str,
                               user_id: UUID | None) -> UUID:
    version = _next_version(conn, student_id)
    pid = uuid4()
    conn.execute(
        "INSERT INTO baseline_profiles (id, institution_id, student_id, pipeline, version, source, demo_author_id, "
        "model_version, status, summary, created_by) "
        "VALUES (%s, %s, %s, 'consistency', %s, 'demonstration', %s, %s, 'queued', %s, %s)",
        (pid, institution_id, student_id, version, author_id, consistency.MODEL_VERSION,
         Jsonb({"note": "demonstration profile, not this student's writing"}), user_id),
    )
    activate_profile(conn, institution_id, student_id, pid)
    return pid


# ------------------------------------------------------------------ profile loading


def current_profile(conn: Connection, institution_id: UUID, student_id: UUID, pipeline: str) -> ProfileRef | None:
    row = conn.execute(
        "SELECT * FROM baseline_profiles WHERE student_id = %s AND pipeline = %s AND status = 'ready'",
        (student_id, pipeline),
    ).fetchone()
    if row is None:
        return None
    loader = None
    if row["artifact_storage_key"]:
        key, pid, wrapped_iid = row["artifact_storage_key"], row["id"], institution_id

        def loader() -> bytes:
            # Re-read the data key in its own tenant transaction so it is never held longer than needed.
            with tenant_tx(wrapped_iid) as c:
                dek = institution_dek(c, wrapped_iid)
            return crypto.decrypt(dek, get_storage().get(key), _profile_aad(pid))

    return ProfileRef(id=row["id"], version=row["version"], source=row["source"],
                      demo_author_id=row["demo_author_id"], artifact_sha256=row["artifact_sha256"],
                      reference_count=row["reference_count"], supervised_count=row["supervised_count"],
                      load_artifact=loader)


# ------------------------------------------------------------------ quality reviews
#
# The Δ model scores only texts whose language and extraction a person has checked (its website
# contract). Reviews live in `document_quality_reviews`, bound to the SHA-256 of the exact text reviewed.

_LANGUAGE_ORDER = ["non_english", "mixed", "uncertain", "unreviewed", "english"]   # worst first
_EXTRACTION_ORDER = ["fail", "unreviewed", "pass"]


def latest_reviews(conn: Connection, document_ids: list) -> dict:
    """The current (latest) review per document, keyed by document id."""
    if not document_ids:
        return {}
    rows = conn.execute(
        "SELECT DISTINCT ON (r.document_id) r.id, r.document_id, r.text_sha256, r.language, r.extraction, "
        "r.reviewed_at, u.email AS reviewed_by_email FROM document_quality_reviews r "
        "JOIN users u ON u.id = r.reviewed_by WHERE r.document_id = ANY(%s) "
        "ORDER BY r.document_id, r.reviewed_at DESC, r.id", (list(document_ids),)).fetchall()
    return {r["document_id"]: r for r in rows}


def candidate_quality(review: dict | None) -> QualityRecord | None:
    """The submission's review as the pipeline receives it. The model's adapter checks its digest."""
    if review is None:
        return None
    return QualityRecord(review_id=str(review["id"]), language=review["language"], extraction=review["extraction"],
                         text_sha256=review["text_sha256"])


def combine_reference_quality(docs: list[tuple[str, dict | None]]) -> QualityRecord:
    """Combine the reviews of a profile's baseline documents into one outcome.

    `docs` holds (sha256 of the document's current text, its latest review or None). The worst language and
    extraction outcome wins; a document without a review counts as unreviewed; a review of a different text
    than the one stored now makes the whole baseline stale.
    """
    languages, extractions, ids, stale = [], [], [], False
    for current_sha, review in docs:
        if review is None:
            languages.append("unreviewed")
            extractions.append("unreviewed")
            continue
        stale |= review["text_sha256"] != current_sha
        languages.append(review["language"])
        extractions.append(review["extraction"])
        ids.append(str(review["id"]))
    language = min(languages or ["unreviewed"], key=_LANGUAGE_ORDER.index)
    extraction = min(extractions or ["unreviewed"], key=_EXTRACTION_ORDER.index)
    review_id = "baseline:" + hashlib.sha256(",".join(sorted(ids)).encode()).hexdigest()[:24]
    return QualityRecord(review_id=review_id, language=language, extraction=extraction, stale=stale)


def text_digests(conn: Connection, institution_id: UUID, document_ids: list) -> dict:
    """SHA-256 of each document's current extracted text (decrypted in memory, never returned)."""
    if not document_ids:
        return {}
    dek = institution_dek(conn, institution_id)
    rows = conn.execute("SELECT document_id, text_enc FROM document_texts WHERE document_id = ANY(%s)",
                        (list(document_ids),)).fetchall()
    return {r["document_id"]: consistency.text_sha256(
        crypto.decrypt(dek, r["text_enc"], f"document_text:{r['document_id']}").decode()) for r in rows}


def reference_quality(conn: Connection, institution_id: UUID, profile_id: UUID) -> QualityRecord | None:
    """Combined review of an enrolled profile's baseline documents; None for demonstration profiles."""
    row = conn.execute("SELECT source, reference_document_ids FROM baseline_profiles WHERE id = %s",
                       (profile_id,)).fetchone()
    if row is None or row["source"] != "enrolled":
        return None
    ids = row["reference_document_ids"]
    reviews, digests = latest_reviews(conn, ids), text_digests(conn, institution_id, ids)
    return combine_reference_quality([(digests.get(d), reviews.get(d)) for d in ids])


def requeue_after_review(conn: Connection, institution_id: UUID, document_id: UUID) -> int:
    """Re-analyse the cases a new review can change: the submission's own case, or (for a baseline
    document) the student's cases whose current profile was built from it."""
    doc = conn.execute("SELECT role, student_id FROM documents WHERE id = %s", (document_id,)).fetchone()
    if doc["role"] == "submission":
        cases = conn.execute("SELECT id FROM cases WHERE document_id = %s AND status IN "
                             "('awaiting_review','evidence_ready','failed')", (document_id,)).fetchall()
    else:
        if not conn.execute("SELECT 1 FROM baseline_profiles WHERE student_id = %s AND status = 'ready' "
                            "AND %s = ANY(reference_document_ids)", (doc["student_id"], document_id)).fetchone():
            return 0
        cases = conn.execute("SELECT id FROM cases WHERE student_id = %s AND status IN "
                             "('awaiting_review','evidence_ready','failed')", (doc["student_id"],)).fetchall()
    for c in cases:
        queue_analysis(conn, institution_id, c["id"])
    return len(cases)


# ------------------------------------------------------------------ jobs


def _still_current(conn: Connection, job: dict) -> bool:
    current = conn.execute("SELECT attempts FROM jobs WHERE id = %s FOR UPDATE", (job["id"],)).fetchone()
    if current is None or current["attempts"] != job["attempts"]:
        log.warning("job %s superseded by a newer attempt; discarding result", job["id"])
        return False
    return True


def run_build_profile(job: dict) -> None:
    iid, pid = job["institution_id"], job["profile_id"]
    with tenant_tx(iid) as conn:
        prof = conn.execute(
            "UPDATE baseline_profiles SET status = 'building' WHERE id = %s AND status IN ('queued','building') "
            "RETURNING student_id, reference_document_ids", (pid,)).fetchone()
        if prof is None:
            raise JobError("profile no longer needs building", permanent=True)
        dek = institution_dek(conn, iid)
        rows = conn.execute(
            "SELECT document_id, text_enc FROM document_texts WHERE document_id = ANY(%s)",
            (prof["reference_document_ids"],)).fetchall()
    if len(rows) != len(prof["reference_document_ids"]):
        raise JobError("a baseline document's text is missing", permanent=True)
    refs = [(str(r["document_id"]), crypto.decrypt(dek, r["text_enc"], f"document_text:{r['document_id']}").decode())
            for r in rows]
    refs.sort()
    try:
        data, sha, summary = consistency.enrol(pid, refs)
    except PipelineInputError as e:
        raise JobError(f"baseline not usable by the model: {e}", permanent=True) from e

    key = profile_storage_key(iid, pid)
    get_storage().put(key, crypto.encrypt(dek, data, _profile_aad(pid)))
    with tenant_tx(iid) as conn:
        if not _still_current(conn, job):
            get_storage().delete(key)
            return
        still = conn.execute("SELECT status FROM baseline_profiles WHERE id = %s FOR UPDATE", (pid,)).fetchone()
        if still is None or still["status"] != "building":
            # Retired while building (e.g. one of its documents was removed): throw the result away.
            get_storage().delete(key)
            conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now() WHERE id = %s",
                         (job["id"],))
            return
        conn.execute(
            "UPDATE baseline_profiles SET artifact_storage_key = %s, artifact_sha256 = %s, summary = %s, error = NULL "
            "WHERE id = %s", (key, sha, Jsonb(summary), pid))
        requeued = activate_profile(conn, iid, prof["student_id"], pid)
        conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now() WHERE id = %s", (job["id"],))
        record(conn, None, "profile.built", "student", prof["student_id"],
               {"profile_id": str(pid), "documents": len(refs), "artifact_sha256": sha,
                "pipeline_version": consistency.PIPELINE_VERSION, "cases_requeued": requeued},
               institution_id=iid, actor_label="worker")
        # Baseline documents may have been added while this one was building.
        maybe_queue_profile(conn, iid, prof["student_id"])


def run_analyse_case(job: dict) -> None:
    iid, case_id = job["institution_id"], job["case_id"]
    with tenant_tx(iid) as conn:
        case = conn.execute(
            "SELECT c.id, c.document_id, c.student_id, s.pseudonym, t.text_enc, t.word_count "
            "FROM cases c JOIN students s ON s.id = c.student_id "
            "LEFT JOIN document_texts t ON t.document_id = c.document_id WHERE c.id = %s", (case_id,)).fetchone()
        if case is None:
            raise JobError("case no longer exists", permanent=True)
        if conn.execute("SELECT status FROM cases WHERE id = %s", (case_id,)).fetchone()["status"] == "withdrawn":
            raise JobError("case withdrawn: its document was removed", permanent=True)
        if case["text_enc"] is None:
            raise JobError("the submission's text is not available", permanent=True)
        dek = institution_dek(conn, iid)
        signals = conn.execute(
            "SELECT hidden_text_chars, hidden_text_spans, metadata FROM document_signals WHERE document_id = %s",
            (case["document_id"],)).fetchone()
        profile = current_profile(conn, iid, case["student_id"], "consistency")
        cand_review = latest_reviews(conn, [case["document_id"]]).get(case["document_id"])
        ref_quality = reference_quality(conn, iid, profile.id) if profile else None

    ctx = AnalysisContext(
        institution_id=iid, case_id=case_id, document_id=case["document_id"],
        student_pseudonym=case["pseudonym"],
        text=crypto.decrypt(dek, case["text_enc"], f"document_text:{case['document_id']}").decode(),
        word_count=case["word_count"], signals=signals,
        profiles={"consistency": profile} if profile else {},
        candidate_quality=candidate_quality(cand_review),
        reference_quality=ref_quality,
    )
    results: list[tuple[object, PipelineResult]] = []
    for pipeline in registered_pipelines():
        try:
            res = pipeline.run(ctx)
        except PipelineInputError as e:
            res = PipelineResult(status="not_eligible", reason=str(e))
        results.append((pipeline, res))

    with tenant_tx(iid) as conn:
        if not _still_current(conn, job):
            return
        if conn.execute("SELECT status FROM cases WHERE id = %s FOR UPDATE", (case_id,)).fetchone()["status"] == "withdrawn":
            conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now() WHERE id = %s",
                         (job["id"],))
            return
        summary = {}
        for pipeline, res in results:
            run_id = conn.execute(
                "INSERT INTO analysis_runs (institution_id, case_id, pipeline, pipeline_version, status, profile_id, "
                "score, threshold, outcome, confidence, reason, details) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (iid, case_id, pipeline.name, pipeline.version, res.status, res.profile_id, res.score,
                 res.threshold, res.outcome, res.confidence, res.reason, Jsonb(res.details)),
            ).fetchone()["id"]
            for i, ev in enumerate(res.evidence):
                conn.execute(
                    "INSERT INTO evidence_items (institution_id, run_id, ordinal, category, kind, statement, observed, "
                    "baseline_mean, baseline_sd, z_score, contribution, spans, details) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (iid, run_id, i, ev.category, ev.kind, ev.statement, ev.observed, ev.baseline_mean,
                     ev.baseline_sd, ev.z_score, ev.contribution, Jsonb([s.as_json() for s in ev.spans]),
                     Jsonb(ev.details)),
                )
            summary[pipeline.name] = {"status": res.status, "version": pipeline.version, "outcome": res.outcome,
                                      "score": res.score, "evidence_items": len(res.evidence)}
            if "website_status" in res.details:
                summary[pipeline.name]["website_status"] = res.details["website_status"]
        delta = next((res for p, res in results if p.name == "consistency"), None)
        if "consistency" not in ctx.profiles:
            status = "awaiting_baseline"
        elif delta is not None and delta.details.get("website_status") == "quality_review_required":
            status = "awaiting_review"
        else:
            status = "evidence_ready"
        conn.execute("UPDATE cases SET status = %s, updated_at = now() WHERE id = %s", (status, case_id))
        conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now() WHERE id = %s", (job["id"],))
        record(conn, None, "analysis.completed", "case", case_id, {"status": status, "pipelines": summary},
               institution_id=iid, actor_label="worker")


def on_job_failed(conn: Connection, job: dict, message: str) -> None:
    if job["kind"] == "build_profile":
        row = conn.execute("UPDATE baseline_profiles SET status = 'failed', error = %s WHERE id = %s "
                           "AND status IN ('queued','building') RETURNING student_id",
                           (message, job["profile_id"])).fetchone()
        record(conn, None, "profile.failed", "student", row["student_id"] if row else None,
               {"profile_id": str(job["profile_id"]), "error": message},
               institution_id=job["institution_id"], actor_label="worker")
    elif job["kind"] == "analyse_case":
        conn.execute("UPDATE cases SET status = 'failed', updated_at = now() WHERE id = %s AND status <> 'withdrawn'",
                     (job["case_id"],))
        record(conn, None, "analysis.failed", "case", job["case_id"], {"error": message},
               institution_id=job["institution_id"], actor_label="worker")


# ------------------------------------------------------------------ document removal


def on_document_removed(conn: Connection, institution_id: UUID, document_id: UUID, student_id: UUID,
                        role: str) -> dict:
    """Undo everything analysis built from a removed document. Returns what changed, for the audit log.

    - Baseline document: every profile that includes it is retired and its encrypted artifact deleted;
      a new profile is queued from the remaining baseline (if still enough); the student's cases are
      re-analysed so no result keeps relying on the removed essay.
    - Submission: its case is withdrawn. Earlier analysis runs stay (numbers and template text only)
      so the audit trail is complete, but the case is no longer active.
    """
    changed: dict = {"profiles_retired": [], "cases_withdrawn": [], "cases_requeued": 0, "profile_queued": None}
    if role == "baseline":
        retired = conn.execute(
            "UPDATE baseline_profiles SET status = 'removed', error = 'built from a document that was removed' "
            "WHERE student_id = %s AND %s = ANY(reference_document_ids) AND status <> 'removed' "
            "RETURNING id, artifact_storage_key", (student_id, document_id)).fetchall()
        for r in retired:
            if r["artifact_storage_key"]:
                get_storage().delete(r["artifact_storage_key"])
            changed["profiles_retired"].append(str(r["id"]))
        if retired:
            pid = maybe_queue_profile(conn, institution_id, student_id)
            changed["profile_queued"] = str(pid) if pid else None
            for c in conn.execute("SELECT id FROM cases WHERE student_id = %s AND status NOT IN ('withdrawn','analysing')",
                                  (student_id,)).fetchall():
                queue_analysis(conn, institution_id, c["id"])
                changed["cases_requeued"] += 1
    else:
        rows = conn.execute("UPDATE cases SET status = 'withdrawn', updated_at = now() WHERE document_id = %s "
                            "RETURNING id", (document_id,)).fetchall()
        changed["cases_withdrawn"] = [str(r["id"]) for r in rows]
    # Pending jobs for the document or its case won't find anything to do.
    conn.execute("UPDATE jobs SET status = 'failed', last_error = 'document removed', lease_until = NULL, "
                 "updated_at = now() WHERE status IN ('queued','running') AND (document_id = %s OR case_id IN "
                 "(SELECT id FROM cases WHERE document_id = %s))", (document_id, document_id))
    return changed
