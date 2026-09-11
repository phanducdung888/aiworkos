"""WorkAssignment — the canonical assignment source of truth.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-11

ADR-0032. There is no assignee column on `work` and there never will be; the architecture test
`test_work_has_no_assignee_column` stops being vacuous from this migration onward.

The single-active-OWNER rule is a partial unique index rather than application logic, because a rule
enforced only in a service is defeated by the first bulk operation, repair script or future tool
call that does not go through it (BR-W-13).

Work with no assignment is valid and expected (BR-W-15). Nothing here requires a row to exist.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE work_assignment (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id            uuid NOT NULL REFERENCES organization (id),
            work_id           uuid NOT NULL,
            person_id         uuid NOT NULL,
            role              text NOT NULL,
            is_primary        boolean NOT NULL DEFAULT false,
            status            text NOT NULL DEFAULT 'active',
            assigned_at       timestamptz NOT NULL DEFAULT now(),
            assigned_by_actor jsonb NOT NULL,
            started_at        timestamptz,
            ended_at          timestamptz,
            source            text NOT NULL DEFAULT 'human',
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            version           integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_work_assignment_role CHECK (role IN (
                'OWNER', 'CONTRIBUTOR', 'REVIEWER')),
            CONSTRAINT ck_work_assignment_status CHECK (status IN ('active', 'ended')),
            CONSTRAINT ck_work_assignment_ended_at_matches_status CHECK (
                (status = 'ended') = (ended_at IS NOT NULL)),
            CONSTRAINT uq_work_assignment_id_org_id UNIQUE (id, org_id),
            CONSTRAINT fk_work_assignment_work_id_org_id
                FOREIGN KEY (work_id, org_id) REFERENCES work (id, org_id),
            CONSTRAINT fk_work_assignment_person_id_org_id
                FOREIGN KEY (person_id, org_id) REFERENCES person (id, org_id)
        );
        """
    )

    # BR-W-13: zero or one active OWNER per work item.
    op.execute(
        "CREATE UNIQUE INDEX uq_work_assignment_single_active_owner "
        "ON work_assignment (work_id) WHERE role = 'OWNER' AND status = 'active'"
    )
    # BR-W-16: at most one active primary assignment per work item.
    op.execute(
        "CREATE UNIQUE INDEX uq_work_assignment_single_active_primary "
        "ON work_assignment (work_id) WHERE is_primary AND status = 'active'"
    )
    # A person holds at most one active assignment of a given role on a given work item. History is
    # retained, so uniqueness applies only to the open row (BR-W-14).
    op.execute(
        "CREATE UNIQUE INDEX uq_work_assignment_active_person_role "
        "ON work_assignment (work_id, person_id, role) WHERE status = 'active'"
    )
    op.execute(
        "CREATE INDEX ix_work_assignment_org_id_person_id ON work_assignment "
        "(org_id, person_id) WHERE status = 'active'"
    )

    # BR-I-05: a departed person receives no new active assignment. Existing rows are retained.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION work_assignment_person_is_assignable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE person_status text;
        BEGIN
            IF NEW.status <> 'active' THEN
                RETURN NEW;
            END IF;
            SELECT status INTO person_status FROM person WHERE id = NEW.person_id;
            IF person_status <> 'active' THEN
                RAISE EXCEPTION
                    'work cannot be assigned to a person with status % (BR-I-05, BR-W-11)',
                    person_status
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_work_assignment_person_is_assignable
        BEFORE INSERT OR UPDATE OF person_id, status ON work_assignment
        FOR EACH ROW EXECUTE FUNCTION work_assignment_person_is_assignable();
        """
    )

    op.execute("ALTER TABLE work_assignment ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE work_assignment FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY work_assignment_tenant_isolation ON work_assignment
        USING (org_id = app_current_org())
        WITH CHECK (org_id = app_current_org());
        """
    )
    op.execute("SELECT app_grant('work_assignment', 'SELECT, INSERT, UPDATE')")
    op.execute(
        "INSERT INTO tenant_scoped_table (table_name, added_in) "
        "VALUES ('work_assignment', '0004')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM tenant_scoped_table WHERE added_in = '0004'")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_work_assignment_person_is_assignable ON work_assignment"
    )
    op.execute("DROP TABLE IF EXISTS work_assignment")
    op.execute("DROP FUNCTION IF EXISTS work_assignment_person_is_assignable()")
