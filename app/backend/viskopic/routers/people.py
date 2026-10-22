"""Users, role assignments (delegated administration) and support grants."""
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection
from pydantic import BaseModel, EmailStr, Field

from .. import access, audit
from ..auth import Principal, require_user
from ..deps import tenant_conn

router = APIRouter(tags=["people"])

Role = Literal["institution_owner", "department_admin", "reviewer", "viewer", "appeals_reviewer"]
ScopeType = Literal["institution", "department", "course"]


class UserIn(BaseModel):
    email: EmailStr
    display_name: str | None = Field(default=None, max_length=200)


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    status: Literal["active", "disabled"] | None = None


class GrantIn(BaseModel):
    user_id: UUID
    role: Role
    scope_type: ScopeType
    scope_id: UUID


class SupportGrantIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)
    hours: int = Field(default=24, ge=1, le=168)


@router.get("/users")
def list_users(p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    if not access.can_manage_people(p):
        raise access.forbid()
    users = conn.execute(
        "SELECT id, email, display_name, status, created_at, last_login_at FROM users ORDER BY lower(email)"
    ).fetchall()
    grants = conn.execute(
        "SELECT id, user_id, role, scope_type, scope_id, granted_at FROM role_assignments WHERE revoked_at IS NULL"
    ).fetchall()
    by_user: dict = {}
    for g in grants:
        by_user.setdefault(g["user_id"], []).append(g)
    for u in users:
        u["roles"] = by_user.get(u["id"], [])
    return users


@router.post("/users", status_code=201)
def invite_user(body: UserIn, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    if not access.can_manage_people(p):
        raise access.forbid()
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO users (institution_id, email, display_name, status, invited_by) "
                "VALUES (%s, lower(%s), %s, 'invited', %s) RETURNING id, email, status",
                (p.institution_id, body.email, body.display_name, p.user_id),
            ).fetchone()
    except psycopg.errors.UniqueViolation as e:
        raise HTTPException(409, "a user with this email already exists") from e
    audit.record(conn, p, "user.invited", "user", row["id"], {"email": row["email"]})
    return row


@router.patch("/users/{user_id}")
def update_user(user_id: UUID, body: UserPatch, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    if body.status is not None and not access.is_owner(p):
        raise access.forbid("only institution owners can enable or disable accounts")
    if not access.can_manage_people(p):
        raise access.forbid()
    if body.status == "disabled" and user_id == p.user_id:
        raise HTTPException(422, "you cannot disable your own account")
    current = conn.execute("SELECT status, sso_subject FROM users WHERE id = %s", (user_id,)).fetchone()
    if current is None:
        raise HTTPException(404, "user not found")
    new_status = body.status
    if new_status == "active" and current["sso_subject"] is None:
        new_status = "invited"  # re-enabled before first login
    row = conn.execute(
        "UPDATE users SET display_name = COALESCE(%s, display_name), status = COALESCE(%s, status) "
        "WHERE id = %s RETURNING id, email, display_name, status",
        (body.display_name, new_status, user_id),
    ).fetchone()
    audit.record(conn, p, "user.updated", "user", user_id, body.model_dump(exclude_none=True))
    return row


@router.post("/role-assignments", status_code=201)
def grant_role(body: GrantIn, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    access.validate_scope(conn, body.role, body.scope_type, body.scope_id, p.institution_id)
    if not access.can_grant(conn, p, body.role, body.scope_type, body.scope_id):
        raise access.forbid("you can only grant roles within your own scope")
    if conn.execute("SELECT 1 FROM users WHERE id = %s", (body.user_id,)).fetchone() is None:
        raise HTTPException(404, "user not found")
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO role_assignments (institution_id, user_id, role, scope_type, scope_id, granted_by) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (p.institution_id, body.user_id, body.role, body.scope_type, body.scope_id, p.user_id),
            ).fetchone()
    except psycopg.errors.UniqueViolation as e:
        raise HTTPException(409, "this user already has that role") from e
    audit.record(conn, p, "role.granted", "user", body.user_id, body.model_dump(mode="json"))
    return row


@router.delete("/role-assignments/{assignment_id}")
def revoke_role(assignment_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    ra = conn.execute(
        "SELECT * FROM role_assignments WHERE id = %s AND revoked_at IS NULL FOR UPDATE", (assignment_id,)
    ).fetchone()
    if ra is None:
        raise HTTPException(404, "role assignment not found")
    if not access.can_grant(conn, p, ra["role"], ra["scope_type"], ra["scope_id"]):
        raise access.forbid("you can only revoke roles within your own scope")
    if ra["role"] == "institution_owner":
        owners = conn.execute(
            "SELECT count(*) AS n FROM role_assignments WHERE role = 'institution_owner' AND revoked_at IS NULL"
        ).fetchone()["n"]
        if owners <= 1:
            raise HTTPException(422, "an institution must keep at least one owner; add another owner first")
    conn.execute(
        "UPDATE role_assignments SET revoked_at = now(), revoked_by = %s WHERE id = %s", (p.user_id, assignment_id)
    )
    audit.record(conn, p, "role.revoked", "user", ra["user_id"],
                 {"role": ra["role"], "scope_type": ra["scope_type"], "scope_id": str(ra["scope_id"])})
    return {"ok": True}


@router.get("/support-grants")
def list_support_grants(p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    if not access.is_owner(p):
        raise access.forbid()
    return conn.execute(
        "SELECT g.*, u.email AS granted_by_email FROM support_grants g JOIN users u ON u.id = g.granted_by "
        "ORDER BY g.created_at DESC"
    ).fetchall()


@router.post("/support-grants", status_code=201)
def create_support_grant(body: SupportGrantIn, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    if not access.is_owner(p):
        raise access.forbid("only institution owners can grant support access")
    expires = datetime.now(timezone.utc) + timedelta(hours=body.hours)
    row = conn.execute(
        "INSERT INTO support_grants (institution_id, granted_by, reason, expires_at) VALUES (%s, %s, %s, %s) "
        "RETURNING id, expires_at",
        (p.institution_id, p.user_id, body.reason, expires),
    ).fetchone()
    audit.record(conn, p, "support.granted", "support_grant", row["id"], {"hours": body.hours, "reason": body.reason})
    return row


@router.delete("/support-grants/{grant_id}")
def revoke_support_grant(grant_id: UUID, p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    if not access.is_owner(p):
        raise access.forbid()
    n = conn.execute(
        "UPDATE support_grants SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL", (grant_id,)
    ).rowcount
    if not n:
        raise HTTPException(404, "active grant not found")
    audit.record(conn, p, "support.revoked", "support_grant", grant_id)
    return {"ok": True}


@router.get("/audit")
def audit_log(
    limit: int = 100,
    before_id: int | None = None,
    p: Principal = Depends(require_user),
    conn: Connection = Depends(tenant_conn),
):
    if not access.is_owner(p):
        raise access.forbid("only institution owners can view the audit log")
    limit = max(1, min(limit, 500))
    return conn.execute(
        "SELECT id, actor_label, action, target_type, target_id, details, at FROM audit_log "
        "WHERE (%s::bigint IS NULL OR id < %s) ORDER BY id DESC LIMIT %s",
        (before_id, before_id, limit),
    ).fetchall()
