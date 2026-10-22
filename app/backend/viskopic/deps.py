from collections.abc import Iterator
from uuid import UUID

from fastapi import Depends, HTTPException
from psycopg import Connection

from . import crypto
from .auth import Principal, require_user
from .db import tenant_tx


def tenant_conn(p: Principal = Depends(require_user)) -> Iterator[Connection]:
    with tenant_tx(p.institution_id) as conn:
        yield conn


def institution_dek(conn: Connection, institution_id: UUID) -> bytes:
    row = conn.execute(
        "SELECT kms_key_ref, wrapped_dek FROM institutions WHERE id = %s", (institution_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "institution not found")
    return crypto.institution_key(institution_id, row["kms_key_ref"], row["wrapped_dek"])
