"""Commitment, Proposal, ProposedChange and ApprovalRecord.

Three shapes, each with a different relationship to change.

A **Commitment** is ordinary mutable state moving through BR-C-04's transitions, so it looks like
Work: a status column, a version, no trigger.

A **Proposal** is mostly frozen and partly not. Its action and its provenance are fixed at
creation because that is what somebody reviews; its status and review fields move as it is
decided. Revising a Proposal produces a *new* action and a new hash (ADR-0041), so the reviewable
content never changes underneath a decision.

An **ApprovalRecord** is frozen except for the outcome of the execution it authorised. That split is
the whole reason it exists separately from `Proposal.status` (domain-model §Proposal): status is
mutable and an approval must not be, so the thing binding a human's authority to a mutation is a row
that cannot be edited afterwards.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

#: A Proposal's decision fields. Everything else — the action, its hash, the provenance, who raised
#: it — is frozen, because those are what a reviewer read before deciding.
PROPOSAL_MUTABLE = ("status", "reviewed_by_person_id", "reviewed_at", "rejection_reason", "version")

#: An ApprovalRecord's execution outcome, written once when the Tool Gateway runs. The approval
#: itself — decision, action, hash, approver — is frozen the moment it is made.
APPROVAL_MUTABLE = (
    "execution_status",
    "resulting_entity_type",
    "resulting_entity_id",
    "resulting_audit_entry_id",
    "executed_at",
    "execution_error",
    "version",
)


def _immutability_trigger(table: str, mutable: tuple[str, ...], rule: str) -> str:
    """Same mechanism as ADR-0038: subtract the allow-list, compare what is left.

    A column added by a later migration is frozen by default, so forgetting to classify a new field
    fails closed.
    """
    keys = ", ".join(f"'{column}'" for column in mutable)
    return f"""
        CREATE OR REPLACE FUNCTION {table}_is_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            frozen_old jsonb;
            frozen_new jsonb;
        BEGIN
            IF TG_OP <> 'UPDATE' THEN
                RAISE EXCEPTION '{table} is immutable ({rule}): % is not permitted', TG_OP
                    USING ERRCODE = 'restrict_violation';
            END IF;
            frozen_old := to_jsonb(OLD) - ARRAY[{keys}];
            frozen_new := to_jsonb(NEW) - ARRAY[{keys}];
            IF frozen_old <> frozen_new THEN
                RAISE EXCEPTION
                    '{table} is immutable ({rule}); a change requires a new row, not an edit'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END
        $$;
    """


def upgrade() -> None:
    # ------------------------------------------------------------------ commitment
    op.execute(
        """
        CREATE TABLE commitment (
            id                      uuid PRIMARY KEY,
            org_id                  uuid NOT NULL REFERENCES organization (id),
            statement               text NOT NULL,
            committed_by_person_id  uuid NOT NULL,
            committed_to_person_id  uuid,
            committed_to_team_id    uuid,
            due_date                date,
            due_precision           text NOT NULL DEFAULT 'vague',
            status                  text NOT NULL DEFAULT 'captured',
            fulfilling_work_id      uuid,
            project_id              uuid,
            origin_event_id         uuid,
            confidence              smallint NOT NULL DEFAULT 0,
            acknowledged_at         timestamptz,
            previous_due_date       date,
            created_by_person_id    uuid,
            created_at              timestamptz NOT NULL DEFAULT now(),
            updated_at              timestamptz NOT NULL DEFAULT now(),
            version                 integer NOT NULL DEFAULT 1,

            CONSTRAINT commitment_status_valid CHECK (status IN (
                'captured', 'open', 'fulfilled', 'missed', 'renegotiated',
                'cancelled', 'disputed', 'withdrawn')),
            CONSTRAINT commitment_due_precision_valid
                CHECK (due_precision IN ('exact', 'week', 'month', 'vague')),
            CONSTRAINT commitment_confidence_range CHECK (confidence BETWEEN 0 AND 100),
            -- BR-C-01. A promise nobody made, or with nothing promised, is not a commitment.
            CONSTRAINT commitment_has_a_statement CHECK (length(btrim(statement)) > 0),
            -- BR-C-02. Both may be null: a promise made to the room is still a promise.
            CONSTRAINT commitment_recipient_is_singular CHECK (
                committed_to_person_id IS NULL OR committed_to_team_id IS NULL),
            -- A date precision without a date describes nothing.
            CONSTRAINT commitment_precision_needs_a_date
                CHECK (due_date IS NOT NULL OR due_precision = 'vague'),
            CONSTRAINT commitment_committer_fk
                FOREIGN KEY (org_id, committed_by_person_id) REFERENCES person (org_id, id),
            CONSTRAINT commitment_recipient_person_fk
                FOREIGN KEY (org_id, committed_to_person_id) REFERENCES person (org_id, id),
            CONSTRAINT commitment_recipient_team_fk
                FOREIGN KEY (org_id, committed_to_team_id) REFERENCES team (org_id, id),
            CONSTRAINT commitment_work_fk
                FOREIGN KEY (org_id, fulfilling_work_id) REFERENCES work (org_id, id),
            CONSTRAINT commitment_project_fk
                FOREIGN KEY (org_id, project_id) REFERENCES project (org_id, id),
            CONSTRAINT commitment_event_fk
                FOREIGN KEY (org_id, origin_event_id) REFERENCES event (org_id, id),
            CONSTRAINT commitment_org_id_unique UNIQUE (org_id, id)
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_commitment_committer ON commitment (org_id, committed_by_person_id)"
    )
    op.execute(
        "CREATE INDEX ix_commitment_recipient ON commitment (org_id, committed_to_person_id) "
        "WHERE committed_to_person_id IS NOT NULL"
    )
    # BR-C-06's sweep reads exactly this: open, dated, and precise enough to be auto-missed.
    op.execute(
        "CREATE INDEX ix_commitment_due ON commitment (org_id, due_date) "
        "WHERE status = 'open' AND due_precision IN ('exact', 'week')"
    )
    op.execute(
        "CREATE INDEX ix_commitment_origin_event ON commitment (org_id, origin_event_id) "
        "WHERE origin_event_id IS NOT NULL"
    )

    # ------------------------------------------------------------------ proposal
    op.execute(
        """
        CREATE TABLE proposal (
            id                  uuid PRIMARY KEY,
            org_id              uuid NOT NULL REFERENCES organization (id),
            kind                text NOT NULL,
            target_type         text NOT NULL,
            target_id           uuid,
            summary             text NOT NULL,
            reason              text,
            confidence          smallint NOT NULL DEFAULT 0,
            action              jsonb NOT NULL,
            action_hash         text NOT NULL,
            source_event_id     uuid,
            supersedes_proposal_id uuid,
            raised_by_person_id uuid,
            routed_to_person_id uuid NOT NULL,
            status              text NOT NULL DEFAULT 'pending',
            reviewed_by_person_id uuid,
            reviewed_at         timestamptz,
            rejection_reason    text,
            expires_at          timestamptz NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            version             integer NOT NULL DEFAULT 1,

            CONSTRAINT proposal_kind_valid CHECK (kind IN ('create', 'update', 'link', 'close')),
            CONSTRAINT proposal_target_type_valid CHECK (target_type IN (
                'work', 'project', 'milestone', 'dependency', 'work_assignment',
                'commitment', 'evidence')),
            CONSTRAINT proposal_status_valid CHECK (status IN (
                'pending', 'accepted', 'accepted_with_edits', 'rejected', 'expired', 'superseded')),
            CONSTRAINT proposal_confidence_range CHECK (confidence BETWEEN 0 AND 100),
            -- An update or a close must say what it is changing; a create must not pretend to.
            CONSTRAINT proposal_target_matches_kind CHECK (
                (kind = 'create' AND target_id IS NULL) OR (kind <> 'create')),
            -- A decided Proposal records who decided it and when. A pending one must not.
            CONSTRAINT proposal_decision_is_attributed CHECK (
                (status = 'pending' AND reviewed_by_person_id IS NULL AND reviewed_at IS NULL)
                OR (status IN ('expired', 'superseded'))
                OR (reviewed_by_person_id IS NOT NULL AND reviewed_at IS NOT NULL)),
            CONSTRAINT proposal_supersedes_is_not_self
                CHECK (supersedes_proposal_id IS NULL OR supersedes_proposal_id <> id),
            CONSTRAINT proposal_event_fk
                FOREIGN KEY (org_id, source_event_id) REFERENCES event (org_id, id),
            CONSTRAINT proposal_supersedes_fk
                FOREIGN KEY (org_id, supersedes_proposal_id) REFERENCES proposal (org_id, id),
            CONSTRAINT proposal_raised_by_fk
                FOREIGN KEY (org_id, raised_by_person_id) REFERENCES person (org_id, id),
            CONSTRAINT proposal_routed_to_fk
                FOREIGN KEY (org_id, routed_to_person_id) REFERENCES person (org_id, id),
            CONSTRAINT proposal_reviewed_by_fk
                FOREIGN KEY (org_id, reviewed_by_person_id) REFERENCES person (org_id, id),
            CONSTRAINT proposal_org_id_unique UNIQUE (org_id, id)
        );
        """
    )
    op.execute("CREATE INDEX ix_proposal_routed ON proposal (org_id, routed_to_person_id, status)")
    op.execute("CREATE INDEX ix_proposal_target ON proposal (org_id, target_type, target_id)")
    op.execute(
        "CREATE INDEX ix_proposal_pending_expiry ON proposal (org_id, expires_at) "
        "WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX ix_proposal_source_event ON proposal (org_id, source_event_id) "
        "WHERE source_event_id IS NOT NULL"
    )

    # The Evidence a Proposal rests on. BR-AI-02 requires at least one for anything AI-originated,
    # and BR-PR-08 requires the chain to be traversable in both directions — which is why this is a
    # table rather than an array column.
    op.execute(
        """
        CREATE TABLE proposal_evidence (
            id           uuid PRIMARY KEY,
            org_id       uuid NOT NULL REFERENCES organization (id),
            proposal_id  uuid NOT NULL,
            evidence_id  uuid NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT proposal_evidence_proposal_fk
                FOREIGN KEY (org_id, proposal_id) REFERENCES proposal (org_id, id),
            CONSTRAINT proposal_evidence_evidence_fk
                FOREIGN KEY (org_id, evidence_id) REFERENCES evidence (org_id, id)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_proposal_evidence "
        "ON proposal_evidence (org_id, proposal_id, evidence_id)"
    )
    op.execute(
        "CREATE INDEX ix_proposal_evidence_reverse ON proposal_evidence (org_id, evidence_id)"
    )

    # ProposedChange is what a reviewer reads: field by field, what it is now and what it would
    # become. It is presentation of the action, never the thing executed — ADR-0042 refuses a
    # generic field-path applier, so nothing reads these rows to perform a mutation.
    op.execute(
        """
        CREATE TABLE proposed_change (
            id             uuid PRIMARY KEY,
            org_id         uuid NOT NULL REFERENCES organization (id),
            proposal_id    uuid NOT NULL,
            field_path     text NOT NULL,
            current_value  jsonb,
            proposed_value jsonb,
            created_at     timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT proposed_change_proposal_fk
                FOREIGN KEY (org_id, proposal_id) REFERENCES proposal (org_id, id)
        );
        """
    )
    op.execute("CREATE INDEX ix_proposed_change_proposal ON proposed_change (org_id, proposal_id)")

    # ------------------------------------------------------------------ approval_record
    op.execute(
        """
        CREATE TABLE approval_record (
            id                       uuid PRIMARY KEY,
            org_id                   uuid NOT NULL REFERENCES organization (id),
            proposal_id              uuid NOT NULL,
            approver_person_id       uuid NOT NULL,
            decision                 text NOT NULL,
            approved_action          jsonb NOT NULL,
            approved_action_hash     text NOT NULL,
            edits                    jsonb,
            decided_at               timestamptz NOT NULL DEFAULT now(),
            execution_status         text NOT NULL DEFAULT 'pending',
            resulting_entity_type    text,
            resulting_entity_id      uuid,
            resulting_audit_entry_id uuid,
            executed_at              timestamptz,
            execution_error          text,
            version                  integer NOT NULL DEFAULT 1,

            CONSTRAINT approval_decision_valid
                CHECK (decision IN ('approved', 'approved_with_edits', 'rejected')),
            CONSTRAINT approval_execution_status_valid
                CHECK (execution_status IN ('pending', 'executed', 'failed', 'expired',
                                            'not_applicable')),
            -- A rejection authorises nothing, so it must never carry an execution.
            CONSTRAINT approval_rejection_executes_nothing CHECK (
                decision <> 'rejected' OR execution_status = 'not_applicable'),
            -- An executed approval names what it produced; that link is half of BR-PR-08's chain.
            CONSTRAINT approval_executed_names_its_result CHECK (
                execution_status <> 'executed'
                OR (resulting_entity_type IS NOT NULL AND resulting_entity_id IS NOT NULL
                    AND executed_at IS NOT NULL)),
            CONSTRAINT approval_edits_accompany_an_edited_decision CHECK (
                decision <> 'approved' OR edits IS NULL),
            CONSTRAINT approval_proposal_fk
                FOREIGN KEY (org_id, proposal_id) REFERENCES proposal (org_id, id),
            CONSTRAINT approval_approver_fk
                FOREIGN KEY (org_id, approver_person_id) REFERENCES person (org_id, id),
            CONSTRAINT approval_record_org_id_unique UNIQUE (org_id, id)
        );
        """
    )
    # One live approval per Proposal. A rejected Proposal that is later re-raised is a new Proposal
    # (BR-PR-02 supersession), so this does not need to allow a second decision on the same row.
    op.execute(
        "CREATE UNIQUE INDEX ux_approval_record_proposal ON approval_record (org_id, proposal_id)"
    )
    op.execute(
        "CREATE INDEX ix_approval_record_hash ON approval_record (org_id, approved_action_hash)"
    )
    op.execute(
        "CREATE INDEX ix_approval_record_result ON approval_record "
        "(org_id, resulting_entity_type, resulting_entity_id) "
        "WHERE resulting_entity_id IS NOT NULL"
    )

    # ------------------------------------------------------------------ immutability
    op.execute(_immutability_trigger("proposal", PROPOSAL_MUTABLE, "ADR-0041"))
    op.execute(
        "CREATE TRIGGER trg_proposal_immutable BEFORE UPDATE OR DELETE ON proposal "
        "FOR EACH ROW EXECUTE FUNCTION proposal_is_immutable()"
    )
    op.execute(_immutability_trigger("approval_record", APPROVAL_MUTABLE, "BR-PR-01"))
    op.execute(
        "CREATE TRIGGER trg_approval_record_immutable BEFORE UPDATE OR DELETE ON approval_record "
        "FOR EACH ROW EXECUTE FUNCTION approval_record_is_immutable()"
    )
    for table in ("proposal", "approval_record"):
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

    # ------------------------------------------------------------------ RLS
    tables = (
        "commitment",
        "proposal",
        "proposal_evidence",
        "proposed_change",
        "approval_record",
    )
    for table in tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (org_id = app_current_org())
            WITH CHECK (org_id = app_current_org());
            """
        )
        op.execute(f"SELECT app_grant('{table}', 'SELECT, INSERT, UPDATE')")

    op.execute(
        "INSERT INTO tenant_scoped_table (table_name, added_in) VALUES "
        "('commitment', '0010'), ('proposal', '0010'), ('proposal_evidence', '0010'), "
        "('proposed_change', '0010'), ('approval_record', '0010')"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM tenant_scoped_table WHERE table_name IN "
        "('commitment', 'proposal', 'proposal_evidence', 'proposed_change', 'approval_record')"
    )
    for table in ("proposal", "approval_record"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_truncate ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_is_immutable()")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_no_truncate()")
    for table in (
        "approval_record",
        "proposed_change",
        "proposal_evidence",
        "proposal",
        "commitment",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
