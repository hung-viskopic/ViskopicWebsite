"""Departments, courses, assignments, students and enrolments."""
import secrets
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection
from pydantic import BaseModel, Field

from .. import access, audit, crypto
from ..access import MANAGE
from ..auth import Principal, require_user
from ..deps import institution_dek, tenant_conn

router = APIRouter(tags=["academic"])


# ------------------------------------------------------------------ departments

class DepartmentIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)


class DepartmentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    status: Literal["active", "archived"] | None = None


@router.get("/departments")
def list_departments(p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    return conn.execute("SELECT id, name, status, created_at FROM departments ORDER BY name").fetchall()


@router.post("/departments", status_code=201)
def create_department(body: DepartmentIn, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    if not access.is_owner(p):
        raise access.forbid("only institution owners can create departments")
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO departments (institution_id, name) VALUES (%s, %s) RETURNING id, name, status",
                (p.institution_id, body.name),
            ).fetchone()
    except psycopg.errors.UniqueViolation as e:
        raise HTTPException(409, "a department with this name already exists") from e
    audit.record(conn, p, "department.created", "department", row["id"], {"name": body.name})
    return row


@router.patch("/departments/{department_id}")
def update_department(department_id: UUID, body: DepartmentPatch, p: Principal = Depends(require_user),
                      conn: Connection = Depends(tenant_conn)):
    if not access.is_owner(p):
        raise access.forbid()
    row = conn.execute(
        "UPDATE departments SET name = COALESCE(%s, name), status = COALESCE(%s, status) WHERE id = %s "
        "RETURNING id, name, status",
        (body.name, body.status, department_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "department not found")
    audit.record(conn, p, "department.updated", "department", department_id, body.model_dump(exclude_none=True))
    return row


# ------------------------------------------------------------------ courses

class CourseIn(BaseModel):
    department_id: UUID
    code: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=2, max_length=300)


class CoursePatch(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=300)
    status: Literal["active", "archived"] | None = None


@router.get("/courses")
def list_courses(p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    ids = access.accessible_course_ids(conn, p)
    rows = conn.execute(
        "SELECT c.id, c.code, c.title, c.status, c.department_id, d.name AS department_name, "
        "(SELECT count(*) FROM enrolments e WHERE e.course_id = c.id AND e.status = 'active') AS active_students, "
        "(SELECT count(*) FROM documents x WHERE x.course_id = c.id) AS documents "
        "FROM courses c JOIN departments d ON d.id = c.department_id "
        "WHERE (%s::uuid[] IS NULL OR c.id = ANY(%s)) ORDER BY c.code",
        (None if ids is None else list(ids), None if ids is None else list(ids)),
    ).fetchall()
    for r in rows:
        r["access"] = access.course_access(conn, p, r["id"])
    return rows


@router.post("/courses", status_code=201)
def create_course(body: CourseIn, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    if not access.can_manage_department(p, body.department_id):
        raise access.forbid("you can only create courses in departments you administer")
    dept = conn.execute("SELECT status FROM departments WHERE id = %s", (body.department_id,)).fetchone()
    if dept is None:
        raise HTTPException(404, "department not found")
    if dept["status"] != "active":
        raise HTTPException(422, "department is archived")
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO courses (institution_id, department_id, code, title) VALUES (%s, %s, %s, %s) "
                "RETURNING id, code, title, status, department_id",
                (p.institution_id, body.department_id, body.code.strip(), body.title.strip()),
            ).fetchone()
    except psycopg.errors.UniqueViolation as e:
        raise HTTPException(409, "a course with this code already exists") from e
    audit.record(conn, p, "course.created", "course", row["id"], {"code": row["code"]})
    return row


@router.get("/courses/{course_id}")
def get_course(course_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    access.require_course(conn, p, course_id)
    row = conn.execute(
        "SELECT c.id, c.code, c.title, c.status, c.department_id, d.name AS department_name "
        "FROM courses c JOIN departments d ON d.id = c.department_id WHERE c.id = %s",
        (course_id,),
    ).fetchone()
    row["access"] = access.course_access(conn, p, course_id)
    return row


@router.patch("/courses/{course_id}")
def update_course(course_id: UUID, body: CoursePatch, p: Principal = Depends(require_user),
                  conn: Connection = Depends(tenant_conn)):
    course = conn.execute("SELECT department_id FROM courses WHERE id = %s", (course_id,)).fetchone()
    if course is None or access.course_access(conn, p, course_id) is None:
        raise HTTPException(404, "course not found")
    if not access.can_manage_department(p, course["department_id"]):
        raise access.forbid("only department administrators and owners can change courses")
    row = conn.execute(
        "UPDATE courses SET title = COALESCE(%s, title), status = COALESCE(%s, status) WHERE id = %s "
        "RETURNING id, code, title, status",
        (body.title, body.status, course_id),
    ).fetchone()
    audit.record(conn, p, "course.updated", "course", course_id, body.model_dump(exclude_none=True))
    return row


# ------------------------------------------------------------------ assignments

class AssignmentIn(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    prompt: str | None = Field(default=None, max_length=20_000)
    genre: Literal["essay", "report", "lab_report", "reflection", "dissertation", "other"] | None = None
    due_at: datetime | None = None


@router.get("/courses/{course_id}/assignments")
def list_assignments(course_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    access.require_course(conn, p, course_id)
    return conn.execute(
        "SELECT id, title, prompt, genre, due_at, created_at FROM assignments WHERE course_id = %s "
        "ORDER BY created_at DESC",
        (course_id,),
    ).fetchall()


@router.post("/courses/{course_id}/assignments", status_code=201)
def create_assignment(course_id: UUID, body: AssignmentIn, p: Principal = Depends(require_user),
                      conn: Connection = Depends(tenant_conn)):
    access.require_course(conn, p, course_id, MANAGE)
    row = conn.execute(
        "INSERT INTO assignments (institution_id, course_id, title, prompt, genre, due_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, title, prompt, genre, due_at, created_at",
        (p.institution_id, course_id, body.title, body.prompt, body.genre, body.due_at),
    ).fetchone()
    audit.record(conn, p, "assignment.created", "assignment", row["id"], {"course_id": str(course_id)})
    return row


# ------------------------------------------------------------------ students

class StudentIn(BaseModel):
    external_ref: str = Field(min_length=1, max_length=100, description="University student number")
    display_name: str | None = Field(default=None, max_length=200)
    enrol_course_id: UUID | None = None


class StudentPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    status: Literal["active", "archived"] | None = None


def _pseudonym() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "S-" + "".join(secrets.choice(alphabet) for _ in range(8))


def student_out(row: dict, dek: bytes) -> dict:
    sid = str(row["id"])
    out = {k: v for k, v in row.items() if k not in ("external_ref_enc", "display_name_enc", "external_ref_hmac")}
    out["external_ref"] = crypto.decrypt(dek, row["external_ref_enc"], f"student:{sid}:ref").decode()
    out["display_name"] = (
        crypto.decrypt(dek, row["display_name_enc"], f"student:{sid}:name").decode()
        if row.get("display_name_enc") else None
    )
    return out


def _can_create_students(conn: Connection, p: Principal) -> bool:
    if access.can_manage_people(p):
        return True
    return any(r.role == "reviewer" for r in p.roles)


def _visible_student(conn: Connection, p: Principal, student_id: UUID) -> bool:
    ids = access.accessible_course_ids(conn, p)
    if ids is None:
        return True
    return conn.execute(
        "SELECT 1 FROM enrolments WHERE student_id = %s AND course_id = ANY(%s)", (student_id, list(ids))
    ).fetchone() is not None


@router.get("/students")
def list_students(p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    ids = access.accessible_course_ids(conn, p)
    rows = conn.execute(
        "SELECT s.*, "
        "(SELECT count(*) FROM documents d WHERE d.student_id = s.id AND d.role = 'baseline' AND d.status = 'completed') AS baseline_docs, "
        "(SELECT count(*) FROM documents d WHERE d.student_id = s.id AND d.role = 'submission') AS submissions "
        "FROM students s WHERE %s::uuid[] IS NULL OR EXISTS "
        "(SELECT 1 FROM enrolments e WHERE e.student_id = s.id AND e.course_id = ANY(%s)) "
        "ORDER BY s.created_at DESC",
        (None if ids is None else list(ids), None if ids is None else list(ids)),
    ).fetchall()
    dek = institution_dek(conn, p.institution_id)
    return [student_out(r, dek) for r in rows]


@router.get("/students/lookup")
def lookup_student(ref: str, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    """Exact-match lookup by university reference, so staff can enrol an existing
    student without being able to browse every student in the institution."""
    if not _can_create_students(conn, p):
        raise access.forbid()
    dek = institution_dek(conn, p.institution_id)
    row = conn.execute(
        "SELECT * FROM students WHERE external_ref_hmac = %s", (crypto.lookup_hmac(dek, "student_ref", ref),)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no student with this reference")
    return student_out(row, dek)


@router.post("/students", status_code=201)
def create_student(body: StudentIn, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    if not _can_create_students(conn, p):
        raise access.forbid()
    if body.enrol_course_id:
        access.require_course(conn, p, body.enrol_course_id, MANAGE)
    elif not access.can_manage_people(p):
        raise HTTPException(422, "reviewers must enrol a new student on one of their courses")
    dek = institution_dek(conn, p.institution_id)
    ref_hmac = crypto.lookup_hmac(dek, "student_ref", body.external_ref)
    if conn.execute("SELECT 1 FROM students WHERE external_ref_hmac = %s", (ref_hmac,)).fetchone():
        raise HTTPException(409, "a student with this reference already exists; enrol the existing student instead")
    sid = conn.execute("SELECT gen_random_uuid() AS id").fetchone()["id"]
    row = conn.execute(
        "INSERT INTO students (id, institution_id, pseudonym, external_ref_hmac, external_ref_enc, display_name_enc) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
        (
            sid, p.institution_id, _pseudonym(), ref_hmac,
            crypto.encrypt(dek, body.external_ref.strip().encode(), f"student:{sid}:ref"),
            crypto.encrypt(dek, body.display_name.encode(), f"student:{sid}:name") if body.display_name else None,
        ),
    ).fetchone()
    if body.enrol_course_id:
        conn.execute(
            "INSERT INTO enrolments (institution_id, student_id, course_id) VALUES (%s, %s, %s)",
            (p.institution_id, sid, body.enrol_course_id),
        )
    audit.record(conn, p, "student.created", "student", sid,
                 {"pseudonym": row["pseudonym"], "enrolled_in": str(body.enrol_course_id) if body.enrol_course_id else None})
    return student_out(row, dek)


@router.get("/students/{student_id}")
def get_student(student_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    row = conn.execute("SELECT * FROM students WHERE id = %s", (student_id,)).fetchone()
    if row is None or not _visible_student(conn, p, student_id):
        raise HTTPException(404, "student not found")
    ids = access.accessible_course_ids(conn, p)
    flt = (None if ids is None else list(ids),) * 2
    enrolments = conn.execute(
        "SELECT e.id, e.status, e.enrolled_at, c.id AS course_id, c.code, c.title, c.status AS course_status "
        "FROM enrolments e JOIN courses c ON c.id = e.course_id WHERE e.student_id = %s "
        "AND (%s::uuid[] IS NULL OR c.id = ANY(%s)) ORDER BY c.code",
        (student_id, *flt),
    ).fetchall()
    documents = conn.execute(
        "SELECT d.id, d.role, d.provenance, d.original_filename, d.file_type, d.status, d.uploaded_at, "
        "d.course_id, c.code AS course_code, a.title AS assignment_title, t.word_count "
        "FROM documents d JOIN courses c ON c.id = d.course_id LEFT JOIN assignments a ON a.id = d.assignment_id "
        "LEFT JOIN document_texts t ON t.document_id = d.id WHERE d.student_id = %s "
        "AND (%s::uuid[] IS NULL OR d.course_id = ANY(%s)) ORDER BY d.uploaded_at DESC",
        (student_id, *flt),
    ).fetchall()
    audit.record(conn, p, "student.viewed", "student", student_id)
    out = student_out(row, institution_dek(conn, p.institution_id))
    out["enrolments"] = enrolments
    out["documents"] = documents
    out["baseline"] = baseline_summary(documents)
    out["can_manage"] = any(e["status"] == "active" and access.course_access(conn, p, e["course_id"]) == MANAGE
                            for e in enrolments)
    return out


def baseline_summary(documents: list[dict]) -> dict:
    """How much of the student's own writing is available. Thresholds are the MVP
    defaults from the design and will become institution settings."""
    done = [d for d in documents if d["role"] == "baseline" and d["status"] == "completed"]
    words = sum(d["word_count"] or 0 for d in done)
    supervised = sum(1 for d in done if d["provenance"] == "supervised")
    eligible = len(done) >= 3 and words >= 1500
    if not eligible:
        confidence = None
    elif supervised >= 2 and len(done) >= 5:
        confidence = "high"
    elif supervised >= 1:
        confidence = "medium"
    else:
        confidence = "low"
    return {"essays": len(done), "words": words, "supervised": supervised,
            "eligible": eligible, "confidence": confidence, "min_essays": 3, "min_words": 1500}


@router.patch("/students/{student_id}")
def update_student(student_id: UUID, body: StudentPatch, p: Principal = Depends(require_user),
                   conn: Connection = Depends(tenant_conn)):
    if not _visible_student(conn, p, student_id) or not _can_create_students(conn, p):
        raise HTTPException(404, "student not found")
    dek = institution_dek(conn, p.institution_id)
    sets, params = [], []
    if body.display_name is not None:
        sets.append("display_name_enc = %s")
        params.append(crypto.encrypt(dek, body.display_name.encode(), f"student:{student_id}:name"))
    if body.status is not None:
        sets.append("status = %s")
        params.append(body.status)
    if not sets:
        raise HTTPException(422, "nothing to update")
    row = conn.execute(f"UPDATE students SET {', '.join(sets)} WHERE id = %s RETURNING *", (*params, student_id)).fetchone()
    audit.record(conn, p, "student.updated", "student", student_id, {"status": body.status} if body.status else {})
    return student_out(row, dek)


# ------------------------------------------------------------------ enrolments

class EnrolIn(BaseModel):
    student_id: UUID


class EnrolPatch(BaseModel):
    status: Literal["active", "withdrawn"]


@router.get("/courses/{course_id}/enrolments")
def list_enrolments(course_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    access.require_course(conn, p, course_id)
    rows = conn.execute(
        "SELECT e.id AS enrolment_id, e.status AS enrolment_status, e.enrolled_at, s.*, "
        "(SELECT count(*) FROM documents d WHERE d.student_id = s.id AND d.role = 'baseline' AND d.status = 'completed') AS baseline_docs "
        "FROM enrolments e JOIN students s ON s.id = e.student_id WHERE e.course_id = %s ORDER BY e.enrolled_at",
        (course_id,),
    ).fetchall()
    dek = institution_dek(conn, p.institution_id)
    return [student_out(r, dek) for r in rows]


@router.post("/courses/{course_id}/enrolments", status_code=201)
def enrol(course_id: UUID, body: EnrolIn, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    access.require_course(conn, p, course_id, MANAGE)
    if conn.execute("SELECT 1 FROM students WHERE id = %s", (body.student_id,)).fetchone() is None:
        raise HTTPException(404, "student not found")
    course = conn.execute("SELECT status FROM courses WHERE id = %s", (course_id,)).fetchone()
    if course["status"] != "active":
        raise HTTPException(422, "course is archived")
    row = conn.execute(
        "INSERT INTO enrolments (institution_id, student_id, course_id) VALUES (%s, %s, %s) "
        "ON CONFLICT (student_id, course_id) DO UPDATE SET status = 'active' RETURNING id, status",
        (p.institution_id, body.student_id, course_id),
    ).fetchone()
    audit.record(conn, p, "enrolment.created", "enrolment", row["id"],
                 {"course_id": str(course_id), "student_id": str(body.student_id)})
    return row


@router.patch("/enrolments/{enrolment_id}")
def update_enrolment(enrolment_id: UUID, body: EnrolPatch, p: Principal = Depends(require_user),
                     conn: Connection = Depends(tenant_conn)):
    e = conn.execute("SELECT course_id FROM enrolments WHERE id = %s", (enrolment_id,)).fetchone()
    if e is None:
        raise HTTPException(404, "enrolment not found")
    access.require_course(conn, p, e["course_id"], MANAGE)
    conn.execute("UPDATE enrolments SET status = %s WHERE id = %s", (body.status, enrolment_id))
    audit.record(conn, p, "enrolment.updated", "enrolment", enrolment_id, {"status": body.status})
    return {"ok": True}
