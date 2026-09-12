"""Creator provenance on the Identity tables.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-12

Migration 0006 gave the Work Core a `created_by_person_id`; the Identity tables never got one, so
CLAUDE.md §6 was true of half the schema. It matters more here than it did there: "who added this
person", "who granted this role" and "who confirmed this phone number belongs to that human" are
the questions an organization actually asks of an identity system, and `role_assignment` already
had `granted_by_person_id` for exactly that reason — this makes the rest of the tables agree with
it.

`organization` is deliberately absent. It is the tenant root, it has no `org_id` to pair the
reference with, and whoever creates one necessarily does so from outside every tenant — there is no
`ORGANIZATION.CREATE` cell in the matrix and no endpoint, because provisioning a tenant is an
administrative act like `ops/db/dev-roles.sql`, not an application feature.

No authorization relation depends on these columns, unlike 0006 where the `PERSONAL` grant on Work
did. They are provenance, and they are indexed only where a query will actually use them.
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

PROVENANCE_TABLES = (
    "person",
    "organization_membership",
    "department",
    "team",
    "team_membership",
    "role_assignment",
    "external_identity",
)


def upgrade() -> None:
    for table in PROVENANCE_TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN created_by_person_id uuid")
        # The composite form, like every other cross-entity reference in this schema: a plain
        # reference to `person (id)` would permit a row crediting somebody in another organization
        # (BR-G-01).
        op.execute(
            f"""
            ALTER TABLE {table}
            ADD CONSTRAINT fk_{table}_created_by_person_id_org_id
            FOREIGN KEY (created_by_person_id, org_id) REFERENCES person (id, org_id)
            """
        )


def downgrade() -> None:
    for table in reversed(PROVENANCE_TABLES):
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "
            f"fk_{table}_created_by_person_id_org_id"
        )
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS created_by_person_id")
