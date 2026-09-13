"""A connector is a role of its own.

ADR-0027 required an ingestion-only identity and nothing issued one, so a channel connector would
have had to run as `member` — which holds 56 of the authorization matrix's 83 cells, including
`PROPOSAL.APPROVE`. A stolen delivery credential could then have approved the AI's own proposals,
closing the Level-2 loop with no person anywhere in it.

`ingestion` holds exactly one grant: `EVENT.CREATE`. It cannot read what it delivered, because a
connector needs no read to do its job and a credential that can page through an organization's
messages is the thing a stolen one would be most useful for.

The whole change is one CHECK constraint's value list. No column, no data, no RLS, no change to any
existing role's semantics. `role_assignment.role` is text with a constraint rather than an enum
type, which is why this is a constraint swap and not an `ALTER TYPE`.

Reversible (contract §6). `downgrade` deletes any `ingestion` assignment, because such a row
cannot exist before this migration and means nothing after it is reversed — and because narrowing
the constraint around one fails, which would make the rollback untrue rather than careful.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_WITHOUT_INGESTION = (
    "'org_admin', 'department_lead', 'team_lead', 'member', 'viewer', 'auditor', 'executive'"
)
_WITH_INGESTION = f"{_WITHOUT_INGESTION}, 'ingestion'"


def upgrade() -> None:
    op.execute("ALTER TABLE role_assignment DROP CONSTRAINT ck_role_assignment_role")
    op.execute(
        "ALTER TABLE role_assignment ADD CONSTRAINT ck_role_assignment_role "
        f"CHECK (role IN ({_WITH_INGESTION}))"
    )


def downgrade() -> None:
    """Roll the role back, and the assignments that only exist because of it.

    Deleting rows in a downgrade is not something to do lightly. It is right here because these
    rows *are* the feature: an `ingestion` assignment cannot exist before this migration and has no
    meaning after it is reversed, so leaving them would leave the table holding a role the
    constraint forbids and the code no longer knows. Narrowing the constraint around them is not an
    option either — it fails, which is what makes "reversible in development" (contract §6)
    untrue.

    Operationally this is a revocation: any connector authenticating with such a credential stops
    being able to deliver. That is the intended effect of rolling back the role it depends on, and
    it is why a rollback is a deliberate act rather than a routine one.

    The table is FORCE RLS, so the delete needs FORCE lifted for one statement — see below. It is
    restored in the same transaction, and no policy is altered.
    """
    # `role_assignment` is FORCE RLS (migration 0001), so even the owner is filtered by the tenant
    # policy — and a migration has no organization context, so a plain DELETE would match nothing
    # and the constraint below would then fail on rows nobody could see. FORCE is lifted for this
    # statement and restored immediately, inside the migration's own transaction.
    op.execute("ALTER TABLE role_assignment NO FORCE ROW LEVEL SECURITY")
    op.execute("DELETE FROM role_assignment WHERE role = 'ingestion'")
    op.execute("ALTER TABLE role_assignment FORCE ROW LEVEL SECURITY")

    op.execute("ALTER TABLE role_assignment DROP CONSTRAINT ck_role_assignment_role")
    op.execute(
        "ALTER TABLE role_assignment ADD CONSTRAINT ck_role_assignment_role "
        f"CHECK (role IN ({_WITHOUT_INGESTION}))"
    )
