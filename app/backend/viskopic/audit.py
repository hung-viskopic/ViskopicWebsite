"""Append-only audit log. The database rejects UPDATE and DELETE on audit_log."""
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb

from .auth import Principal


def record(
    conn: Connection,
    p: Principal | None,
    action: str,
    target_type: str | None = None,
    target_id: Any = None,
    details: dict | None = None,
    *,
    institution_id: UUID | None = None,
    actor_label: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (institution_id, actor_user_id, actor_label, action, target_type, target_id, details) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            institution_id or (p.institution_id if p else None),
            p.user_id if p else None,
            actor_label or (p.label if p else "system"),
            action,
            target_type,
            str(target_id) if target_id is not None else None,
            Jsonb(details or {}),
        ),
    )
