"""Read models.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-11

`work_current_owner` exists so that "who owns this?" does not require every caller to remember the
role and status filter. It is a view, not a column: derived, never written, and carrying no
authority of its own (ADR-0032).

Both views are created `WITH (security_invoker = true)`. Without it a view runs with its owner's
privileges and would quietly bypass row-level security, which would make the whole tenancy story
depend on nobody ever adding a view.
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW work_current_owner WITH (security_invoker = true) AS
        SELECT
            wa.work_id,
            wa.org_id,
            wa.person_id,
            wa.assigned_at
        FROM work_assignment wa
        WHERE wa.role = 'OWNER' AND wa.status = 'active';
        """
    )

    # BR-RPT-01: All Work partitions into Project Work and Non-project Work. Expressed once, here,
    # so that no report has to decide for itself what "all work" means.
    op.execute(
        """
        CREATE VIEW work_partitioned WITH (security_invoker = true) AS
        SELECT
            w.id,
            w.org_id,
            w.project_id,
            w.status,
            w.due_date,
            w.priority,
            CASE WHEN w.project_id IS NULL THEN 'non_project' ELSE 'project' END AS partition,
            owner.person_id AS current_owner_person_id
        FROM work w
        LEFT JOIN work_current_owner owner ON owner.work_id = w.id;
        """
    )

    op.execute("SELECT app_grant('work_current_owner', 'SELECT')")
    op.execute("SELECT app_grant('work_partitioned', 'SELECT')")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS work_partitioned")
    op.execute("DROP VIEW IF EXISTS work_current_owner")
