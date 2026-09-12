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


@lru_cache
def get_settings() -> Settings:
    return Settings()
