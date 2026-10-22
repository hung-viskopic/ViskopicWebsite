"""Platform operator endpoints (Viskopic staff).

These only call the narrow SECURITY DEFINER functions in the database. The operator
never gets a tenant context, so there is no code path from here to students,
documents, texts or decisions.
"""
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, EmailStr, Field

from .. import crypto
from ..auth import Principal, require_operator
from ..db import system_tx

router = APIRouter(prefix="/platform", tags=["platform"])


class InstitutionIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(pattern=r"^[a-z0-9-]{2,40}$")
    owner_email: EmailStr
    # Reference to the key in the institution's KMS. Locally any label works.
    kms_key_ref: str = "local-dev"


class OwnerIn(BaseModel):
    email: EmailStr


class StatusIn(BaseModel):
    status: str = Field(pattern="^(active|suspended)$")


def _audit(conn, institution_id, action: str, details: dict) -> None:
    conn.execute("SELECT platform_audit(%s, %s, %s)", (institution_id, action, Jsonb(details)))


@router.get("/institutions")
def list_institutions(_: Principal = Depends(require_operator)):
    with system_tx() as conn:
        return conn.execute("SELECT * FROM platform_list_institutions()").fetchall()


@router.post("/institutions", status_code=201)
def create_institution(body: InstitutionIn, p: Principal = Depends(require_operator)):
    wrapped = crypto.get_key_provider().new_wrapped_key(body.kms_key_ref)
    try:
        with system_tx() as conn:
            iid = conn.execute(
                "SELECT platform_create_institution(%s, %s, %s, %s) AS id",
                (body.name, body.slug, body.kms_key_ref, wrapped),
            ).fetchone()["id"]
            owner = conn.execute("SELECT platform_invite_owner(%s, %s) AS id", (iid, body.owner_email)).fetchone()["id"]
            _audit(conn, iid, "institution.created", {"by": p.label, "owner_email": body.owner_email})
    except psycopg.errors.UniqueViolation as e:
        raise HTTPException(409, "slug or owner email already in use") from e
    return {"id": iid, "owner_user_id": owner}


@router.post("/institutions/{institution_id}/owners", status_code=201)
def invite_owner(institution_id: UUID, body: OwnerIn, p: Principal = Depends(require_operator)):
    try:
        with system_tx() as conn:
            uid = conn.execute("SELECT platform_invite_owner(%s, %s) AS id", (institution_id, body.email)).fetchone()["id"]
            _audit(conn, institution_id, "institution.owner_invited", {"by": p.label, "email": body.email})
    except psycopg.errors.RaiseException as e:
        raise HTTPException(409, str(e.diag.message_primary)) from e
    except psycopg.errors.UniqueViolation as e:
        raise HTTPException(409, "email already in use") from e
    return {"user_id": uid}


@router.post("/institutions/{institution_id}/status")
def set_status(institution_id: UUID, body: StatusIn, p: Principal = Depends(require_operator)):
    with system_tx() as conn:
        conn.execute("SELECT platform_set_institution_status(%s, %s)", (institution_id, body.status))
        _audit(conn, institution_id, f"institution.{body.status}", {"by": p.label})
    return {"ok": True}


@router.get("/institutions/{institution_id}/support/jobs")
def support_jobs(institution_id: UUID, p: Principal = Depends(require_operator)):
    try:
        with system_tx() as conn:
            rows = conn.execute("SELECT * FROM platform_support_jobs(%s)", (institution_id,)).fetchall()
            _audit(conn, institution_id, "support.jobs_viewed", {"by": p.label})
            return rows
    except psycopg.errors.RaiseException as e:
        raise HTTPException(403, "the institution has not granted support access") from e
