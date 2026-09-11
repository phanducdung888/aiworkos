"""Work Core: project, milestone, work, dependency.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-11

The invariants that would be expensive to discover in production live in the database here, not only
in the domain layer:

* `work.project_id` is nullable and nothing requires it (ADR-0029). A milestone link still requires
  a project, and the composite key `(id, project_id, org_id)` on milestone makes it impossible for
  work to point at a milestone belonging to a different project (BR-P-06).
* Work hierarchy is acyclic and at most three deep (BR-W-06), enforced by trigger. A cycle here
  makes every rollup query non-terminating, and by then it is production data.
* The dependency graph over active `blocks` edges is acyclic (BR-D-02), enforced by trigger, with
  the offending path reported. Cycle protection is not deferred to the UI (contract §8).
* `blocked` requires either a reason or an active blocking dependency (BR-W-04). A row-level CHECK
  cannot see the dependency table, so this is a trigger.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLES = ("project", "milestone", "work", "dependency")


def upgrade() -> None:
    # ---------------------------------------------------------------- project
    op.execute(
        """
        CREATE TABLE project (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id             uuid NOT NULL REFERENCES organization (id),
            name               text NOT NULL,
            description        text,
            objective          text,
            owning_team_id     uuid,
            department_id      uuid,
            lead_person_id     uuid,
            sponsor_person_id  uuid,
            status             text NOT NULL DEFAULT 'proposed',
            start_date         date,
            target_date        date,
            actual_end_date    date,
            visibility         text NOT NULL DEFAULT 'organization',
            source             text NOT NULL DEFAULT 'human',
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            version            integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_project_status CHECK (status IN (
                'proposed', 'active', 'on_hold', 'completed', 'cancelled')),
            CONSTRAINT ck_project_visibility CHECK (visibility IN (
                'organization', 'department', 'team', 'restricted')),
            CONSTRAINT ck_project_source CHECK (source IN ('human', 'ai', 'import')),
            CONSTRAINT ck_project_dates CHECK (
                start_date IS NULL OR target_date IS NULL OR target_date >= start_date),
            CONSTRAINT ck_project_owner_required CHECK (
                owning_team_id IS NOT NULL OR department_id IS NOT NULL),
            CONSTRAINT uq_project_id_org_id UNIQUE (id, org_id),
            CONSTRAINT fk_project_owning_team_id_org_id
                FOREIGN KEY (owning_team_id, org_id) REFERENCES team (id, org_id),
            CONSTRAINT fk_project_department_id_org_id
                FOREIGN KEY (department_id, org_id) REFERENCES department (id, org_id),
            CONSTRAINT fk_project_lead_person_id_org_id
                FOREIGN KEY (lead_person_id, org_id) REFERENCES person (id, org_id),
            CONSTRAINT fk_project_sponsor_person_id_org_id
                FOREIGN KEY (sponsor_person_id, org_id) REFERENCES person (id, org_id)
        );
        """
    )
    op.execute("CREATE INDEX ix_project_org_id_status ON project (org_id, status)")

    # ---------------------------------------------------------------- milestone
    op.execute(
        """
        CREATE TABLE milestone (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id              uuid NOT NULL REFERENCES organization (id),
            project_id          uuid NOT NULL,
            name                text NOT NULL,
            description         text,
            acceptance_criteria text,
            target_date         date,
            actual_date         date,
            status              text NOT NULL DEFAULT 'planned',
            order_index         integer NOT NULL DEFAULT 0,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            version             integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_milestone_status CHECK (status IN (
                'planned', 'in_progress', 'achieved', 'missed', 'cancelled')),
            CONSTRAINT uq_milestone_id_org_id UNIQUE (id, org_id),
            CONSTRAINT uq_milestone_id_project_id_org_id UNIQUE (id, project_id, org_id),
            CONSTRAINT fk_milestone_project_id_org_id
                FOREIGN KEY (project_id, org_id) REFERENCES project (id, org_id)
        );
        """
    )
    op.execute("CREATE INDEX ix_milestone_org_id_project_id ON milestone (org_id, project_id)")

    # ---------------------------------------------------------------- work
    op.execute(
        """
        CREATE TABLE work (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id          uuid NOT NULL REFERENCES organization (id),
            project_id      uuid,
            milestone_id    uuid,
            parent_work_id  uuid,
            title           text NOT NULL,
            description     text,
            type            text NOT NULL DEFAULT 'task',
            status          text NOT NULL DEFAULT 'todo',
            priority        text NOT NULL DEFAULT 'normal',
            due_date        date,
            blocked_reason  text,
            started_at      timestamptz,
            completed_at    timestamptz,
            confidence      smallint,
            source          text NOT NULL DEFAULT 'human',
            origin_event_id uuid,
            last_signal_at  timestamptz,
            visibility      text NOT NULL DEFAULT 'team',
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            version         integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_work_title_not_blank CHECK (length(btrim(title)) > 0),
            CONSTRAINT ck_work_type CHECK (type IN (
                'deliverable', 'task', 'activity', 'investigation')),
            CONSTRAINT ck_work_status CHECK (status IN (
                'proposed', 'todo', 'in_progress', 'blocked', 'done', 'cancelled', 'rejected')),
            CONSTRAINT ck_work_priority CHECK (priority IN ('low', 'normal', 'high', 'critical')),
            CONSTRAINT ck_work_visibility CHECK (visibility IN (
                'organization', 'department', 'team', 'restricted')),
            CONSTRAINT ck_work_source CHECK (source IN ('human', 'ai', 'import')),
            CONSTRAINT ck_work_confidence CHECK (confidence IS NULL
                OR confidence BETWEEN 0 AND 100),
            CONSTRAINT ck_work_not_own_parent CHECK (parent_work_id <> id),
            CONSTRAINT ck_work_milestone_requires_project CHECK (
                milestone_id IS NULL OR project_id IS NOT NULL),
            CONSTRAINT ck_work_completed_at_matches_status CHECK (
                (status = 'done') = (completed_at IS NOT NULL)),
            CONSTRAINT uq_work_id_org_id UNIQUE (id, org_id),
            CONSTRAINT fk_work_project_id_org_id
                FOREIGN KEY (project_id, org_id) REFERENCES project (id, org_id),
            CONSTRAINT fk_work_milestone_id_project_id_org_id
                FOREIGN KEY (milestone_id, project_id, org_id)
                REFERENCES milestone (id, project_id, org_id),
            CONSTRAINT fk_work_parent_work_id_org_id
                FOREIGN KEY (parent_work_id, org_id) REFERENCES work (id, org_id)
        );
        """
    )
    op.execute("CREATE INDEX ix_work_org_id_status ON work (org_id, status)")
    op.execute("CREATE INDEX ix_work_org_id_project_id ON work (org_id, project_id)")
    # ADR-0029: non-project work is a first-class partition and is queried as such (BR-RPT-01).
    op.execute(
        "CREATE INDEX ix_work_org_id_non_project ON work (org_id) WHERE project_id IS NULL"
    )
    op.execute("CREATE INDEX ix_work_org_id_due_date ON work (org_id, due_date) "
               "WHERE due_date IS NOT NULL")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION work_hierarchy_invariants() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            depth     integer := 1;
            cursor_id uuid := NEW.parent_work_id;
        BEGIN
            WHILE cursor_id IS NOT NULL LOOP
                depth := depth + 1;
                IF cursor_id = NEW.id THEN
                    RAISE EXCEPTION 'work hierarchy would contain a cycle (BR-W-06)'
                        USING ERRCODE = 'check_violation';
                END IF;
                IF depth > 3 THEN
                    RAISE EXCEPTION 'work hierarchy exceeds the maximum depth of 3 (BR-W-06)'
                        USING ERRCODE = 'check_violation';
                END IF;
                SELECT parent_work_id INTO cursor_id FROM work WHERE id = cursor_id;
            END LOOP;
            RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_work_hierarchy_invariants
        BEFORE INSERT OR UPDATE OF parent_work_id ON work
        FOR EACH ROW EXECUTE FUNCTION work_hierarchy_invariants();
        """
    )

    # ---------------------------------------------------------------- dependency
    op.execute(
        """
        CREATE TABLE dependency (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id       uuid NOT NULL REFERENCES organization (id),
            blocker_type text NOT NULL,
            blocker_id   uuid NOT NULL,
            blocked_type text NOT NULL,
            blocked_id   uuid NOT NULL,
            kind         text NOT NULL DEFAULT 'blocks',
            status       text NOT NULL DEFAULT 'active',
            rationale    text,
            created_at   timestamptz NOT NULL DEFAULT now(),
            updated_at   timestamptz NOT NULL DEFAULT now(),
            version      integer NOT NULL DEFAULT 1,
            CONSTRAINT ck_dependency_blocker_type CHECK (blocker_type IN ('work', 'milestone')),
            CONSTRAINT ck_dependency_blocked_type CHECK (blocked_type IN ('work', 'milestone')),
            CONSTRAINT ck_dependency_kind CHECK (kind IN ('blocks', 'informs')),
            CONSTRAINT ck_dependency_status CHECK (status IN ('active', 'resolved', 'withdrawn')),
            CONSTRAINT ck_dependency_distinct_endpoints CHECK (
                blocker_type <> blocked_type OR blocker_id <> blocked_id),
            CONSTRAINT uq_dependency_id_org_id UNIQUE (id, org_id)
        );
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_dependency_active ON dependency "
        "(org_id, blocker_type, blocker_id, blocked_type, blocked_id, kind) "
        "WHERE status = 'active'"
    )
    op.execute(
        "CREATE INDEX ix_dependency_org_id_blocked ON dependency "
        "(org_id, blocked_type, blocked_id) WHERE status = 'active'"
    )

    # Endpoint existence and tenancy. A polymorphic reference cannot carry a foreign key, so the
    # check that would otherwise be free has to be written out.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dependency_endpoint_exists(
            endpoint_type text, endpoint_id uuid, expected_org uuid
        ) RETURNS boolean
        LANGUAGE plpgsql STABLE AS $$
        DECLARE found_org uuid;
        BEGIN
            IF endpoint_type = 'work' THEN
                SELECT org_id INTO found_org FROM work WHERE id = endpoint_id;
            ELSE
                SELECT org_id INTO found_org FROM milestone WHERE id = endpoint_id;
            END IF;
            RETURN found_org IS NOT NULL AND found_org = expected_org;
        END
        $$;
        """
    )

    # BR-D-02. Walks the active `blocks` graph from the proposed blocked endpoint; if it reaches the
    # blocker, the edge would close a cycle. `informs` edges are soft and excluded by design.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dependency_invariants() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE reachable boolean;
        BEGIN
            IF NOT dependency_endpoint_exists(NEW.blocker_type, NEW.blocker_id, NEW.org_id) THEN
                RAISE EXCEPTION
                    'dependency blocker does not exist in this organization (BR-D-01, BR-D-04)'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF NOT dependency_endpoint_exists(NEW.blocked_type, NEW.blocked_id, NEW.org_id) THEN
                RAISE EXCEPTION
                    'dependency blocked side does not exist in this organization (BR-D-01, BR-D-04)'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;

            IF NEW.kind <> 'blocks' OR NEW.status <> 'active' THEN
                RETURN NEW;
            END IF;

            WITH RECURSIVE downstream(node_type, node_id) AS (
                SELECT NEW.blocked_type, NEW.blocked_id
                UNION
                SELECT d.blocked_type, d.blocked_id
                FROM dependency d
                JOIN downstream s
                  ON d.blocker_type = s.node_type AND d.blocker_id = s.node_id
                WHERE d.org_id = NEW.org_id
                  AND d.kind = 'blocks'
                  AND d.status = 'active'
                  AND d.id <> NEW.id
            )
            SELECT EXISTS (
                SELECT 1 FROM downstream
                WHERE node_type = NEW.blocker_type AND node_id = NEW.blocker_id
            ) INTO reachable;

            IF reachable THEN
                RAISE EXCEPTION
                    'dependency would create a cycle: % % already depends on % % (BR-D-02)',
                    NEW.blocker_type, NEW.blocker_id, NEW.blocked_type, NEW.blocked_id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_dependency_invariants
        BEFORE INSERT OR UPDATE ON dependency
        FOR EACH ROW EXECUTE FUNCTION dependency_invariants();
        """
    )

    # BR-W-04: blocked needs a reason or an active blocking dependency. Neither is sufficient alone
    # as a CHECK, because one of them lives in another table.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION work_blocked_requires_cause() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status <> 'blocked' THEN
                RETURN NEW;
            END IF;
            IF NEW.blocked_reason IS NOT NULL AND length(btrim(NEW.blocked_reason)) > 0 THEN
                RETURN NEW;
            END IF;
            IF EXISTS (
                SELECT 1 FROM dependency
                WHERE org_id = NEW.org_id
                  AND blocked_type = 'work'
                  AND blocked_id = NEW.id
                  AND kind = 'blocks'
                  AND status = 'active'
            ) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                'blocked work requires a reason or an active blocking dependency (BR-W-04)'
                USING ERRCODE = 'check_violation';
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_work_blocked_requires_cause
        BEFORE INSERT OR UPDATE OF status ON work
        FOR EACH ROW EXECUTE FUNCTION work_blocked_requires_cause();
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
            "INSERT INTO tenant_scoped_table (table_name, added_in) "
            f"VALUES ('{table}', '0003')"
        )


def downgrade() -> None:
    op.execute("DELETE FROM tenant_scoped_table WHERE added_in = '0003'")
    op.execute("DROP TRIGGER IF EXISTS trg_work_blocked_requires_cause ON work")
    op.execute("DROP TRIGGER IF EXISTS trg_dependency_invariants ON dependency")
    op.execute("DROP TABLE IF EXISTS dependency")
    op.execute("DROP FUNCTION IF EXISTS dependency_invariants()")
    op.execute("DROP FUNCTION IF EXISTS dependency_endpoint_exists(text, uuid, uuid)")
    op.execute("DROP FUNCTION IF EXISTS work_blocked_requires_cause()")
    op.execute("DROP TRIGGER IF EXISTS trg_work_hierarchy_invariants ON work")
    op.execute("DROP TABLE IF EXISTS work")
    op.execute("DROP FUNCTION IF EXISTS work_hierarchy_invariants()")
    op.execute("DROP TABLE IF EXISTS milestone")
    op.execute("DROP TABLE IF EXISTS project")
