from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VISKOPIC_", env_file=".env", extra="ignore")

    # Database. The app URL must use the viskopic_app role (no BYPASSRLS, not table owner).
    database_url: str = "postgresql://viskopic_app:viskopic_app@localhost:5432/viskopic"
    # Owner URL, used only to run migrations.
    migrate_database_url: str = "postgresql://viskopic:viskopic@localhost:5432/viskopic"

    # OIDC (Keycloak locally; the university's identity provider later).
    oidc_issuer: str = "http://localhost:18080/realms/viskopic"
    # The issuer as seen from inside Docker may differ from the browser's view.
    oidc_jwks_url: str = "http://keycloak:8080/realms/viskopic/protocol/openid-connect/certs"
    oidc_audience: str = "viskopic-api"
    # Tests can supply a static JWKS instead of fetching one.
    oidc_jwks_json: str | None = None
    platform_operator_role: str = "platform_operator"

    # Object storage (SeaweedFS locally, S3 later).
    storage_backend: str = "s3"  # "s3" or "memory" (tests)
    s3_endpoint_url: str | None = "http://s3:8333"
    s3_bucket: str = "viskopic-documents"
    s3_access_key: str = "viskopic"
    s3_secret_key: str = "viskopic-local-s3-secret"
    s3_region: str = "eu-west-2"

    # Key management. "local" wraps per-institution data keys with a master key from
    # the environment. Production replaces this with the institution's own KMS.
    key_provider: str = "local"
    local_master_key_b64: str = ""

    # Intake limits.
    max_upload_bytes: int = 10 * 1024 * 1024
    max_docx_uncompressed_bytes: int = 50 * 1024 * 1024
    extraction_timeout_seconds: int = 60
    extraction_memory_mb: int = 512

    # Analysis. Demonstration profiles link a student to a research author shipped with the
    # consistency model, so the evidence report can be tried before a real baseline exists.
    # Local development only: switch off anywhere real students are involved.
    allow_demo_profiles: bool = True
    # The Δ model's website contract scores only when a reviewer has checked the language and text
    # extraction of the submission and of every baseline document. With this on, texts nobody has
    # reviewed yet are still scored, labelled "unreviewed demonstration". Known failures and
    # out-of-date reviews stay blocked either way. Local development only: keep it off elsewhere.
    allow_unreviewed_consistency: bool = False

    cors_origins: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
