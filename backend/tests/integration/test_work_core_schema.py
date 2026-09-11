"""Work Core invariants, enforced by PostgreSQL.

The domain layer checks these too. These tests exist because the domain layer can be bypassed by a
bulk operation, a repair script or a future tool call, and the database cannot. Where a rule appears
in both places, this is the one that still holds at 3am.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

SessionMaker = sessionmaker[Session]


def _scoped(session: Session, org_id: uuid.UUID) -> None:
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )


@pytest.fixture
def org(two_orgs: tuple[uuid.UUID, uuid.UUID]) -> uuid.UUID:
    return two_orgs[0]


@pytest.fixture
def session(app_session_factory: SessionMaker, org: uuid.UUID):  # type: ignore[no-untyped-def]
    s = app_session_factory()
    _scoped(s, org)
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def make_person(session: Session, org: uuid.UUID, name: str = "Dana") -> uuid.UUID:
    return session.execute(
        text(
            "INSERT INTO person (org_id, display_name) VALUES (:org, :name) RETURNING id"
        ),
        {"org": org, "name": name},
    ).scalar_one()


def make_team(session: Session, org: uuid.UUID) -> uuid.UUID:
    return session.execute(
        text("INSERT INTO team (org_id, name) VALUES (:org, 'Platform') RETURNING id"),
        {"org": org},
    ).scalar_one()


def make_project(session: Session, org: uuid.UUID, team_id: uuid.UUID) -> uuid.UUID:
    return session.execute(
        text(
            "INSERT INTO project (org_id, name, owning_team_id) "
            "VALUES (:org, 'Migration', :team) RETURNING id"
        ),
        {"org": org, "team": team_id},
    ).scalar_one()


def make_work(
    session: Session,
    org: uuid.UUID,
    *,
    title: str = "Check the IOC API",
    project_id: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
) -> uuid.UUID:
    return session.execute(
        text(
            "INSERT INTO work (org_id, title, project_id, parent_work_id) "
            "VALUES (:org, :title, :project, :parent) RETURNING id"
        ),
        {"org": org, "title": title, "project": project_id, "parent": parent_id},
    ).scalar_one()


def link(
    session: Session,
    org: uuid.UUID,
    blocker: uuid.UUID,
    blocked: uuid.UUID,
    *,
    kind: str = "blocks",
) -> uuid.UUID:
    return session.execute(
        text(
            "INSERT INTO dependency (org_id, blocker_type, blocker_id, blocked_type, "
            "blocked_id, kind) VALUES (:org, 'work', :blocker, 'work', :blocked, :kind) "
            "RETURNING id"
        ),
        {"org": org, "blocker": blocker, "blocked": blocked, "kind": kind},
    ).scalar_one()


# --------------------------------------------------------------------------- optional project


def test_work_can_be_created_with_no_project_and_no_assignment(
    session: Session, org: uuid.UUID
) -> None:
    """ADR-0029 and BR-W-15, together, at the database level."""
    work = make_work(session, org)
    row = session.execute(
        text("SELECT project_id, status FROM work WHERE id = :id"), {"id": work}
    ).one()
    assert row.project_id is None
    assert row.status == "todo"
    assert session.execute(
        text("SELECT count(*) FROM work_assignment WHERE work_id = :id"), {"id": work}
    ).scalar() == 0


def test_a_milestone_link_requires_a_project(session: Session, org: uuid.UUID) -> None:
    team = make_team(session, org)
    project = make_project(session, org, team)
    milestone = session.execute(
        text(
            "INSERT INTO milestone (org_id, project_id, name) "
            "VALUES (:org, :project, 'Cutover') RETURNING id"
        ),
        {"org": org, "project": project},
    ).scalar_one()
    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO work (org_id, title, milestone_id) "
                "VALUES (:org, 'orphan', :milestone)"
            ),
            {"org": org, "milestone": milestone},
        )


def test_work_cannot_reference_a_milestone_from_another_project(
    session: Session, org: uuid.UUID
) -> None:
    """BR-P-06, bought by the composite key on milestone rather than by a service check."""
    team = make_team(session, org)
    project_a = make_project(session, org, team)
    project_b = session.execute(
        text(
            "INSERT INTO project (org_id, name, owning_team_id) "
            "VALUES (:org, 'Other', :team) RETURNING id"
        ),
        {"org": org, "team": team},
    ).scalar_one()
    milestone_b = session.execute(
        text(
            "INSERT INTO milestone (org_id, project_id, name) "
            "VALUES (:org, :project, 'Theirs') RETURNING id"
        ),
        {"org": org, "project": project_b},
    ).scalar_one()

    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO work (org_id, title, project_id, milestone_id) "
                "VALUES (:org, 'confused', :project, :milestone)"
            ),
            {"org": org, "project": project_a, "milestone": milestone_b},
        )


def test_a_project_requires_an_owning_team_or_department(
    session: Session, org: uuid.UUID
) -> None:
    with pytest.raises(DBAPIError):
        session.execute(
            text("INSERT INTO project (org_id, name) VALUES (:org, 'Ownerless')"),
            {"org": org},
        )


# --------------------------------------------------------------------------- work invariants


def test_a_blank_title_is_refused(session: Session, org: uuid.UUID) -> None:
    with pytest.raises(DBAPIError):
        session.execute(
            text("INSERT INTO work (org_id, title) VALUES (:org, '   ')"), {"org": org}
        )


def test_completed_at_and_status_cannot_disagree(session: Session, org: uuid.UUID) -> None:
    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO work (org_id, title, status) VALUES (:org, 'half done', 'done')"
            ),
            {"org": org},
        )


def test_blocking_requires_a_reason_or_an_active_blocker(
    session: Session, org: uuid.UUID
) -> None:
    """BR-W-04. A row-level CHECK cannot see the dependency table, so this is a trigger."""
    work = make_work(session, org)
    with pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE work SET status = 'blocked' WHERE id = :id"), {"id": work}
        )
    session.rollback()
    _scoped(session, org)

    work = make_work(session, org, title="with a reason")
    session.execute(
        text(
            "UPDATE work SET status = 'blocked', blocked_reason = 'waiting on vendor' "
            "WHERE id = :id"
        ),
        {"id": work},
    )

    blocker = make_work(session, org, title="blocker")
    blocked = make_work(session, org, title="blocked by dependency")
    link(session, org, blocker, blocked)
    session.execute(
        text("UPDATE work SET status = 'blocked' WHERE id = :id"), {"id": blocked}
    )


def test_work_hierarchy_depth_is_capped_at_three(session: Session, org: uuid.UUID) -> None:
    root = make_work(session, org, title="root")
    child = make_work(session, org, title="child", parent_id=root)
    grandchild = make_work(session, org, title="grandchild", parent_id=child)
    with pytest.raises(DBAPIError):
        make_work(session, org, title="too deep", parent_id=grandchild)


def test_work_cannot_be_its_own_parent(session: Session, org: uuid.UUID) -> None:
    work = make_work(session, org)
    with pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE work SET parent_work_id = id WHERE id = :id"), {"id": work}
        )


def test_a_work_hierarchy_cycle_is_refused(session: Session, org: uuid.UUID) -> None:
    root = make_work(session, org, title="root")
    child = make_work(session, org, title="child", parent_id=root)
    with pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE work SET parent_work_id = :child WHERE id = :root"),
            {"child": child, "root": root},
        )


# --------------------------------------------------------------------------- dependencies


def test_a_direct_dependency_cycle_is_refused(session: Session, org: uuid.UUID) -> None:
    a = make_work(session, org, title="a")
    b = make_work(session, org, title="b")
    link(session, org, a, b)
    with pytest.raises(DBAPIError) as excinfo:
        link(session, org, b, a)
    assert "cycle" in str(excinfo.value)


def test_an_indirect_dependency_cycle_is_refused(session: Session, org: uuid.UUID) -> None:
    """A → B → C → A. Contract §8 names this case explicitly."""
    a = make_work(session, org, title="a")
    b = make_work(session, org, title="b")
    c = make_work(session, org, title="c")
    link(session, org, a, b)
    link(session, org, b, c)
    with pytest.raises(DBAPIError) as excinfo:
        link(session, org, c, a)
    assert "cycle" in str(excinfo.value)


def test_a_self_dependency_is_refused(session: Session, org: uuid.UUID) -> None:
    a = make_work(session, org, title="a")
    with pytest.raises(DBAPIError):
        link(session, org, a, a)


def test_a_valid_dependency_chain_is_accepted(session: Session, org: uuid.UUID) -> None:
    nodes = [make_work(session, org, title=f"n{i}") for i in range(5)]
    for blocker, blocked in zip(nodes, nodes[1:], strict=False):
        link(session, org, blocker, blocked)
    assert session.execute(
        text("SELECT count(*) FROM dependency WHERE status = 'active'")
    ).scalar() == 4


def test_a_diamond_is_not_a_cycle(session: Session, org: uuid.UUID) -> None:
    root, left, right, tip = (make_work(session, org, title=n) for n in "rlt2")
    link(session, org, root, left)
    link(session, org, root, right)
    link(session, org, left, tip)
    link(session, org, right, tip)


def test_soft_informs_edges_do_not_create_cycles(session: Session, org: uuid.UUID) -> None:
    a = make_work(session, org, title="a")
    b = make_work(session, org, title="b")
    link(session, org, a, b, kind="informs")
    link(session, org, b, a, kind="informs")


def test_a_dependency_endpoint_must_exist_in_this_organization(
    session: Session, org: uuid.UUID, two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """BR-D-04. A polymorphic reference gets no foreign key, so the trigger does the work."""
    a = make_work(session, org, title="a")
    with pytest.raises(DBAPIError):
        link(session, org, a, uuid.uuid4())


def test_a_duplicate_active_dependency_is_refused(session: Session, org: uuid.UUID) -> None:
    a = make_work(session, org, title="a")
    b = make_work(session, org, title="b")
    link(session, org, a, b)
    with pytest.raises(DBAPIError):
        link(session, org, a, b)


# --------------------------------------------------------------------------- assignment


def test_a_second_active_owner_is_refused_by_the_index(
    session: Session, org: uuid.UUID
) -> None:
    """BR-W-13, enforced where a bulk update cannot get around it."""
    work = make_work(session, org)
    first = make_person(session, org, "First")
    second = make_person(session, org, "Second")
    assign = text(
        "INSERT INTO work_assignment (org_id, work_id, person_id, role, assigned_by_actor) "
        "VALUES (:org, :work, :person, :role, '{}'::jsonb)"
    )
    session.execute(assign, {"org": org, "work": work, "person": first, "role": "OWNER"})
    with pytest.raises(DBAPIError):
        session.execute(
            assign, {"org": org, "work": work, "person": second, "role": "OWNER"}
        )


def test_ending_an_owner_assignment_frees_the_slot(session: Session, org: uuid.UUID) -> None:
    work = make_work(session, org)
    first = make_person(session, org, "First")
    second = make_person(session, org, "Second")
    assign = text(
        "INSERT INTO work_assignment (org_id, work_id, person_id, role, assigned_by_actor) "
        "VALUES (:org, :work, :person, 'OWNER', '{}'::jsonb)"
    )
    session.execute(assign, {"org": org, "work": work, "person": first})
    session.execute(
        text(
            "UPDATE work_assignment SET status = 'ended', ended_at = now() "
            "WHERE work_id = :work AND person_id = :person"
        ),
        {"work": work, "person": first},
    )
    session.execute(assign, {"org": org, "work": work, "person": second})

    owners = session.execute(
        text("SELECT person_id FROM work_current_owner WHERE work_id = :work"), {"work": work}
    ).scalars().all()
    assert owners == [second]
    # History is retained, not overwritten (BR-W-14).
    assert session.execute(
        text("SELECT count(*) FROM work_assignment WHERE work_id = :work"), {"work": work}
    ).scalar() == 2


def test_many_contributors_and_reviewers_are_allowed(session: Session, org: uuid.UUID) -> None:
    work = make_work(session, org)
    assign = text(
        "INSERT INTO work_assignment (org_id, work_id, person_id, role, assigned_by_actor) "
        "VALUES (:org, :work, :person, :role, '{}'::jsonb)"
    )
    for index in range(3):
        person = make_person(session, org, f"Contributor {index}")
        session.execute(
            assign, {"org": org, "work": work, "person": person, "role": "CONTRIBUTOR"}
        )
    for index in range(2):
        person = make_person(session, org, f"Reviewer {index}")
        session.execute(
            assign, {"org": org, "work": work, "person": person, "role": "REVIEWER"}
        )
    assert session.execute(
        text("SELECT count(*) FROM work_assignment WHERE work_id = :work"), {"work": work}
    ).scalar() == 5


def test_only_one_assignment_may_be_primary(session: Session, org: uuid.UUID) -> None:
    work = make_work(session, org)
    assign = text(
        "INSERT INTO work_assignment (org_id, work_id, person_id, role, is_primary, "
        "assigned_by_actor) VALUES (:org, :work, :person, :role, true, '{}'::jsonb)"
    )
    session.execute(
        assign,
        {"org": org, "work": work, "person": make_person(session, org, "A"), "role": "OWNER"},
    )
    with pytest.raises(DBAPIError):
        session.execute(
            assign,
            {
                "org": org,
                "work": work,
                "person": make_person(session, org, "B"),
                "role": "REVIEWER",
            },
        )


def test_a_departed_person_cannot_receive_a_new_assignment(
    session: Session, org: uuid.UUID
) -> None:
    work = make_work(session, org)
    person = make_person(session, org, "Leaver")
    session.execute(
        text("UPDATE person SET status = 'departed' WHERE id = :id"), {"id": person}
    )
    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO work_assignment (org_id, work_id, person_id, role, "
                "assigned_by_actor) VALUES (:org, :work, :person, 'OWNER', '{}'::jsonb)"
            ),
            {"org": org, "work": work, "person": person},
        )


def test_work_cannot_be_assigned_to_a_person_from_another_organization(
    app_session_factory: SessionMaker, two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    org_a, org_b = two_orgs
    outsider = app_session_factory()
    _scoped(outsider, org_b)
    stranger = make_person(outsider, org_b, "Outsider")
    outsider.commit()
    outsider.close()

    session = app_session_factory()
    _scoped(session, org_a)
    work = make_work(session, org_a)
    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "INSERT INTO work_assignment (org_id, work_id, person_id, role, "
                "assigned_by_actor) VALUES (:org, :work, :person, 'OWNER', '{}'::jsonb)"
            ),
            {"org": org_a, "work": work, "person": stranger},
        )
    session.rollback()
    session.close()


# --------------------------------------------------------------------------- read models


def test_the_partition_view_splits_project_and_non_project_work(
    session: Session, org: uuid.UUID
) -> None:
    """BR-RPT-01. Expressed once in a view so no report has to decide for itself."""
    team = make_team(session, org)
    project = make_project(session, org, team)
    make_work(session, org, title="in a project", project_id=project)
    make_work(session, org, title="no project")
    make_work(session, org, title="also no project")

    rows = session.execute(
        text("SELECT partition, count(*) FROM work_partitioned GROUP BY partition")
    ).all()
    counts = {partition: count for partition, count in rows}
    assert counts == {"project": 1, "non_project": 2}


def test_the_read_models_respect_row_level_security(
    app_session_factory: SessionMaker, two_orgs: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """`security_invoker` on the views. Without it a view quietly bypasses tenancy."""
    org_a, org_b = two_orgs
    session = app_session_factory()
    _scoped(session, org_a)
    work = make_work(session, org_a, title="theirs to see")
    person = make_person(session, org_a, "Owner")
    session.execute(
        text(
            "INSERT INTO work_assignment (org_id, work_id, person_id, role, assigned_by_actor) "
            "VALUES (:org, :work, :person, 'OWNER', '{}'::jsonb)"
        ),
        {"org": org_a, "work": work, "person": person},
    )
    session.commit()
    session.close()

    session = app_session_factory()
    _scoped(session, org_b)
    assert session.execute(text("SELECT count(*) FROM work_current_owner")).scalar() == 0
    assert session.execute(text("SELECT count(*) FROM work_partitioned")).scalar() == 0
    session.close()
