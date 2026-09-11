"""Platform foundation: extensions, tenancy primitives, audit, outbox.

Revision ID: 0001
Revises:
Create Date: 2026-09-11

Establishes the invariants that everything later depends on, in one migration so they cannot be
half-applied:

* `app_current_org()` — the single function every RLS policy reads (ADR-0008).
* `organization` — the tenant root.
* `audit_entry` — append-only, enforced by trigger, not by convention (BR-G-02, BR-G-03).
* `outbox` — transactional outbox (ADR-0005).
* `workos_app` — the application role. It is not the table owner, and every org-scoped table is
  created with FORCE ROW LEVEL SECURITY so that even the owner is subject to isolation. Two
  independent mechanisms, because a single one is a single point of failure (PQ-2).
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "workos_app"


def upgrade() -> None:
    # ---------------------------------------------------------------- grant helper
    # The application role is created by environment bootstrap (ops/db/dev-roles.sql), not here.
    # Roles and credentials are an environment concern; a migration that creates them needs
    # CREATEROLE in every environment it touches, and fails in the ones that sensibly withhold it.
    # This helper makes every grant conditional, so the schema applies cleanly whether or not the
    # role exists yet.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION app_grant(target text, privileges text) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                EXECUTE format('GRANT %s ON %s TO {APP_ROLE}', privileges, target);
            END IF;
        END
        $$;
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA public TO {APP_ROLE}';
            END IF;
        END
        $$;
        """
    )

    # ---------------------------------------------------------------- org context function
    # `true` as the second argument makes a missing setting return NULL rather than raising, so an
    # unscoped session sees nothing instead of erroring. Default deny, quietly.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_current_org() RETURNS uuid
        LANGUAGE sql STABLE AS $$
            SELECT NULLIF(current_setting('app.current_org_id', true), '')::uuid
        $$;
        """
    )
    op.execute("SELECT app_grant('FUNCTION app_current_org()', 'EXECUTE')")

    # ---------------------------------------------------------------- organization
    op.execute(
        """
        CREATE TABLE organization (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name            text NOT NULL,
            slug            text NOT NULL,
            timezone        text NOT NULL DEFAULT 'UTC',
            status          text NOT NULL DEFAULT 'active',
            ai_enabled      boolean NOT NULL DEFAULT false,
            autonomy_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
            retention_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            version         integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_organization_status
                CHECK (status IN ('active', 'suspended')),
            CONSTRAINT uq_organization_slug UNIQUE (slug)
        );
        """
    )

    # ---------------------------------------------------------------- audit
    op.execute(
        """
        CREATE TABLE audit_entry (
            id                    uuid PRIMARY KEY,
            org_id                uuid NOT NULL REFERENCES organization (id),
            occurred_at           timestamptz NOT NULL DEFAULT now(),
            actor                 jsonb NOT NULL,
            action                text NOT NULL,
            resource_type         text NOT NULL,
            resource_id           uuid,
            before_state          jsonb,
            after_state           jsonb,
            request_id            text,
            authorization_context jsonb
        );
        """
    )
    op.execute("CREATE INDEX ix_audit_entry_org_id_occurred_at ON audit_entry (org_id, occurred_at \
        DESC)")
    op.execute(
        "CREATE INDEX ix_audit_entry_resource_type_resource_id "
        "ON audit_entry (resource_type, resource_id)"
    )

    # Append-only. A trigger rather than a permission grant, so it holds for the owner, for a
    # superuser, and for anyone who arrives with psql and good intentions.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_entry_is_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_entry is append-only (BR-G-03): % is not permitted', TG_OP
                USING ERRCODE = 'restrict_violation';
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_entry_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_entry
        FOR EACH STATEMENT EXECUTE FUNCTION audit_entry_is_append_only();
        """
    )

    # ---------------------------------------------------------------- outbox
    op.execute(
        """
        CREATE TABLE outbox (
            id             uuid PRIMARY KEY,
            org_id         uuid NOT NULL REFERENCES organization (id),
            occurred_at    timestamptz NOT NULL DEFAULT now(),
            type           text NOT NULL,
            aggregate_type text NOT NULL,
            aggregate_id   uuid,
            payload        jsonb NOT NULL DEFAULT '{}'::jsonb,
            actor          jsonb NOT NULL,
            correlation_id text,
            causation_id   uuid,
            published_at   timestamptz,
            attempts       integer NOT NULL DEFAULT 0,
            last_error     text
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_outbox_unpublished ON outbox (id) WHERE published_at IS NULL"
    )

    # ---------------------------------------------------------------- RLS
    for table in ("organization", "audit_entry", "outbox"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY organization_tenant_isolation ON organization
        USING (id = app_current_org())
        WITH CHECK (id = app_current_org());
        """
    )
    # Audit gets SELECT and INSERT policies and no others. Without an UPDATE or DELETE policy those
    # commands are refused by RLS before the trigger is even reached.
    op.execute(
        "CREATE POLICY audit_entry_read ON audit_entry FOR SELECT USING (org_id = \
            app_current_org())"
    )
    op.execute(
        "CREATE POLICY audit_entry_append ON audit_entry FOR INSERT "
        "WITH CHECK (org_id = app_current_org())"
    )
    op.execute(
        """
        CREATE POLICY outbox_tenant_isolation ON outbox
        USING (org_id = app_current_org())
        WITH CHECK (org_id = app_current_org());
        """
    )

    op.execute("SELECT app_grant('organization', 'SELECT, INSERT, UPDATE')")
    op.execute("SELECT app_grant('audit_entry', 'SELECT, INSERT')")
    op.execute("SELECT app_grant('outbox', 'SELECT, INSERT, UPDATE')")

    # ---------------------------------------------------------------- registry of scoped tables
    # Used by the schema-invariant architecture test. A table listed here must carry org_id and have
    # RLS enabled and forced; a table not listed must be justified in review.
    op.execute(
        """
        CREATE TABLE tenant_scoped_table (
            table_name text PRIMARY KEY,
            added_in   text NOT NULL
        );
        """
    )
    op.execute(
        "INSERT INTO tenant_scoped_table (table_name, added_in) VALUES "
        "('organization', '0001'), ('audit_entry', '0001'), ('outbox', '0001')"
    )
    op.execute("SELECT app_grant('tenant_scoped_table', 'SELECT')")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tenant_scoped_table")
    op.execute("DROP TABLE IF EXISTS outbox")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_entry_append_only ON audit_entry")
    op.execute("DROP TABLE IF EXISTS audit_entry")
    op.execute("DROP FUNCTION IF EXISTS audit_entry_is_append_only()")
    op.execute("DROP TABLE IF EXISTS organization")
    op.execute("DROP FUNCTION IF EXISTS app_current_org()")
    op.execute("DROP FUNCTION IF EXISTS app_grant(text, text)")
    # The role is not dropped: it is created by environment bootstrap, not by this migration.
