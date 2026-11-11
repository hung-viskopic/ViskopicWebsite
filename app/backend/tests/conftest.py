"""Test setup: a real Postgres database (so row-level security is actually exercised),
in-memory object storage, and locally signed OIDC tokens.

Point VISKOPIC_TEST_ADMIN_URL at a Postgres superuser connection, e.g. the compose
database:  postgresql://viskopic:viskopic@localhost:15432/postgres

The tests connect as the cluster-wide role `viskopic_app`. On a fresh server they create it with the
password "app". If it already exists (the compose database creates it at init with VISKOPIC_APP_PASSWORD
from app/.env), set VISKOPIC_TEST_APP_PASSWORD to its password; the tests never change an existing role.
"""
import base64
import io
import json
import os
import time
import uuid
import zipfile
from collections.abc import Mapping

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

ADMIN_URL = os.environ.get("VISKOPIC_TEST_ADMIN_URL", "postgresql://postgres@127.0.0.1:55432/postgres")
DB = "viskopic_test"
ISSUER = "http://test-issuer/realms/viskopic"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_key.public_key()))
_jwk.update({"kid": "test", "alg": "RS256", "use": "sig"})


def _url(base: str, user: str, password: str, db: str) -> str:
    rest = base.split("@", 1)[1].rsplit("/", 1)[0]
    return f"postgresql://{user}:{password}@{rest}/{db}"


APP_PASSWORD = os.environ.get("VISKOPIC_TEST_APP_PASSWORD", "app")
OWNER_URL = _url(ADMIN_URL, "viskopic_owner_test", "owner", DB)
APP_URL = _url(ADMIN_URL, "viskopic_app", APP_PASSWORD, DB)

os.environ.update({
    "VISKOPIC_DATABASE_URL": APP_URL,
    "VISKOPIC_MIGRATE_DATABASE_URL": OWNER_URL,
    "VISKOPIC_OIDC_ISSUER": ISSUER,
    "VISKOPIC_OIDC_JWKS_JSON": json.dumps({"keys": [_jwk]}),
    "VISKOPIC_STORAGE_BACKEND": "memory",
    "VISKOPIC_LOCAL_MASTER_KEY_B64": base64.b64encode(os.urandom(32)).decode(),
})


@pytest.fixture(scope="session", autouse=True)
def database():
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
        for role, pw in (("viskopic_owner_test", "owner"), ("viskopic_app", APP_PASSWORD)):
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                c.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{pw}' NOSUPERUSER NOBYPASSRLS")
        c.execute(f"CREATE DATABASE {DB} OWNER viskopic_owner_test")
    # Fail fast with a clear message instead of a pool timeout in every test.
    try:
        psycopg.connect(_url(ADMIN_URL, "viskopic_app", APP_PASSWORD, DB), connect_timeout=5).close()
    except psycopg.OperationalError as e:
        pytest.exit(f"cannot connect as viskopic_app ({e}). If the role already exists on this server, "
                    "set VISKOPIC_TEST_APP_PASSWORD to its password (compose: VISKOPIC_APP_PASSWORD in app/.env).",
                    returncode=2)
    from viskopic.migrate import migrate

    assert migrate(OWNER_URL)
    yield
    from viskopic.db import close_pool

    close_pool()


@pytest.fixture(scope="session")
def client(database):
    from fastapi.testclient import TestClient

    from viskopic.main import app

    return TestClient(app)


def token(sub: str, email: str | None = None, roles: list[str] | None = None, verified: bool = True) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": "viskopic-api", "sub": sub, "iat": now, "exp": now + 600,
        "email": email, "email_verified": verified, "realm_access": {"roles": roles or []},
    }
    return jwt.encode(claims, _key, algorithm="RS256", headers={"kid": "test"})


def auth(sub: str, email: str | None = None, roles: list[str] | None = None, verified: bool = True) -> dict:
    return {"Authorization": f"Bearer {token(sub, email, roles, verified)}"}


class _FreshAuth(Mapping):
    """Headers that sign a new token on every request, so a long run can't outlive a module-level token."""

    def __init__(self, *args):
        self._args = args

    def _headers(self) -> dict:
        return auth(*self._args)

    def __getitem__(self, key):
        return self._headers()[key]

    def __iter__(self):
        return iter(self._headers())

    def __len__(self):
        return 1


OPERATOR = _FreshAuth("operator-1", "ops@viskopic.test", ["platform_operator"])


class Tenant:
    """An institution created through the platform API, with its owner logged in."""

    def __init__(self, client, name: str):
        slug = f"{name.lower()}-{uuid.uuid4().hex[:6]}"
        self.owner_email = f"owner@{slug}.ac.uk"
        r = client.post("/api/platform/institutions", headers=OPERATOR,
                        json={"name": name, "slug": slug, "owner_email": self.owner_email})
        assert r.status_code == 201, r.text
        self.id = r.json()["id"]
        self.slug = slug
        self.owner = auth(f"sub-{slug}-owner", self.owner_email)
        me = client.get("/api/me", headers=self.owner)
        assert me.status_code == 200, me.text

    def add_user(self, client, who: str) -> tuple[str, dict]:
        email = f"{who}@{self.slug}.ac.uk"
        r = client.post("/api/users", headers=self.owner, json={"email": email})
        assert r.status_code == 201, r.text
        headers = auth(f"sub-{self.slug}-{who}", email)
        assert client.get("/api/me", headers=headers).status_code == 200
        return r.json()["id"], headers


@pytest.fixture
def tenant_factory(client):
    return lambda name="Uni": Tenant(client, name)


def make_docx(paragraphs: list[str], hidden_white: str | None = None, edit_minutes: int = 240) -> bytes:
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{p}</w:t></w:r></w:p>" for p in paragraphs)
    if hidden_white:
        body += ('<w:p><w:r><w:rPr><w:color w:val="FFFFFF"/><w:sz w:val="2"/></w:rPr>'
                 f"<w:t>{hidden_white}</w:t></w:r></w:p>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')
        z.writestr("docProps/core.xml",
                   '<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">'
                   "<dc:creator>Test Student</dc:creator><cp:revision>7</cp:revision></cp:coreProperties>")
        z.writestr("docProps/app.xml",
                   '<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                   f"<TotalTime>{edit_minutes}</TotalTime><Application>Microsoft Office Word</Application></Properties>")
    return buf.getvalue()


def owner_conn():
    return psycopg.connect(OWNER_URL, autocommit=True)


def app_conn():
    return psycopg.connect(APP_URL, autocommit=True)
