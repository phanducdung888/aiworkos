"""Runtime configuration. No secrets are hard-coded; everything comes from the environment."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKOS_", extra="ignore")

    # Migrations and administrative tasks run as the owner role. It owns the schema but is not a
    # superuser and does not hold BYPASSRLS, because FORCE ROW LEVEL SECURITY is inert for a role
    # that has either.
    database_url: str = "postgresql+psycopg://workos_owner:workos_owner@localhost:5432/workos"
    # Normal application traffic runs as a role that owns nothing, so that RLS actually applies.
    app_database_url: str = "postgresql+psycopg://workos_app:workos_app@localhost:5432/workos"
    # The worker connects as its own role (ADR-0046). It holds one extra policy, on `job`, so it
    # can claim work before it knows which tenant the work belongs to; on every other table it is
    # exactly as constrained as the application role. Separate credentials rather than a shared
    # one, so the queue exemption cannot be reached by anything serving an HTTP request.
    worker_database_url: str = (
        "postgresql+psycopg://workos_worker:workos_worker@localhost:5432/workos"
    )

    sql_echo: bool = False

    # --- OpenID Connect (Keycloak) ---
    # Validated on every request: signature against the JWKS, plus issuer, audience and expiry
    # (security-model §2). Defaults point at the Compose realm so a developer who has run
    # `make up` needs no configuration; nothing here is a secret.
    oidc_issuer: str = "http://localhost:8080/realms/workos"
    oidc_audience: str = "workos-api"
    oidc_jwks_url: str = "http://localhost:8080/realms/workos/protocol/openid-connect/certs"
    #: How long a fetched JWKS is trusted before it is refetched. A key rotation is still picked up
    #: immediately, because an unknown `kid` forces a refetch regardless of this window.
    oidc_jwks_ttl_seconds: int = 600

    # --- Object storage (MinIO / S3), ADR-0039 ---
    # Attachment bytes never pass through this process: the API issues short-lived presigned URLs
    # and the client transfers directly. Defaults point at the Compose MinIO so `make up` needs no
    # configuration; the credentials here are the same development ones Compose starts with and are
    # overridden by the environment anywhere that matters.
    s3_endpoint_url: str = "http://localhost:9000"
    #: The address a *client* uses. Empty means "the same one this process uses", which is right
    #: for a single-address deployment and wrong the moment the store sits behind a proxy — a
    #: presigned URL cannot be rewritten after signing, because the signature covers the host
    #: (ADR-0063).
    s3_public_endpoint_url: str = ""
    s3_access_key: str = "workos"
    s3_secret_key: str = "workos-dev-secret"
    s3_bucket: str = "workos-attachments"
    s3_region: str = "us-east-1"

    # --- LLM provider (ADR-0049) ---
    # A configuration boundary, not a credential store. The key is read from the environment and
    # never appears in a domain type, an AIInteraction or a log line; there is no default and no
    # value in the repository. An empty key means no real provider is configured, which is the
    # state every environment is in until somebody sets one.
    llm_provider: str = "fake"
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-5"
    llm_timeout_seconds: float = 30.0

    # A second provider behind the same contract (CP13). Deliberately parallel keys rather than a
    # redesign of `llm_*`: a provider registry is the right shape and is a change of its own, and
    # doing it here would mean touching configuration that three checkpoints already depend on.
    # Neither key has a value in the repository; both come from the environment or not at all.
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"


@lru_cache
def get_settings() -> Settings:
    return Settings()
