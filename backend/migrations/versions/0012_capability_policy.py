"""Capability policy, and the end of the unscoped job read.

Two hardening changes and one new table.

`agent_capability_policy` gives BR-AI-30 the shape it asks for: one row per `(organization,
capability, entity_type, action)`. There is no default row and no seed — **absence is denial**
(ADR-0047). A capability that is off when it should be on is a support ticket; one that is on when
it should be off is an AI writing into somebody's organization without anybody having decided it
should, and those failures are not worth treating symmetrically.

The `job` policies from 0011 are replaced. Checkpoint 8 keyed the worker's exemption on *the absence
of an organization setting*, so any session that forgot to scope could read the queue. It is now
keyed on identity: `workos_worker`, a role that does nothing else (ADR-0046). Every other role gets
strict tenant isolation back, and `test_an_unscoped_session_sees_nothing` covers the queue again.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

WORKER_ROLE = "workos_worker"

#: Every table the worker touches while executing an approved Proposal. It needs the same
#: privileges the application role has — and is subject to the same policies, because it holds no
#: exemption anywhere but `job`.
WORKER_TABLES = (
    ("organization", "SELECT"),
    ("person", "SELECT"),
    ("team", "SELECT"),
    ("department", "SELECT"),
    ("role_assignment", "SELECT"),
    ("team_membership", "SELECT"),
    ("external_identity", "SELECT"),
    ("organization_membership", "SELECT"),
    ("project", "SELECT, INSERT, UPDATE"),
    ("milestone", "SELECT, INSERT, UPDATE"),
    ("work", "SELECT, INSERT, UPDATE"),
    ("work_assignment", "SELECT, INSERT, UPDATE"),
    ("dependency", "SELECT, INSERT, UPDATE"),
    ("commitment", "SELECT, INSERT, UPDATE"),
    ("event", "SELECT"),
    ("event_participant", "SELECT"),
    ("event_attachment", "SELECT"),
    ("evidence", "SELECT, INSERT, UPDATE"),
    ("proposal", "SELECT, INSERT, UPDATE"),
    ("proposal_evidence", "SELECT, INSERT"),
    ("proposed_change", "SELECT, INSERT"),
    ("approval_record", "SELECT, INSERT, UPDATE"),
    ("ai_interaction", "SELECT, INSERT, UPDATE"),
    ("tool_call", "SELECT, INSERT"),
    ("agent_capability_policy", "SELECT"),
    ("audit_entry", "SELECT, INSERT"),
    ("outbox", "SELECT, INSERT, UPDATE"),
    ("idempotency_record", "SELECT, INSERT"),
    ("job", "SELECT, INSERT, UPDATE"),
    ("tenant_scoped_table", "SELECT"),
)


def upgrade() -> None:
    # ------------------------------------------------------------------ similarity (BR-AI-05)
    # Trigram matching over Work titles, so `find_similar` is a deterministic SQL query rather than
    # a vector index. Determinism is what the rule needs: BR-AI-05 asks "did you look", and an
    # answer that varies between runs makes the check unreproducible. A vector store would improve
    # recall and is not required to make the rule enforceable, so it is not introduced on
    # speculation.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # GIN over the title, so the search does not degrade into a sequential scan the moment an
    # organization has real data in it.
    op.execute(
        "CREATE INDEX ix_work_title_trgm ON work USING gin (title gin_trgm_ops)"
    )

    # ------------------------------------------------------------------ capability policy
    op.execute(
        """
        CREATE TABLE agent_capability_policy (
            id            uuid PRIMARY KEY,
            org_id        uuid NOT NULL REFERENCES organization (id),
            capability    text NOT NULL,
            entity_type   text NOT NULL,
            action        text NOT NULL,
            mode          text NOT NULL,
            reason        text,
            decided_by_person_id uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            version       integer NOT NULL DEFAULT 1,

            -- BR-AI-31. The MVP ceiling is level 2, and values above it are not representable —
            -- not disabled, not guarded by a check somewhere in Python, absent from the type.
            CONSTRAINT agent_policy_mode_valid CHECK (mode IN (
                'off', 'level_1_propose', 'level_2_approved_execution')),
            CONSTRAINT agent_policy_capability_valid CHECK (capability IN (
                'extract', 'link_evidence', 'explain', 'answer')),
            CONSTRAINT agent_policy_decided_by_fk
                FOREIGN KEY (org_id, decided_by_person_id) REFERENCES person (org_id, id),
            CONSTRAINT agent_policy_org_id_unique UNIQUE (org_id, id)
        );
        """
    )
    # One row per cell. A second row for the same cell would make "what is the policy" a question
    # with two answers, which is how a permission table becomes unauditable.
    op.execute(
        """
        CREATE UNIQUE INDEX ux_agent_policy_cell
        ON agent_capability_policy (org_id, capability, entity_type, action)
        """
    )
    op.execute(
        "CREATE INDEX ix_agent_policy_enabled ON agent_capability_policy (org_id, capability) "
        "WHERE mode <> 'off'"
    )

    op.execute("ALTER TABLE agent_capability_policy ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_capability_policy FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY agent_capability_policy_tenant_isolation ON agent_capability_policy
        USING (org_id = app_current_org())
        WITH CHECK (org_id = app_current_org());
        """
    )
    op.execute("SELECT app_grant('agent_capability_policy', 'SELECT, INSERT, UPDATE')")
    op.execute(
        "INSERT INTO tenant_scoped_table (table_name, added_in) VALUES "
        "('agent_capability_policy', '0012')"
    )

    # ------------------------------------------------------------------ job isolation (ADR-0046)
    op.execute("DROP POLICY IF EXISTS job_read_for_worker ON job")
    op.execute("DROP POLICY IF EXISTS job_claim_for_worker ON job")
    op.execute("DROP POLICY IF EXISTS job_enqueue_is_scoped ON job")

    # The exemption is now an identity rather than a state. `current_user` cannot be set by a
    # request, a header or a forgotten `set_config`; it is who the connection authenticated as.
    op.execute(
        f"""
        CREATE POLICY job_tenant_isolation ON job
        USING (org_id = app_current_org() OR current_user = '{WORKER_ROLE}')
        WITH CHECK (org_id = app_current_org() OR current_user = '{WORKER_ROLE}');
        """
    )

    # ------------------------------------------------------------------ worker grants
    for table, privileges in WORKER_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{WORKER_ROLE}')
                   AND EXISTS (
                       SELECT 1 FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_name = '{table}'
                   )
                THEN
                    EXECUTE 'GRANT {privileges} ON {table} TO {WORKER_ROLE}';
                END IF;
            END
            $$;
            """
        )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{WORKER_ROLE}') THEN
                EXECUTE 'GRANT USAGE ON SCHEMA public TO {WORKER_ROLE}';
                EXECUTE 'GRANT EXECUTE ON FUNCTION app_current_org() TO {WORKER_ROLE}';
                -- The outbox's sequence, and any other the schema holds. Granted wholesale
                -- because a sequence confers no visibility: it hands out numbers and reads no
                -- rows, so the narrow-grant discipline that matters for tables buys nothing here
                -- except a migration that breaks the next time one is added.
                EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {WORKER_ROLE}';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM tenant_scoped_table WHERE table_name = 'agent_capability_policy'"
    )
    op.execute("DROP TABLE IF EXISTS agent_capability_policy")
    op.execute("DROP INDEX IF EXISTS ix_work_title_trgm")
    op.execute("DROP POLICY IF EXISTS job_tenant_isolation ON job")
    op.execute(
        """
        CREATE POLICY job_read_for_worker ON job FOR SELECT
        USING (org_id = app_current_org() OR app_current_org() IS NULL);
        """
    )
    op.execute(
        """
        CREATE POLICY job_claim_for_worker ON job FOR UPDATE
        USING (org_id = app_current_org() OR app_current_org() IS NULL)
        WITH CHECK (org_id = app_current_org() OR app_current_org() IS NULL);
        """
    )
    op.execute(
        """
        CREATE POLICY job_enqueue_is_scoped ON job FOR INSERT
        WITH CHECK (org_id = app_current_org());
        """
    )
