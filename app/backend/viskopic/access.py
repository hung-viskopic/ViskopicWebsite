"""Authorisation rules.

Roles are assignments of (role, scope). This module is the single place that turns
a caller's assignments into yes/no answers. Row-level security separately guarantees
that nothing outside the caller's institution is ever visible.

    institution_owner   institution   everything in the institution
    department_admin    department    courses, staff and students in that department
    reviewer            course        manage students, documents and assignments on the course
    viewer              course        read-only on the course
    appeals_reviewer    institution   read-only across courses (appeals come later)
"""
from uuid import UUID

from fastapi import HTTPException, status
from psycopg import Connection

from .auth import Principal

MANAGE = "manage"
VIEW = "view"


def forbid(msg: str = "you do not have permission for this") -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, msg)


def is_owner(p: Principal) -> bool:
    return any(r.role == "institution_owner" for r in p.roles)


def admin_department_ids(p: Principal) -> set[UUID]:
    return {r.scope_id for r in p.roles if r.role == "department_admin"}


def _course_department(conn: Connection, course_id: UUID) -> UUID | None:
    row = conn.execute("SELECT department_id FROM courses WHERE id = %s", (course_id,)).fetchone()
    return row["department_id"] if row else None


def course_access(conn: Connection, p: Principal, course_id: UUID) -> str | None:
    dept = _course_department(conn, course_id)
    if dept is None:  # not in this institution (row-level security hides it) or doesn't exist
        return None
    if is_owner(p):
        return MANAGE
    if dept in admin_department_ids(p):
        return MANAGE
    direct = {r.role for r in p.roles if r.scope_type == "course" and r.scope_id == course_id}
    if "reviewer" in direct:
        return MANAGE
    if "viewer" in direct or any(r.role == "appeals_reviewer" for r in p.roles):
        return VIEW
    return None


def require_course(conn: Connection, p: Principal, course_id: UUID, need: str = VIEW) -> None:
    level = course_access(conn, p, course_id)
    if level is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "course not found")
    if need == MANAGE and level != MANAGE:
        raise forbid("you have read-only access to this course")


def accessible_course_ids(conn: Connection, p: Principal) -> set[UUID] | None:
    """Course ids the caller can see; None means every course in the institution."""
    if is_owner(p) or any(r.role == "appeals_reviewer" for r in p.roles):
        return None
    ids = {r.scope_id for r in p.roles if r.scope_type == "course"}
    depts = admin_department_ids(p)
    if depts:
        rows = conn.execute("SELECT id FROM courses WHERE department_id = ANY(%s)", (list(depts),)).fetchall()
        ids |= {r["id"] for r in rows}
    return ids


def can_manage_department(p: Principal, department_id: UUID) -> bool:
    return is_owner(p) or department_id in admin_department_ids(p)


def can_manage_people(p: Principal) -> bool:
    return is_owner(p) or bool(admin_department_ids(p))


def can_grant(conn: Connection, p: Principal, role: str, scope_type: str, scope_id: UUID) -> bool:
    """Delegation rule: you can only grant within your own scope, and only roles
    no more powerful than your own."""
    if is_owner(p):
        return True
    depts = admin_department_ids(p)
    if not depts:
        return False
    if role == "department_admin" and scope_type == "department":
        return scope_id in depts
    if role in ("reviewer", "viewer") and scope_type == "course":
        return _course_department(conn, scope_id) in depts
    return False


def validate_scope(conn: Connection, role: str, scope_type: str, scope_id: UUID, institution_id: UUID) -> None:
    expected = {
        "institution_owner": "institution",
        "appeals_reviewer": "institution",
        "department_admin": "department",
        "reviewer": "course",
        "viewer": "course",
    }
    if expected.get(role) != scope_type:
        raise HTTPException(422, f"role {role} must be scoped to a {expected.get(role, 'valid scope')}")
    if scope_type == "institution":
        ok = scope_id == institution_id
    elif scope_type == "department":
        ok = conn.execute("SELECT 1 FROM departments WHERE id = %s", (scope_id,)).fetchone() is not None
    else:
        ok = conn.execute("SELECT 1 FROM courses WHERE id = %s", (scope_id,)).fetchone() is not None
    if not ok:
        raise HTTPException(422, "scope not found")
