"""AIInteraction, ToolCall and the job queue.

`ai_interaction` is the record of an AI run: what triggered it, whose authority it borrowed, which
model answered, and what it produced. It is append-mostly — the outcome fields are written when the
run finishes and nothing else changes — because BR-PR-08 requires the chain from an Event to a
mutation to be traversable, and a link that can be rewritten is not a link.

`tool_call` records every call the runtime made, **including the rejected ones**. Those are the
interesting rows: a refusal is the authority model working, and a table that only kept successes
would make the one thing worth auditing invisible.

`job` is the execution queue (ADR-0044). Claimed with `FOR UPDATE SKIP LOCKED` inside the same
transaction that does the work, so a crash leaves the row unclaimed and retryable rather than lost.

Deliberately absent: any column that could hold a credential, an API key, or a raw prompt payload.
`arguments_redacted` is the shape of a tool call, not its contents.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

#: An interaction's outcome, written once when the run ends. Everything else — what triggered it,
#: whose authority it used, which model and prompt — is frozen, because those are what an auditor
#: reads to decide whether the run should have happened at all.
INTERACTION_MUTABLE = (
    "status",
    "finished_at",
    # Not known until the provider answers: a routed request may land on a different model version
    # than the one asked for, and recording the requested one would be a lie about which model
    # produced the output. Everything that decides *whether the run should have happened* — the
    # trigger, the delegated principal, the pinned prompt — stays frozen.
    "model",
    "model_version",
    "latency_ms",
    "token_usage",
    "cost_estimate",
    "output_summary",
    "error",
    "version",
)


def upgrade() -> None:
    # ------------------------------------------------------------------ ai_interaction
    op.execute(
        """
        CREATE TABLE ai_interaction (
            id                   uuid PRIMARY KEY,
            org_id               uuid NOT NULL REFERENCES organization (id),
            kind                 text NOT NULL,
            trigger_type         text NOT NULL,
            trigger_ref          uuid,
            -- BR-AI-03. The human whose authority this run borrowed. Null only for a system-scope
            -- agent, which by construction can reach nothing a person could not.
            principal_person_id  uuid,
            agent_identity       text NOT NULL,
            runtime              text NOT NULL,
            provider             text NOT NULL,
            model                text NOT NULL,
            model_version        text NOT NULL,
            prompt_id            text NOT NULL,
            prompt_version       text NOT NULL,
            tool_manifest_version text NOT NULL,
            input_refs           jsonb NOT NULL DEFAULT '{}'::jsonb,
            status               text NOT NULL DEFAULT 'running',
            started_at           timestamptz NOT NULL DEFAULT now(),
            finished_at          timestamptz,
            latency_ms           integer,
            token_usage          jsonb,
            cost_estimate        numeric(12, 6),
            output_summary       jsonb,
            error                text,
            correlation_id       text,
            version              integer NOT NULL DEFAULT 1,

            CONSTRAINT ai_interaction_kind_valid CHECK (kind IN (
                'extraction', 'linking', 'monitoring_explanation', 'summarisation',
                'question_answer', 'evaluation')),
            CONSTRAINT ai_interaction_trigger_valid CHECK (trigger_type IN (
                'event', 'schedule', 'user_request', 'retry')),
            CONSTRAINT ai_interaction_status_valid CHECK (status IN (
                'running', 'succeeded', 'failed', 'rejected', 'timed_out')),
            -- A prompt is pinned, never "latest": a run that cannot say which prompt produced it
            -- is not reproducible, and BR-AI-32's promotion metrics would be measuring nothing.
            CONSTRAINT ai_interaction_prompt_is_pinned
                CHECK (prompt_version <> 'latest' AND length(btrim(prompt_version)) > 0),
            CONSTRAINT ai_interaction_finished_has_a_time CHECK (
                status = 'running' OR finished_at IS NOT NULL),
            CONSTRAINT ai_interaction_principal_fk
                FOREIGN KEY (org_id, principal_person_id) REFERENCES person (org_id, id),
            CONSTRAINT ai_interaction_org_id_unique UNIQUE (org_id, id)
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_ai_interaction_trigger "
        "ON ai_interaction (org_id, trigger_type, trigger_ref)"
    )
    op.execute(
        "CREATE INDEX ix_ai_interaction_started ON ai_interaction (org_id, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_ai_interaction_running ON ai_interaction (org_id, started_at) "
        "WHERE status = 'running'"
    )

    # ------------------------------------------------------------------ tool_call
    op.execute(
        """
        CREATE TABLE tool_call (
            id                   uuid PRIMARY KEY,
            org_id               uuid NOT NULL REFERENCES organization (id),
            ai_interaction_id    uuid NOT NULL,
            sequence             integer NOT NULL,
            tool_name            text NOT NULL,
            tool_version         text NOT NULL,
            -- The *shape* of the call: argument names and types, never their values. A tool call
            -- carrying a quoted message would put the message in a second place with a different
            -- retention story (BR-E-07).
            arguments_redacted   jsonb NOT NULL DEFAULT '{}'::jsonb,
            authorization_result text NOT NULL,
            outcome              text NOT NULL,
            target_entity_type   text,
            target_entity_id     uuid,
            duration_ms          integer,
            error                text,
            created_at           timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT tool_call_authorization_valid
                CHECK (authorization_result IN ('allowed', 'denied')),
            CONSTRAINT tool_call_outcome_valid
                CHECK (outcome IN ('succeeded', 'failed', 'refused', 'not_attempted')),
            -- A denied call must not claim to have done anything. This is the row an auditor reads.
            CONSTRAINT tool_call_denied_did_nothing CHECK (
                authorization_result = 'allowed' OR outcome IN ('refused', 'not_attempted')),
            CONSTRAINT tool_call_interaction_fk
                FOREIGN KEY (org_id, ai_interaction_id)
                REFERENCES ai_interaction (org_id, id)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_tool_call_sequence "
        "ON tool_call (org_id, ai_interaction_id, sequence)"
    )
    op.execute(
        "CREATE INDEX ix_tool_call_denied ON tool_call (org_id, created_at DESC) "
        "WHERE authorization_result = 'denied'"
    )

    # ------------------------------------------------------------------ provenance links
    # BR-AI-02. An AI-originated Proposal carries its interaction; so does AI-produced Evidence.
    # Added as columns rather than a join table because the relationship is one-to-many from the
    # interaction and every row has at most one origin.
    op.execute("ALTER TABLE proposal ADD COLUMN ai_interaction_id uuid")
    op.execute(
        "ALTER TABLE proposal ADD CONSTRAINT proposal_ai_interaction_fk "
        "FOREIGN KEY (org_id, ai_interaction_id) REFERENCES ai_interaction (org_id, id)"
    )
    op.execute(
        "CREATE INDEX ix_proposal_ai_interaction ON proposal (org_id, ai_interaction_id) "
        "WHERE ai_interaction_id IS NOT NULL"
    )
    # Evidence gets no such column. `produced_by_type`/`produced_by_id` already carry it
    # polymorphically (domain-model §Evidence), and Signal is upstream of Intelligence in the
    # context order (ADR-0040) — a foreign key here would point down the order and make the capture
    # schema depend on the AI layer's. The index below is what makes the polymorphic lookup usable.
    op.execute(
        "CREATE INDEX ix_evidence_produced_by "
        "ON evidence (org_id, produced_by_type, produced_by_id)"
    )
    # `proposal` is frozen by a subtract-the-allow-list trigger (ADR-0038), so the column added
    # above is immutable by construction — which is what a provenance link should be.

    # ------------------------------------------------------------------ job queue (ADR-0044)
    op.execute(
        """
        CREATE TABLE job (
            id             uuid PRIMARY KEY,
            org_id         uuid NOT NULL REFERENCES organization (id),
            kind           text NOT NULL,
            payload        jsonb NOT NULL DEFAULT '{}'::jsonb,
            status         text NOT NULL DEFAULT 'pending',
            attempts       integer NOT NULL DEFAULT 0,
            max_attempts   integer NOT NULL DEFAULT 5,
            run_after      timestamptz NOT NULL DEFAULT now(),
            started_at     timestamptz,
            finished_at    timestamptz,
            last_error     text,
            -- What makes a job idempotent to enqueue. Two requests to execute the same approval
            -- produce one row, so a double-click cannot become two queued mutations.
            dedupe_key     text,
            created_at     timestamptz NOT NULL DEFAULT now(),
            version        integer NOT NULL DEFAULT 1,

            CONSTRAINT job_status_valid CHECK (status IN (
                'pending', 'running', 'succeeded', 'failed', 'dead')),
            CONSTRAINT job_attempts_non_negative CHECK (attempts >= 0),
            CONSTRAINT job_finished_has_a_time CHECK (
                status IN ('pending', 'running') OR finished_at IS NOT NULL),
            -- A job that has exhausted its attempts is `dead` and is not retried. It is kept, so
            -- somebody can see what stopped rather than discovering a gap.
            CONSTRAINT job_dead_is_exhausted CHECK (
                status <> 'dead' OR attempts >= max_attempts)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_job_dedupe ON job (org_id, kind, dedupe_key) "
        "WHERE dedupe_key IS NOT NULL AND status IN ('pending', 'running')"
    )
    # The claim query reads exactly this: runnable, oldest first.
    op.execute(
        "CREATE INDEX ix_job_runnable ON job (run_after, id) WHERE status = 'pending'"
    )
    op.execute("CREATE INDEX ix_job_org_status ON job (org_id, status)")

    # ------------------------------------------------------------------ RLS
    for table in ("ai_interaction", "tool_call", "job"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"SELECT app_grant('{table}', 'SELECT, INSERT, UPDATE')")

    for table in ("ai_interaction", "tool_call"):
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (org_id = app_current_org())
            WITH CHECK (org_id = app_current_org());
            """
        )

    # `job` is the one deliberate exception to default-deny, and it is split by command so the
    # exception is as small as it can be (ADR-0044).
    #
    # A worker serves every tenant. It cannot know which organization has work before it looks, and
    # no role in this system holds BYPASSRLS — `test_neither_role_can_bypass_row_level_security`
    # asserts that and must keep passing. So the *claim* must be able to run unscoped, and the
    # worker sets the organization from the row it claimed before any handler runs.
    #
    # Writing is not exempt. INSERT still requires a scope, so a job can only be enqueued from
    # inside a request that already established one; a worker cannot manufacture work for a tenant.
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

    op.execute(
        "INSERT INTO tenant_scoped_table (table_name, added_in) VALUES "
        "('ai_interaction', '0011'), ('tool_call', '0011'), ('job', '0011')"
    )

    # ------------------------------------------------------------------ immutability
    keys = ", ".join(f"'{column}'" for column in INTERACTION_MUTABLE)
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION ai_interaction_is_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            frozen_old jsonb;
            frozen_new jsonb;
        BEGIN
            IF TG_OP <> 'UPDATE' THEN
                RAISE EXCEPTION 'ai_interaction is immutable (BR-PR-08): % is not permitted', TG_OP
                    USING ERRCODE = 'restrict_violation';
            END IF;
            frozen_old := to_jsonb(OLD) - ARRAY[{keys}];
            frozen_new := to_jsonb(NEW) - ARRAY[{keys}];
            IF frozen_old <> frozen_new THEN
                RAISE EXCEPTION
                    'ai_interaction is immutable (BR-PR-08); a rerun is a new interaction'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_ai_interaction_immutable BEFORE UPDATE OR DELETE ON ai_interaction "
        "FOR EACH ROW EXECUTE FUNCTION ai_interaction_is_immutable()"
    )
    # `tool_call` is append-only outright: there is no field on a recorded call that should ever
    # change, and a rewritten denial is the audit failure this whole table exists to prevent.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tool_call_is_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'tool_call is append-only (BR-AI-03): % is not permitted', TG_OP
                USING ERRCODE = 'restrict_violation';
        END
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_tool_call_append_only "
        "BEFORE UPDATE OR DELETE OR TRUNCATE ON tool_call "
        "FOR EACH STATEMENT EXECUTE FUNCTION tool_call_is_append_only()"
    )
    for table in ("ai_interaction",):
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {table}_no_truncate() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION '{table} is immutable: TRUNCATE is not permitted'
                    USING ERRCODE = 'restrict_violation';
            END
            $$;
            """
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {table}_no_truncate()"
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM tenant_scoped_table WHERE table_name IN "
        "('ai_interaction', 'tool_call', 'job')"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_tool_call_append_only ON tool_call")
    op.execute("DROP TRIGGER IF EXISTS trg_ai_interaction_immutable ON ai_interaction")
    op.execute("DROP TRIGGER IF EXISTS trg_ai_interaction_no_truncate ON ai_interaction")
    op.execute("DROP FUNCTION IF EXISTS tool_call_is_append_only()")
    op.execute("DROP FUNCTION IF EXISTS ai_interaction_is_immutable()")
    op.execute("DROP FUNCTION IF EXISTS ai_interaction_no_truncate()")
    op.execute("DROP INDEX IF EXISTS ix_evidence_produced_by")
    op.execute("DROP INDEX IF EXISTS ix_proposal_ai_interaction")
    op.execute("ALTER TABLE proposal DROP CONSTRAINT IF EXISTS proposal_ai_interaction_fk")
    op.execute("ALTER TABLE proposal DROP COLUMN IF EXISTS ai_interaction_id")
    for table in ("job", "tool_call", "ai_interaction"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
