"""Visibility inheritance backfill, request idempotency, and one dead index.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-12

**Visibility backfill (W-7, BR-W-19).** Work in a `restricted` Project was readable more widely than
the Project itself, so marking a Project restricted protected nothing while looking like it did.
The application enforces inheritance from now on; this narrows the rows that predate it. There is no
production data yet, which is exactly why writing it now is cheap: the statement is the same one a
real deployment would need, and it is far easier to get right before it matters.

**`idempotency_key` (W-6).** `POST` is not naturally safe to retry, and a client that times out
cannot know whether its Work item was created. Recording the response against a caller-supplied key,
in the same transaction as the mutation, lets the retry return the first answer instead of making a
second entity. Tenant-scoped and RLS-protected like every other table: a key is a request of one
organization and the response body it replays is that organization's data.

**`ix_outbox_unpublished`.** Dead since migration 0006 pointed the relay at `seq`. Dropped
deliberately and with review (contract §16) rather than left to cost a write on every mutation in
the system, because every mutation appends to the outbox.
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

#: Widest to narrowest. The position in this list is the comparison; there is no ordering in the
#: database because a text column with a CHECK constraint carries none.
VISIBILITY_ORDER = ("organization", "department", "team", "restricted")

#: Module-level so the test can run the statement the migration runs, rather than a paraphrase of
#: it. A backfill that is only ever executed once is a backfill nobody has checked.
BACKFILL_WORK_VISIBILITY = """
    UPDATE work w
    SET visibility = p.visibility,
        updated_at = now(),
        version = w.version + 1
    FROM project p
    WHERE w.project_id = p.id
      AND w.org_id = p.org_id
      AND array_position(ARRAY['organization','department','team','restricted']::text[],
                         w.visibility)
        < array_position(ARRAY['organization','department','team','restricted']::text[],
                         p.visibility)
"""


def upgrade() -> None:
    # ------------------------------------------------------------------ BR-W-19 backfill
    # `array_position` turns the level into a rank, so "wider than" is a comparison the database can
    # make. A Work wider than its Project is set to the Project's level exactly — not to the
    # narrowest, which would hide work nobody asked to hide.
    op.execute(BACKFILL_WORK_VISIBILITY)

    # ------------------------------------------------------------------ idempotency
    op.execute(
        """
        CREATE TABLE idempotency_key (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id          uuid NOT NULL REFERENCES organization (id),
            key             text NOT NULL,
            endpoint        text NOT NULL,
            request_hash    text NOT NULL,
            response_status smallint NOT NULL,
            response_body   jsonb NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_idempotency_key_org_id_key_endpoint UNIQUE (org_id, key, endpoint),
            CONSTRAINT ck_idempotency_key_key_not_blank CHECK (length(btrim(key)) > 0)
        );
        """
    )
    # Scoped by endpoint as well as key, so the same key used against two different operations is
    # two records rather than one collision. A client generating one key per user action should not
    # have to know which endpoints that action touches.
    op.execute(
        "CREATE INDEX ix_idempotency_key_org_id_created_at ON idempotency_key (org_id, created_at)"
    )
    op.execute("ALTER TABLE idempotency_key ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE idempotency_key FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY idempotency_key_tenant_isolation ON idempotency_key
        USING (org_id = app_current_org())
        WITH CHECK (org_id = app_current_org());
        """
    )
    # No UPDATE and no DELETE grant: a stored response is the answer that was given, and rewriting
    # it would make a replay disagree with what the client already received. Retention is a job for
    # Phase 2, which will run as a role that can delete.
    op.execute("SELECT app_grant('idempotency_key', 'SELECT, INSERT')")
    op.execute(
        "INSERT INTO tenant_scoped_table (table_name, added_in) VALUES ('idempotency_key', '0007')"
    )

    # ------------------------------------------------------------------ dead index
    op.execute("DROP INDEX IF EXISTS ix_outbox_unpublished")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbox_unpublished ON outbox (id) "
        "WHERE published_at IS NULL"
    )
    op.execute("DELETE FROM tenant_scoped_table WHERE table_name = 'idempotency_key'")
    op.execute("DROP TABLE IF EXISTS idempotency_key")
    # The backfill is deliberately not reversed. It narrowed read access to what BR-W-19 requires,
    # and the previous values are not recoverable from the schema. Re-widening them on a downgrade
    # would hand out access that a rollback never intended to grant.
