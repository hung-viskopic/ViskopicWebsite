"""Authentication: verify OIDC access tokens and resolve who is calling.

Two kinds of caller:
- Institution users: a row in `users`, matched by SSO subject (or by verified email
  on first login after an invitation). All their requests run in their institution's
  tenant context.
- Platform operators (Viskopic staff): identified by a realm role in the token. They
  get no tenant context at all, so tenant tables return no rows to them.
"""
import json
from dataclasses import dataclass, field
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status

from .config import get_settings
from .db import system_tx, tenant_tx


@dataclass(frozen=True)
class RoleAssignment:
    id: UUID
    role: str
    scope_type: str
    scope_id: UUID


@dataclass
class Principal:
    subject: str
    email: str | None
    is_operator: bool = False
    user_id: UUID | None = None
    institution_id: UUID | None = None
    roles: list[RoleAssignment] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.email or self.subject


class _StaticJwks:
    def __init__(self, jwks: dict):
        self.keys = {k["kid"]: jwt.PyJWK(k) for k in jwks["keys"]}

    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        kid = jwt.get_unverified_header(token).get("kid")
        if kid not in self.keys:
            raise jwt.InvalidTokenError("unknown signing key")
        return self.keys[kid]


@lru_cache
def _jwks_client():
    s = get_settings()
    if s.oidc_jwks_json:
        return _StaticJwks(json.loads(s.oidc_jwks_json))
    return jwt.PyJWKClient(s.oidc_jwks_url, cache_keys=True, lifespan=300)


def verify_token(token: str) -> dict:
    s = get_settings()
    try:
        key = _jwks_client().get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            key.key,
            algorithms=["RS256", "ES256"],
            audience=s.oidc_audience,
            issuer=s.oidc_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}") from e


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    return header[7:].strip()


def load_roles(institution_id: UUID, user_id: UUID) -> list[RoleAssignment]:
    with tenant_tx(institution_id) as conn:
        rows = conn.execute(
            "SELECT id, role, scope_type, scope_id FROM role_assignments "
            "WHERE user_id = %s AND revoked_at IS NULL",
            (user_id,),
        ).fetchall()
    return [RoleAssignment(**r) for r in rows]


def get_principal(request: Request) -> Principal:
    claims = verify_token(_bearer(request))
    s = get_settings()
    realm_roles = claims.get("realm_access", {}).get("roles", [])
    email = claims.get("email")
    if s.platform_operator_role in realm_roles:
        return Principal(subject=claims["sub"], email=email, is_operator=True)

    with system_tx() as conn:
        row = conn.execute(
            "SELECT * FROM auth_resolve_user(%s, %s, %s)",
            (claims["sub"], email, bool(claims.get("email_verified"))),
        ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no Viskopic account for this login; ask your institution administrator for an invitation")
    if row["status"] != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account disabled")
    if row["institution_status"] != "active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "institution suspended")
    p = Principal(subject=claims["sub"], email=email, user_id=row["user_id"], institution_id=row["institution_id"])
    p.roles = load_roles(p.institution_id, p.user_id)
    return p


def require_user(p: Principal = Depends(get_principal)) -> Principal:
    if p.is_operator or p.institution_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "platform operators have no access to institution data")
    return p


def require_operator(p: Principal = Depends(get_principal)) -> Principal:
    if not p.is_operator:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "platform operator only")
    return p
