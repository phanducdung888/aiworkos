"""Creator provenance on Work Core rows, and a total order for the outbox.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-12

Two defects from earlier checkpoints, both cheap now and expensive later.

**`created_by_person_id` (W-1).** CLAUDE.md §6 requires every table to record who created the row,
and migrations 0003 and 0004 did not. The consequence was not cosmetic: the `PERSONAL`
authorization relation on Work could only be derived from `WorkAssignment`, so a member who
captured Work with no assignee could not afterwards edit it. The Phase 1 readiness review says such
Work draws authority from "the OWNER, the capturing user and their team lead", and MON-001 and
MON-002a both escalate to "the capturing user" — three places in the specification referring to a
person the schema could not name.

A `uuid` column rather than the `jsonb` actor the convention literally says, for two reasons. The
authorization relation is evaluated on every Work read path and must be indexable, which a jsonb
extraction is not, and the full actor — including the AI interaction and approval that produced an
AI-originated row — is already in `audit_entry`, where nothing can quietly rewrite it. Nullable,
because rows created by a monitor, an import or a system actor have no person behind them.

**`outbox.seq` (W-4).** `relay_once` claims `ORDER BY id`, but ids are UUIDv7 whose sub-millisecond
bits are random, so events appended inside one transaction have no defined order. A consumer could
see `WorkCompleted` before the `WorkStatusChanged` that caused it. `occurred_at` cannot fix this:
`now()` is transaction start time and is identical for every event in the transaction. A sequence
is the only thing here that increases per row.
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

#: Every Work Core table. `work_assignment` gets one too: who assigned somebody is a different fact
#: from who they are, and `assigned_by_actor` records the actor rather than an indexable person.
PROVENANCE_TABLES = ("project", "milestone", "work", "work_assignment", "dependency")


def upgrade() -> None:
    for table in PROVENANCE_TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN created_by_person_id uuid")
        # The composite form, like every other cross-entity reference in this schema: a plain
        # reference to `person (id)` would permit a row pointing at a person in another
        # organization, and the constraint is what makes that unrepresentable rather than merely
        # unlikely (BR-G-01).
        op.execute(
            f"""
            ALTER TABLE {table}
            ADD CONSTRAINT fk_{table}_created_by_person_id_org_id
            FOREIGN KEY (created_by_person_id, org_id) REFERENCES person (id, org_id)
            """
        )

    # Only on `work`. This index exists to serve the authorization relation, and Work is the only
    # entity whose relation depends on the column (ADR-0029: project-less Work has no team to
    # inherit reach from, so authorship is the whole of it).
    op.execute(
        "CREATE INDEX ix_work_org_id_created_by_person_id "
        "ON work (org_id, created_by_person_id)"
    )

    # bigserial rather than an identity column so the default and the sequence arrive together on
    # an existing table. The partial index mirrors the claim predicate in `relay_once`.
    op.execute("ALTER TABLE outbox ADD COLUMN seq bigserial NOT NULL")
    op.execute(
        "CREATE INDEX ix_outbox_unpublished_seq ON outbox (seq) WHERE published_at IS NULL"
    )
    # A serial column is two objects, and the grants on the table say nothing about the sequence
    # behind it. Without this the application role can no longer insert into `outbox` at all —
    # which is to say every mutation in the system stops, because every mutation emits.
    # `app_grant` (migration 0001) is conditional on the role existing, so this stays correct in
    # an environment that provisions roles differently.
    op.execute("SELECT app_grant('SEQUENCE outbox_seq_seq', 'USAGE')")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_outbox_unpublished_seq")
    op.execute("ALTER TABLE outbox DROP COLUMN IF EXISTS seq")

    op.execute("DROP INDEX IF EXISTS ix_work_org_id_created_by_person_id")
    for table in reversed(PROVENANCE_TABLES):
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "
            f"fk_{table}_created_by_person_id_org_id"
        )
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS created_by_person_id")
