"""Document intake: upload, status, extracted text and signals."""
import hashlib
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from psycopg import Connection
from pydantic import BaseModel, Field

from .. import access, audit, crypto
from ..access import MANAGE
from ..analysis import consistency
from ..analysis import service as analysis
from ..auth import Principal, require_user
from ..config import get_settings
from ..deps import institution_dek, tenant_conn
from ..intake.validation import InvalidUpload, detect_type
from ..storage import get_storage

router = APIRouter(tags=["documents"])

DOC_COLUMNS = (
    "d.id, d.student_id, d.course_id, d.assignment_id, d.role, d.provenance, d.original_filename, d.file_type, "
    "d.size_bytes, d.sha256, d.status, d.attempts, d.last_error, d.uploaded_at, d.processed_at, "
    "d.removed_at, d.removal_reason, ru.email AS removed_by_email, "
    "c.code AS course_code, s.pseudonym AS student_pseudonym, a.title AS assignment_title, "
    "u.email AS uploaded_by_email, t.word_count"
)
DOC_JOINS = (
    "FROM documents d JOIN courses c ON c.id = d.course_id JOIN students s ON s.id = d.student_id "
    "LEFT JOIN assignments a ON a.id = d.assignment_id JOIN users u ON u.id = d.uploaded_by "
    "LEFT JOIN users ru ON ru.id = d.removed_by "
    "LEFT JOIN document_texts t ON t.document_id = d.id"
)


def storage_key(institution_id: UUID, document_id: UUID) -> str:
    return f"{institution_id}/documents/{document_id}"


@router.post("/documents", status_code=201)
def upload_document(
    file: UploadFile = File(...),
    student_id: UUID = Form(...),
    course_id: UUID = Form(...),
    role: Literal["baseline", "submission"] = Form(...),
    provenance: Literal["supervised", "unsupervised"] = Form(...),
    assignment_id: UUID | None = Form(None),
    p: Principal = Depends(require_user),
    conn: Connection = Depends(tenant_conn),
):
    access.require_course(conn, p, course_id, MANAGE)

    # Intake gate: student, course and enrolment must all be active.
    row = conn.execute(
        "SELECT s.status AS student_status, c.status AS course_status, e.status AS enrolment_status "
        "FROM students s CROSS JOIN courses c LEFT JOIN enrolments e ON e.student_id = s.id AND e.course_id = c.id "
        "WHERE s.id = %s AND c.id = %s",
        (student_id, course_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "student or course not found")
    problems = []
    if row["student_status"] != "active":
        problems.append("the student is archived")
    if row["course_status"] != "active":
        problems.append("the course is archived")
    if row["enrolment_status"] != "active":
        problems.append("the student is not actively enrolled on this course")
    if problems:
        raise HTTPException(422, "upload blocked: " + "; ".join(problems))
    if assignment_id is not None:
        a = conn.execute("SELECT course_id FROM assignments WHERE id = %s", (assignment_id,)).fetchone()
        if a is None or a["course_id"] != course_id:
            raise HTTPException(422, "the assignment does not belong to this course")

    max_bytes = get_settings().max_upload_bytes
    data = file.file.read(max_bytes + 1)
    filename = (file.filename or "upload").replace("/", "_").replace("\\", "_")[:255]
    try:
        kind = detect_type(filename, data)
    except InvalidUpload as e:
        raise HTTPException(422, f"file rejected: {e}") from e

    document_id = uuid4()
    sha = hashlib.sha256(data).hexdigest()
    dek = institution_dek(conn, p.institution_id)
    key = storage_key(p.institution_id, document_id)
    # Encrypt before the bytes leave the API process.
    get_storage().put(key, crypto.encrypt(dek, data, f"document:{document_id}"))

    try:
        conn.execute(
            "INSERT INTO documents (id, institution_id, student_id, course_id, assignment_id, role, provenance, "
            "original_filename, file_type, size_bytes, sha256, storage_key, uploaded_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (document_id, p.institution_id, student_id, course_id, assignment_id, role, provenance,
             filename, kind, len(data), sha, key, p.user_id),
        )
        conn.execute(
            "INSERT INTO jobs (institution_id, kind, document_id) VALUES (%s, 'extract_document', %s)",
            (p.institution_id, document_id),
        )
        audit.record(conn, p, "document.uploaded", "document", document_id,
                     {"role": role, "provenance": provenance, "file_type": kind, "size_bytes": len(data),
                      "sha256": sha, "course_id": str(course_id), "student_id": str(student_id)})
    except Exception:
        get_storage().delete(key)  # don't leave an orphaned object behind
        raise
    return {"id": document_id, "status": "queued", "file_type": kind, "sha256": sha}


@router.get("/documents")
def list_documents(
    course_id: UUID | None = None,
    student_id: UUID | None = None,
    status: str | None = None,
    p: Principal = Depends(require_user),
    conn: Connection = Depends(tenant_conn),
):
    ids = access.accessible_course_ids(conn, p)
    if course_id is not None:
        access.require_course(conn, p, course_id)
    return conn.execute(
        f"SELECT {DOC_COLUMNS} {DOC_JOINS} WHERE (%s::uuid[] IS NULL OR d.course_id = ANY(%s)) "
        "AND (%s::uuid IS NULL OR d.course_id = %s) AND (%s::uuid IS NULL OR d.student_id = %s) "
        "AND (%s::text IS NULL OR d.status = %s) ORDER BY d.uploaded_at DESC LIMIT 500",
        (None if ids is None else list(ids), None if ids is None else list(ids),
         course_id, course_id, student_id, student_id, status, status),
    ).fetchall()


def _load(conn: Connection, p: Principal, document_id: UUID) -> dict:
    row = conn.execute(f"SELECT {DOC_COLUMNS} {DOC_JOINS} WHERE d.id = %s", (document_id,)).fetchone()
    if row is None or access.course_access(conn, p, row["course_id"]) is None:
        raise HTTPException(404, "document not found")
    return row


@router.get("/documents/{document_id}")
def get_document(document_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    doc = _load(conn, p, document_id)
    doc["signals"] = conn.execute(
        "SELECT hidden_text_chars, hidden_text_spans, metadata FROM document_signals WHERE document_id = %s",
        (document_id,),
    ).fetchone()
    doc["text_meta"] = conn.execute(
        "SELECT word_count, char_count, extractor_version, extracted_at FROM document_texts WHERE document_id = %s",
        (document_id,),
    ).fetchone()
    doc["access"] = access.course_access(conn, p, doc["course_id"])
    doc["can_remove"] = access.is_owner(p) and doc["status"] != "removed"
    doc["quality_review"] = _quality_review(conn, p, document_id)
    return doc


def _quality_review(conn: Connection, p: Principal, document_id: UUID) -> dict | None:
    """The latest quality review and whether it still matches the stored text (no text is returned)."""
    review = analysis.latest_reviews(conn, [document_id]).get(document_id)
    if review is None:
        return None
    current = analysis.text_digests(conn, p.institution_id, [document_id]).get(document_id)
    return {"language": review["language"], "extraction": review["extraction"],
            "reviewed_at": review["reviewed_at"], "reviewed_by_email": review["reviewed_by_email"],
            "current": current is not None and current == review["text_sha256"]}


@router.get("/documents/{document_id}/text")
def get_document_text(document_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    _load(conn, p, document_id)
    t = conn.execute("SELECT text_enc FROM document_texts WHERE document_id = %s", (document_id,)).fetchone()
    if t is None:
        raise HTTPException(409, "the text was deleted when the document was removed" if _is_removed(conn, document_id)
                            else "text is not available yet")
    dek = institution_dek(conn, p.institution_id)
    audit.record(conn, p, "document.text_viewed", "document", document_id)
    return {"text": crypto.decrypt(dek, t["text_enc"], f"document_text:{document_id}").decode()}


@router.post("/documents/{document_id}/retry")
def retry_document(document_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    doc = _load(conn, p, document_id)
    access.require_course(conn, p, doc["course_id"], MANAGE)
    if doc["status"] != "failed":
        raise HTTPException(409, "only failed documents can be retried")
    conn.execute("UPDATE documents SET status = 'queued', attempts = 0, last_error = NULL WHERE id = %s", (document_id,))
    conn.execute(
        "INSERT INTO jobs (institution_id, kind, document_id) VALUES (%s, 'extract_document', %s)",
        (p.institution_id, document_id),
    )
    audit.record(conn, p, "document.retry", "document", document_id)
    return {"ok": True}


class QualityReviewIn(BaseModel):
    language: Literal["english", "non_english", "mixed", "uncertain"]
    extraction: Literal["pass", "fail"]


@router.post("/documents/{document_id}/quality-review", status_code=201)
def review_quality(document_id: UUID, body: QualityReviewIn, p: Principal = Depends(require_user),
                   conn: Connection = Depends(tenant_conn)):
    """Record a reviewer's check of the extracted text: is it English prose, and was it extracted cleanly?

    The Δ model scores only reviewed texts. The review is bound to the SHA-256 of the text as stored now,
    computed here on the server, so it can't be attached to a different text. Reviewers only.
    """
    doc = _load(conn, p, document_id)
    access.require_course(conn, p, doc["course_id"], MANAGE)
    if doc["status"] != "completed":
        raise HTTPException(409, "only documents with extracted text can be reviewed")
    sha = analysis.text_digests(conn, p.institution_id, [document_id]).get(document_id)
    if sha is None:
        raise HTTPException(409, "text is not available")
    rid = conn.execute(
        "INSERT INTO document_quality_reviews (institution_id, document_id, text_sha256, language, extraction, "
        "reviewed_by) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (p.institution_id, document_id, sha, body.language, body.extraction, p.user_id)).fetchone()["id"]
    requeued = analysis.requeue_after_review(conn, p.institution_id, document_id)
    audit.record(conn, p, "document.quality_reviewed", "document", document_id,
                 {"review_id": str(rid), "language": body.language, "extraction": body.extraction,
                  "text_sha256": sha, "pipeline_version": consistency.PIPELINE_VERSION, "cases_requeued": requeued})
    return {"id": rid, "language": body.language, "extraction": body.extraction, "current": True,
            "cases_requeued": requeued}


def _is_removed(conn: Connection, document_id: UUID) -> bool:
    row = conn.execute("SELECT status FROM documents WHERE id = %s", (document_id,)).fetchone()
    return row is not None and row["status"] == "removed"


class RemoveIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


@router.post("/documents/{document_id}/remove")
def remove_document(document_id: UUID, body: RemoveIn, p: Principal = Depends(require_user),
                    conn: Connection = Depends(tenant_conn)):
    """Remove a wrongly uploaded document. Institution owners only.

    Deletes the original file, the extracted text and the file signals, keeps a tombstone row and an
    audit record (who, when, why), and undoes anything analysis built from it.
    """
    doc = _load(conn, p, document_id)
    if not access.is_owner(p):
        raise access.forbid("only institution owners can remove documents")
    row = conn.execute("SELECT status, role, student_id, storage_key FROM documents WHERE id = %s FOR UPDATE",
                       (document_id,)).fetchone()
    if row["status"] == "removed":
        raise HTTPException(409, "the document has already been removed")
    # Queued or processing documents can be removed too: the worker checks for removal before saving.
    conn.execute("DELETE FROM document_texts WHERE document_id = %s", (document_id,))
    conn.execute("DELETE FROM document_signals WHERE document_id = %s", (document_id,))
    conn.execute(
        "UPDATE documents SET status = 'removed', removed_at = now(), removed_by = %s, removal_reason = %s, "
        "last_error = NULL WHERE id = %s", (p.user_id, body.reason.strip(), document_id))
    changed = analysis.on_document_removed(conn, p.institution_id, document_id, row["student_id"], row["role"])
    audit.record(conn, p, "document.removed", "document", document_id,
                 {"reason": body.reason.strip(), "role": row["role"], "student_id": str(row["student_id"]),
                  "course_id": str(doc["course_id"]), "sha256": doc["sha256"], **changed})
    # Last, so a storage error rolls the whole removal back. (If the commit itself then failed, the
    # file would be gone but the row not marked removed; re-running the removal fixes that.)
    get_storage().delete(row["storage_key"])
    return {"ok": True, "status": "removed", **changed}
