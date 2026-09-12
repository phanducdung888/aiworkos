"""BR-W-19: Work never sees further than the Project it belongs to.

The leak this closes was quiet, which is what made it dangerous. Marking a Project `restricted`
changed the Project row and nothing else, so the Work inside it stayed as readable as before while
the interface said otherwise. A control that reports success and does nothing is worse than one that
is absent, because nobody goes looking for it.

Four directions, and all four have to hold or the leak simply moves:

* creating Work wider than its Project is refused;
* creating it narrower is allowed, because a Work may keep a secret its Project does not;
* narrowing the Project drags its wider Work down with it;
* widening the Project leaves its Work alone.

The last one is the one people expect to be symmetric. It is not, deliberately: access is granted by
somebody deciding to grant it, never as a side effect of a change to a container.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contexts.work.public import (
    ChangeProjectVisibility,
    ChangeWorkVisibility,
    CreateProject,
    CreateWork,
    DomainRuleViolation,
    ProjectService,
    WorkService,
)
from app.platform.authz import Role
from app.platform.ids import uuid7
from tests.integration.conftest import WorkOrg
from tests.integration.test_work_services import context

pytestmark = pytest.mark.integration


def a_project(
    session: Session, org: WorkOrg, *, visibility: str, name: str = "Migration"
) -> uuid.UUID:
    service = ProjectService(context(session, org, org.admin, Role.ORG_ADMIN))
    return service.create(
        CreateProject(name=name, owning_team_id=org.team_id, visibility=visibility)
    ).id


def visibility_of(session: Session, table: str, row_id: uuid.UUID) -> str:
    return str(
        session.execute(
            text(f"SELECT visibility FROM {table} WHERE id = :id"),  # noqa: S608
            {"id": row_id},
        ).scalar_one()
    )


def test_work_inherits_the_projects_visibility_when_none_is_named(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = a_project(scoped_session, work_org, visibility="restricted")

    work = WorkService(admin).create(CreateWork(title="Sensitive", project_id=project_id))
    assert work.visibility == "restricted", (
        "the default `team` would have been wider than the project that contains it"
    )


def test_work_with_no_project_keeps_its_own_default(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """ADR-0029. There is nothing to inherit from, and that is not an error to be corrected."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    work = WorkService(admin).create(CreateWork(title="Unparented"))
    assert work.visibility == "team"


def test_work_may_be_narrower_than_its_project(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = a_project(scoped_session, work_org, visibility="department")

    work = WorkService(admin).create(
        CreateWork(title="Quieter", project_id=project_id, visibility="restricted")
    )
    assert work.visibility == "restricted"


def test_work_may_not_be_wider_than_its_project(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = a_project(scoped_session, work_org, visibility="team")

    with pytest.raises(DomainRuleViolation, match="BR-W-19"):
        WorkService(admin).create(
            CreateWork(title="Louder", project_id=project_id, visibility="organization")
        )


def test_a_later_widening_of_the_work_itself_is_refused_too(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """Otherwise the rule would hold for exactly one moment in a Work item's life."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = a_project(scoped_session, work_org, visibility="team")
    work = WorkService(admin).create(CreateWork(title="Inside", project_id=project_id))

    with pytest.raises(DomainRuleViolation, match="BR-W-19"):
        WorkService(admin).change_visibility(
            ChangeWorkVisibility(
                work_id=work.id,
                expected_version=work.version,
                visibility="organization",
            )
        )


def test_narrowing_a_project_narrows_the_work_inside_it(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = a_project(scoped_session, work_org, visibility="organization")
    wide = WorkService(admin).create(CreateWork(title="Was org wide", project_id=project_id))
    already_narrow = WorkService(admin).create(
        CreateWork(title="Already quiet", project_id=project_id, visibility="restricted")
    )
    assert wide.visibility == "organization"

    project_version = int(
        scoped_session.execute(
            text("SELECT version FROM project WHERE id = :id"), {"id": project_id}
        ).scalar_one()
    )
    ProjectService(
        context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    ).change_visibility(
        ChangeProjectVisibility(
            project_id=project_id, expected_version=project_version, visibility="team"
        )
    )

    assert visibility_of(scoped_session, "work", wide.id) == "team"
    assert visibility_of(scoped_session, "work", already_narrow.id) == "restricted", (
        "a cascade narrows what is too wide; it does not level everything to the same value"
    )


def test_the_cascade_records_the_action_that_caused_it(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """Same shape as BR-P-04's cascade: the audit entry says why, not just what."""
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = a_project(scoped_session, work_org, visibility="organization")
    work = WorkService(admin).create(CreateWork(title="Dragged along", project_id=project_id))

    project_version = int(
        scoped_session.execute(
            text("SELECT version FROM project WHERE id = :id"), {"id": project_id}
        ).scalar_one()
    )
    ProjectService(
        context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    ).change_visibility(
        ChangeProjectVisibility(
            project_id=project_id,
            expected_version=project_version,
            visibility="restricted",
        )
    )

    entry = scoped_session.execute(
        text(
            "SELECT action, actor, before_state, after_state FROM audit_entry "
            "WHERE resource_id = :id ORDER BY occurred_at DESC, id DESC LIMIT 1"
        ),
        {"id": work.id},
    ).mappings().one()
    assert entry["action"] == "change_visibility"
    assert entry["before_state"]["visibility"] == "organization"
    assert entry["after_state"]["visibility"] == "restricted"
    assert entry["actor"]["extra"]["cascaded_from"] == (
        "project.change_visibility:restricted"
    )


def test_widening_a_project_leaves_its_work_where_it_was(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """The asymmetry, asserted rather than assumed.

    Granting read access is a decision somebody makes about a specific thing. If widening a Project
    swept its Work along, a lead opening up a project for a quarterly review would publish every
    deliberately-quiet item inside it, and would have no reason to expect that.
    """
    admin = context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    project_id = a_project(scoped_session, work_org, visibility="restricted")
    work = WorkService(admin).create(CreateWork(title="Stays quiet", project_id=project_id))
    assert work.visibility == "restricted"

    project_version = int(
        scoped_session.execute(
            text("SELECT version FROM project WHERE id = :id"), {"id": project_id}
        ).scalar_one()
    )
    ProjectService(
        context(scoped_session, work_org, work_org.admin, Role.ORG_ADMIN)
    ).change_visibility(
        ChangeProjectVisibility(
            project_id=project_id,
            expected_version=project_version,
            visibility="organization",
        )
    )

    assert visibility_of(scoped_session, "work", work.id) == "restricted"


def test_the_migration_backfill_narrows_rows_that_predate_the_rule(
    scoped_session: Session, work_org: WorkOrg
) -> None:
    """Migration 0007, run against data deliberately inserted in the broken shape.

    Written directly with SQL, because the service layer now refuses to produce this state — which
    is the point, and also why the backfill cannot be exercised through it. The statement imported
    here is the one the migration executes, not a paraphrase of it.
    """
    spec = importlib.util.spec_from_file_location(
        "migration_0007",
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "0007_visibility_backfill_and_idempotency.py",
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    project_id = a_project(scoped_session, work_org, visibility="team")
    too_wide = uuid7()
    fine = uuid7()
    for work_id, visibility in ((too_wide, "organization"), (fine, "restricted")):
        scoped_session.execute(
            text(
                "INSERT INTO work (id, org_id, project_id, title, status, visibility) "
                "VALUES (:id, :org, :project, :title, 'todo', :visibility)"
            ),
            {
                "id": work_id,
                "org": work_org.org_id,
                "project": project_id,
                "title": f"Legacy {visibility}",
                "visibility": visibility,
            },
        )
    scoped_session.commit()
    assert visibility_of(scoped_session, "work", too_wide) == "organization"

    scoped_session.execute(text(migration.BACKFILL_WORK_VISIBILITY))
    scoped_session.commit()

    assert visibility_of(scoped_session, "work", too_wide) == "team"
    assert visibility_of(scoped_session, "work", fine) == "restricted"
