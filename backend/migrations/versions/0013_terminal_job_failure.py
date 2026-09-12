"""A dead job says why it stopped.

`job_dead_is_exhausted` required `attempts >= max_attempts` for a job to be `dead`, which encoded
one reason for dying: the retry budget ran out. Checkpoint 10 adds another — an approval whose
execution window has closed cannot improve by being tried again (ADR-0051), so its job is dead
after one attempt.

Setting `attempts = max_attempts` to satisfy the old constraint would have been a lie in a column
somebody will later read as a retry count. The constraint is replaced with the invariant it was
actually protecting: a dead job is never silent. Exhaustion and terminal refusal both record a
reason, and a job that stopped for no stated reason is the thing worth refusing.

No data migration: every existing `dead` row already carries `last_error`, because the only path
that produced one set it.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE job DROP CONSTRAINT IF EXISTS job_dead_is_exhausted")
    op.execute(
        """
        ALTER TABLE job ADD CONSTRAINT job_dead_states_a_reason
        CHECK (status <> 'dead' OR last_error IS NOT NULL)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE job DROP CONSTRAINT IF EXISTS job_dead_states_a_reason")
    # Restoring the old constraint would fail against any row killed terminally with attempts
    # below the budget, which is the state this migration exists to allow. Rows are relaxed to
    # `failed` first so the downgrade is actually runnable rather than theoretically reversible.
    #
    # RLS is suspended for the statement. `job` is FORCE ROW LEVEL SECURITY and a migration runs as
    # the owner with no organization context, so the UPDATE would otherwise match nothing and the
    # constraint would be restored against rows it cannot satisfy — a downgrade that reports
    # success and leaves the schema unrunnable. Owner-level maintenance across every tenant is what
    # a migration is for; it is re-forced on the next line rather than left to a later one.
    op.execute("ALTER TABLE job NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE job DISABLE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE job SET status = 'failed' WHERE status = 'dead' AND attempts < max_attempts"
    )
    op.execute("ALTER TABLE job ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE job FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        ALTER TABLE job ADD CONSTRAINT job_dead_is_exhausted
        CHECK (status <> 'dead' OR attempts >= max_attempts)
        """
    )
