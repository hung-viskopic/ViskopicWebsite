"""Background worker: claims jobs from Postgres and runs them.

Run with:  python -m viskopic.worker

Jobs are claimed with FOR UPDATE SKIP LOCKED, so several workers can run safely.
A claimed job holds a lease; if a worker dies, the lease expires and another worker
picks the job up. Each attempt is numbered, and a stale worker cannot overwrite the
result of a newer attempt.
"""
import hashlib
import logging
import signal
import time
from uuid import UUID

from psycopg.types.json import Jsonb

from . import crypto
from .audit import record
from .config import get_settings
from .db import system_tx, tenant_tx
from .deps import institution_dek
from .analysis import service as analysis
from .analysis.service import JobError
from .intake.extraction import ExtractionError, extract_sandboxed
from .storage import get_storage

log = logging.getLogger("viskopic.worker")
# Building a profile scores the whole frozen background bank, so it gets a longer lease.
CLAIM_SQL = """
UPDATE jobs SET status = 'running', attempts = attempts + 1,
       lease_until = now() + CASE kind WHEN 'build_profile' THEN interval '20 minutes'
                                       WHEN 'analyse_case' THEN interval '5 minutes'
                                       ELSE interval '2 minutes' END,
       updated_at = now()
WHERE id = (
  SELECT id FROM jobs
  WHERE (status = 'queued' AND run_after <= now())
     OR (status = 'running' AND lease_until < now())
  ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
)
RETURNING id, institution_id, kind, document_id, profile_id, case_id, attempts, max_attempts
"""


def claim() -> dict | None:
    with system_tx() as conn:
        return conn.execute(CLAIM_SQL).fetchone()


def process_one() -> bool:
    """Claim and run one job. Returns False when the queue is empty."""
    job = claim()
    if job is None:
        return False
    if job["attempts"] > job["max_attempts"]:
        _fail(job, "gave up after repeated interrupted attempts")
        return True
    try:
        if job["kind"] == "extract_document":
            _extract(job)
        elif job["kind"] == "build_profile":
            analysis.run_build_profile(job)
        elif job["kind"] == "analyse_case":
            analysis.run_analyse_case(job)
        else:
            raise JobError(f"unknown job kind {job['kind']}", permanent=True)
    except (ExtractionError, JobError) as e:
        if e.permanent or job["attempts"] >= job["max_attempts"]:
            _fail(job, str(e))
        else:
            _requeue(job, str(e))
    except Exception as e:  # infrastructure problem (storage, database): retry
        log.exception("job %s failed", job["id"])
        if job["attempts"] >= job["max_attempts"]:
            _fail(job, f"processing error: {type(e).__name__}")
        else:
            _requeue(job, f"processing error: {type(e).__name__}")
    return True


def _extract(job: dict) -> None:
    s = get_settings()
    iid, doc_id = job["institution_id"], job["document_id"]
    with tenant_tx(iid) as conn:
        doc = conn.execute(
            "UPDATE documents SET status = 'processing', attempts = %s WHERE id = %s AND status <> 'removed' "
            "RETURNING file_type, sha256, storage_key",
            (job["attempts"], doc_id),
        ).fetchone()
        if doc is None:
            raise ExtractionError("document no longer exists or was removed", permanent=True)
        dek = institution_dek(conn, iid)

    data = crypto.decrypt(dek, get_storage().get(doc["storage_key"]), f"document:{doc_id}")
    if hashlib.sha256(data).hexdigest() != doc["sha256"]:
        raise ExtractionError("checksum mismatch: the stored file does not match what was uploaded", permanent=True)

    result = extract_sandboxed(doc["file_type"], data, s.extraction_timeout_seconds, s.extraction_memory_mb)

    with tenant_tx(iid) as conn:
        current = conn.execute("SELECT attempts FROM jobs WHERE id = %s FOR UPDATE", (job["id"],)).fetchone()
        if current is None or current["attempts"] != job["attempts"]:
            log.warning("job %s superseded by a newer attempt; discarding result", job["id"])
            return
        if conn.execute("SELECT status FROM documents WHERE id = %s FOR UPDATE", (doc_id,)).fetchone()["status"] == "removed":
            log.info("document %s was removed during extraction; discarding result", doc_id)
            return
        conn.execute(
            "INSERT INTO document_texts (document_id, institution_id, text_enc, word_count, char_count, extractor_version) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (document_id) DO UPDATE SET text_enc = EXCLUDED.text_enc, "
            "word_count = EXCLUDED.word_count, char_count = EXCLUDED.char_count, "
            "extractor_version = EXCLUDED.extractor_version, extracted_at = now()",
            (doc_id, iid, crypto.encrypt(dek, result.text.encode(), f"document_text:{doc_id}"),
             result.word_count, result.char_count, result.extractor_version),
        )
        conn.execute(
            "INSERT INTO document_signals (document_id, institution_id, hidden_text_chars, hidden_text_spans, metadata) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (document_id) DO UPDATE SET "
            "hidden_text_chars = EXCLUDED.hidden_text_chars, hidden_text_spans = EXCLUDED.hidden_text_spans, "
            "metadata = EXCLUDED.metadata, created_at = now()",
            (doc_id, iid, result.hidden_text_chars, Jsonb(result.hidden_text_spans), Jsonb(result.metadata)),
        )
        conn.execute(
            "UPDATE documents SET status = 'completed', last_error = NULL, processed_at = now() WHERE id = %s", (doc_id,)
        )
        conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, updated_at = now() WHERE id = %s", (job["id"],))
        record(conn, None, "document.extracted", "document", doc_id,
               {"words": result.word_count, "hidden_text_chars": result.hidden_text_chars, "attempt": job["attempts"]},
               institution_id=iid, actor_label="worker")
        # Baseline: maybe build a new profile. Submission: open a case and queue its analysis.
        analysis.on_document_extracted(conn, iid, doc_id)


def _fail(job: dict, message: str) -> None:
    with tenant_tx(job["institution_id"]) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'failed', last_error = %s, lease_until = NULL, updated_at = now() WHERE id = %s",
            (message, job["id"]),
        )
        if job["kind"] != "extract_document":
            analysis.on_job_failed(conn, job, message)
            return
        conn.execute(
            "UPDATE documents SET status = 'failed', last_error = %s, processed_at = now() "
            "WHERE id = %s AND status <> 'removed'",
            (message, job["document_id"]),
        )
        record(conn, None, "document.failed", "document", job["document_id"],
               {"error": message, "attempts": job["attempts"]}, institution_id=job["institution_id"], actor_label="worker")


def _requeue(job: dict, message: str) -> None:
    delay = 10 * 2 ** (job["attempts"] - 1)  # 10s, 20s, 40s ...
    with tenant_tx(job["institution_id"]) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'queued', last_error = %s, lease_until = NULL, "
            "run_after = now() + make_interval(secs => %s), updated_at = now() WHERE id = %s",
            (message, delay, job["id"]),
        )
        if job["kind"] == "extract_document":
            conn.execute("UPDATE documents SET status = 'queued', last_error = %s WHERE id = %s AND status <> 'removed'",
                         (message, job["document_id"]))


def run_forever(poll_seconds: float = 1.0) -> None:
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker started")
    while not stop:
        try:
            if not process_one():
                time.sleep(poll_seconds)
        except Exception:
            log.exception("worker loop error")
            time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_forever()
