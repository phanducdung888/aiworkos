"""The conversation a message belongs to.

ADR-0072, BR-E-19, resolving M-8. `source_ref` identifies one message and nothing said which
messages belong together. For email that was tolerable. For a chat channel the conversation *is*
the unit — and, more immediately, it is the deterministic key ADR-0073 builds its candidate set
from: "another message in this same conversation is already evidence for that work item" is a join,
where "this message sounds like that work item" is a guess.

Nullable, and it stays nullable. A channel that does not thread, a manual capture typed into the
browser, and every Event that predates this migration all legitimately have none — and a backfill
would have to invent thread identity from subject lines, which BR-E-19 forbids for exactly the
reason it would be wrong: a reply that changes the subject leaves the thread, and two unrelated
messages titled "Re: update" join it.

Frozen without further work: `event_is_immutable()` compares `to_jsonb(row)` minus an explicit list
of mutable keys, so a new column is frozen by construction. That is the property worth having —
the trigger did not need to know this column was coming.

The index is the read this exists for, and it is partial: rows with no thread are the ones the
candidate resolver can never match, so indexing them would be paying for entries that are only ever
skipped.

Reversible (contract §6).

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("event", sa.Column("thread_ref", sa.Text(), nullable=True))
    # Scoped by `source_system` for the same reason `source_ref` is: thread identifiers are opaque
    # strings a channel chose, and two channels may pick the same one without meaning anything by
    # it. `org_id` leads because every query starts there and RLS is keyed on it.
    op.execute(
        "CREATE INDEX ix_event_thread ON event (org_id, source_system, thread_ref) "
        "WHERE thread_ref IS NOT NULL AND deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_event_thread")
    op.drop_column("event", "thread_ref")
