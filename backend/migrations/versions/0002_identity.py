"""Identity and organization schema.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11

Two things here are deliberate and worth reading before changing:

1. **Composite foreign keys.** Every parent carries `UNIQUE (id, org_id)` and every child references
   `(parent_id, org_id)`. A cross-organization reference is therefore rejected by the database, not
   merely by a service that remembered to check (BR-G-01). It costs one redundant-looking unique
   index per table and removes an entire class of bug.

2. **Person is organization-scoped.** A human who works for two organizations has two Person rows.
   This follows PQ-2 and BR-G-01 literally. See the deviations section of the checkpoint report:
   one sentence in the security model implies a Person can span organizations, which contradicts
   this. Implemented per the binding decision; flagged for a ruling rather than reconciled here.
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "workos_app"

TABLES = (
    "person",
    "organization_membership",
    "department",
    "team",
    "team_membership",
    "role_assignment",
    "external_identity",
)


def upgrade() -> None:
    # ---------------------------------------------------------------- person
    op.execute(
        """
        CREATE TABLE person (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id           uuid NOT NULL REFERENCES organization (id),
            keycloak_subject text,
            display_name     text NOT NULL,
            email            text,
            status           text NOT NULL DEFAULT 'active',
            timezone         text,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            version          integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_person_status CHECK (status IN ('active', 'inactive', 'departed')),
            CONSTRAINT uq_person_id_org_id UNIQUE (id, org_id),
            CONSTRAINT uq_person_org_id_keycloak_subject UNIQUE (org_id, keycloak_subject)
        );
        """
    )
    op.execute("CREATE INDEX ix_person_org_id_status ON person (org_id, status)")

    # ---------------------------------------------------------------- organization membership
    op.execute(
        """
        CREATE TABLE organization_membership (
            id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id     uuid NOT NULL REFERENCES organization (id),
            person_id  uuid NOT NULL,
            status     text NOT NULL DEFAULT 'active',
            joined_at  timestamptz NOT NULL DEFAULT now(),
            left_at    timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version    integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_organization_membership_status
                CHECK (status IN ('active', 'suspended', 'ended')),
            CONSTRAINT uq_organization_membership_id_org_id UNIQUE (id, org_id),
            CONSTRAINT uq_organization_membership_org_id_person_id UNIQUE (org_id, person_id),
            CONSTRAINT fk_organization_membership_person_id_org_id
                FOREIGN KEY (person_id, org_id) REFERENCES person (id, org_id)
        );
        """
    )

    # ---------------------------------------------------------------- department
    op.execute(
        """
        CREATE TABLE department (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id               uuid NOT NULL REFERENCES organization (id),
            parent_department_id uuid,
            name                 text NOT NULL,
            lead_person_id       uuid,
            status               text NOT NULL DEFAULT 'active',
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            version              integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_department_status CHECK (status IN ('active', 'archived')),
            CONSTRAINT ck_department_not_own_parent CHECK (parent_department_id <> id),
            CONSTRAINT uq_department_id_org_id UNIQUE (id, org_id),
            CONSTRAINT fk_department_parent_department_id_org_id
                FOREIGN KEY (parent_department_id, org_id) REFERENCES department (id, org_id),
            CONSTRAINT fk_department_lead_person_id_org_id
                FOREIGN KEY (lead_person_id, org_id) REFERENCES person (id, org_id)
        );
        """
    )
    op.execute("CREATE INDEX ix_department_org_id_parent_department_id ON department (org_id, \
        parent_department_id)")

    # BR-I-01: the hierarchy is a tree, no cycles, maximum depth 5. Enforced in the database because
    # a cycle here makes every rollup query non-terminating, and by then it is production data.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION department_tree_invariants() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            depth   integer := 1;
            cursor_id uuid := NEW.parent_department_id;
        BEGIN
            WHILE cursor_id IS NOT NULL LOOP
                depth := depth + 1;
                IF cursor_id = NEW.id THEN
                    RAISE EXCEPTION
                        'department hierarchy would contain a cycle (BR-I-01)'
                        USING ERRCODE = 'check_violation';
                END IF;
                IF depth > 5 THEN
                    RAISE EXCEPTION
                        'department hierarchy exceeds the maximum depth of 5 (BR-I-01)'
                        USING ERRCODE = 'check_violation';
                END IF;
                SELECT parent_department_id INTO cursor_id FROM department WHERE id = cursor_id;
            END LOOP;
            RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_department_tree_invariants
        BEFORE INSERT OR UPDATE OF parent_department_id ON department
        FOR EACH ROW EXECUTE FUNCTION department_tree_invariants();
        """
    )

    # ---------------------------------------------------------------- team
    op.execute(
        """
        CREATE TABLE team (
            id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id         uuid NOT NULL REFERENCES organization (id),
            department_id  uuid,
            name           text NOT NULL,
            lead_person_id uuid,
            status         text NOT NULL DEFAULT 'active',
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            version        integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_team_status CHECK (status IN ('active', 'archived')),
            CONSTRAINT uq_team_id_org_id UNIQUE (id, org_id),
            CONSTRAINT fk_team_department_id_org_id
                FOREIGN KEY (department_id, org_id) REFERENCES department (id, org_id),
            CONSTRAINT fk_team_lead_person_id_org_id
                FOREIGN KEY (lead_person_id, org_id) REFERENCES person (id, org_id)
        );
        """
    )
    op.execute("CREATE INDEX ix_team_org_id_department_id ON team (org_id, department_id)")

    # ---------------------------------------------------------------- team membership
    op.execute(
        """
        CREATE TABLE team_membership (
            id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id     uuid NOT NULL REFERENCES organization (id),
            team_id    uuid NOT NULL,
            person_id  uuid NOT NULL,
            role       text NOT NULL DEFAULT 'member',
            valid_from timestamptz NOT NULL DEFAULT now(),
            valid_to   timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version    integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_team_membership_role CHECK (role IN ('lead', 'member', 'guest')),
            CONSTRAINT uq_team_membership_id_org_id UNIQUE (id, org_id),
            CONSTRAINT fk_team_membership_team_id_org_id
                FOREIGN KEY (team_id, org_id) REFERENCES team (id, org_id),
            CONSTRAINT fk_team_membership_person_id_org_id
                FOREIGN KEY (person_id, org_id) REFERENCES person (id, org_id)
        );
        """
    )
    # BR-I-03: membership is time-bounded and history is retained, so uniqueness applies only to
    # the currently open row.
    op.execute(
        "CREATE UNIQUE INDEX uq_team_membership_active ON team_membership (team_id, person_id) "
        "WHERE valid_to IS NULL"
    )

    # ---------------------------------------------------------------- role assignment
    op.execute(
        """
        CREATE TABLE role_assignment (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id      uuid NOT NULL REFERENCES organization (id),
            person_id   uuid NOT NULL,
            role        text NOT NULL,
            scope_type  text NOT NULL DEFAULT 'organization',
            scope_id    uuid,
            granted_at  timestamptz NOT NULL DEFAULT now(),
            granted_by_person_id uuid,
            revoked_at  timestamptz,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            version     integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_role_assignment_role CHECK (role IN (
                'org_admin', 'department_lead', 'team_lead',
                'member', 'viewer', 'auditor', 'executive'
            )),
            CONSTRAINT ck_role_assignment_scope_type CHECK (scope_type IN (
                'organization', 'department', 'team', 'project'
            )),
            CONSTRAINT ck_role_assignment_scope_id_required
                CHECK (scope_type = 'organization' OR scope_id IS NOT NULL),
            CONSTRAINT uq_role_assignment_id_org_id UNIQUE (id, org_id),
            CONSTRAINT fk_role_assignment_person_id_org_id
                FOREIGN KEY (person_id, org_id) REFERENCES person (id, org_id)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_role_assignment_active "
        "ON role_assignment (org_id, person_id, role, scope_type, COALESCE(scope_id, org_id)) "
        "WHERE revoked_at IS NULL"
    )
    op.execute("CREATE INDEX ix_role_assignment_org_id_person_id ON role_assignment (org_id, \
        person_id) WHERE revoked_at IS NULL")

    # ---------------------------------------------------------------- external identity
    op.execute(
        """
        CREATE TABLE external_identity (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id               uuid NOT NULL REFERENCES organization (id),
            person_id            uuid NOT NULL,
            source_system        text NOT NULL,
            external_id          text NOT NULL,
            handle               text,
            confidence           smallint NOT NULL DEFAULT 0,
            confirmed_by_person_id uuid,
            confirmed_at         timestamptz,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            version              integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_external_identity_confidence
                CHECK (confidence BETWEEN 0 AND 100),
            CONSTRAINT uq_external_identity_id_org_id UNIQUE (id, org_id),
            CONSTRAINT uq_external_identity_org_id_source_system_external_id
                UNIQUE (org_id, source_system, external_id),
            CONSTRAINT fk_external_identity_person_id_org_id
                FOREIGN KEY (person_id, org_id) REFERENCES person (id, org_id),
            CONSTRAINT fk_external_identity_confirmed_by_person_id_org_id
                FOREIGN KEY (confirmed_by_person_id, org_id) REFERENCES person (id, org_id)
        );
        """
    )

    # ---------------------------------------------------------------- RLS
    for table in TABLES:
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
            f"('{table}', '0002')"
        )


def downgrade() -> None:
    op.execute("DELETE FROM tenant_scoped_table WHERE added_in = '0002'")
    op.execute("DROP TABLE IF EXISTS external_identity")
    op.execute("DROP TABLE IF EXISTS role_assignment")
    op.execute("DROP TABLE IF EXISTS team_membership")
    op.execute("DROP TABLE IF EXISTS team")
    op.execute("DROP TRIGGER IF EXISTS trg_department_tree_invariants ON department")
    op.execute("DROP TABLE IF EXISTS department")
    op.execute("DROP FUNCTION IF EXISTS department_tree_invariants()")
    op.execute("DROP TABLE IF EXISTS organization_membership")
    op.execute("DROP TABLE IF EXISTS person")
