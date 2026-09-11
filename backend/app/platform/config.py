"""Runtime configuration. No secrets are hard-coded; everything comes from the environment."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKOS_", extra="ignore")

    # Migrations and administrative tasks run as the owner role.
    database_url: str = "postgresql+psycopg://workos:workos@localhost:5432/workos"
    # Normal application traffic runs as a non-superuser role so that RLS actually applies.
    app_database_url: str = "postgresql+psycopg://workos_app:workos_app@localhost:5432/workos"

    sql_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
