from fastapi import APIRouter, Depends
from psycopg import Connection

from .. import access
from ..auth import Principal, get_principal, require_user
from ..deps import tenant_conn

router = APIRouter(tags=["me"])


@router.get("/me")
def me(p: Principal = Depends(get_principal)):
    if p.is_operator:
        return {"kind": "platform_operator", "email": p.email, "roles": [], "permissions": {"platform": True}}
    with_names = _roles_with_names(p)
    return {
        "kind": "user",
        "user_id": p.user_id,
        "institution_id": p.institution_id,
        "email": p.email,
        "roles": with_names,
        "permissions": {
            "is_owner": access.is_owner(p),
            "manage_people": access.can_manage_people(p),
            "admin_department_ids": [str(d) for d in access.admin_department_ids(p)],
        },
    }


def _roles_with_names(p: Principal) -> list[dict]:
    from ..db import tenant_tx

    with tenant_tx(p.institution_id) as conn:
        inst = conn.execute("SELECT name FROM institutions WHERE id = %s", (p.institution_id,)).fetchone()
        out = []
        for r in p.roles:
            out.append({
                "id": r.id, "role": r.role, "scope_type": r.scope_type, "scope_id": r.scope_id,
                "scope_name": _scope_name(conn, r.scope_type, r.scope_id, inst["name"] if inst else None),
            })
        return out


def _scope_name(conn: Connection, scope_type: str, scope_id, institution_name: str | None) -> str | None:
    if scope_type == "institution":
        return institution_name
    if scope_type == "department":
        row = conn.execute("SELECT name FROM departments WHERE id = %s", (scope_id,)).fetchone()
        return row["name"] if row else None
    row = conn.execute("SELECT code || ' ' || title AS n FROM courses WHERE id = %s", (scope_id,)).fetchone()
    return row["n"] if row else None


@router.get("/institution")
def my_institution(p: Principal = Depends(require_user), conn: Connection = Depends(tenant_conn)):
    return conn.execute(
        "SELECT id, name, slug, status, kms_key_ref, created_at FROM institutions WHERE id = %s",
        (p.institution_id,),
    ).fetchone()
