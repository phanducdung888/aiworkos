"""`find_similar` (BR-AI-05).

The rule exists to stop an agent that reads ten messages about one deadline from proposing ten Work
items — somebody then rejects nine and learns to stop reading proposals carefully.

Two properties matter more than recall does.

**Determinism.** The same corpus and query must produce the same list in the same order. BR-AI-05
asks "did you look", and an answer that varies between runs makes the check unreproducible and the
evaluation metrics meaningless.

**It reads through the caller's visibility.** A similarity search is an excellent way to enumerate a
corpus one probe at a time, and the defence is to never select the rows rather than to filter the
results afterwards.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import app.contexts.work.public as work
from app.platform.authz import Principal, Role
from app.platform.ids import uuid7
from tests.integration.conftest import WorkOrg

pytestmark = pytest.mark.integration


def a_work_item(session: Session, org_id: uuid.UUID, title: str, **over: object) -> uuid.UUID:
    work_id = uuid7()
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )
    session.execute(
        text(
            "INSERT INTO work (id, org_id, title, type, status, visibility, source) "
            "VALUES (:id, :org, :title, 'task', 'todo', :visibility, 'human')"
        ),
        {
            "id": work_id,
            "org": org_id,
            "title": title,
            "visibility": over.get("visibility", "organization"),
        },
    )
    session.commit()
    return work_id


def search(
    session: Session, org_id: uuid.UUID, person_id: uuid.UUID, title: str
) -> list[work.SimilarWork]:
    principal = Principal(
        person_id=person_id, org_id=org_id, roles=frozenset({Role.ORG_ADMIN})
    )
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )
    return work.find_similar_work(
        session, principal, work.reach_of(session, principal), title=title
    )


# --------------------------------------------------------------------------- matching


def test_a_near_duplicate_is_found(
    scoped_session: Session, work_org: WorkOrg, roles: None
) -> None:
    a_work_item(scoped_session, work_org.org_id, "Send the revised quote to finance")
    found = search(
        scoped_session, work_org.org_id, work_org.admin, "Send the revised quote"
    )
    assert [row.title for row in found] == ["Send the revised quote to finance"]
    assert found[0].score > work.MIN_SIMILARITY


def test_unrelated_work_is_not_found(
    scoped_session: Session, work_org: WorkOrg, roles: None
) -> None:
    """The threshold has to actually discriminate, or every proposal is a duplicate of something."""
    a_work_item(scoped_session, work_org.org_id, "Book the venue for the offsite")
    assert search(
        scoped_session, work_org.org_id, work_org.admin, "Send the revised quote"
    ) == []


def test_an_empty_query_finds_nothing(
    scoped_session: Session, work_org: WorkOrg, roles: None
) -> None:
    a_work_item(scoped_session, work_org.org_id, "Send the revised quote")
    assert search(scoped_session, work_org.org_id, work_org.admin, "   ") == []


def test_the_result_is_a_reference_not_a_copy(
    scoped_session: Session, work_org: WorkOrg, roles: None
) -> None:
    """An id, a title, a status and a score.

    A search result is not a grant of access to everything the row holds, and returning the
    description would make the search a way to read work by guessing at its title.
    """
    a_work_item(scoped_session, work_org.org_id, "Send the revised quote")
    found = search(
        scoped_session, work_org.org_id, work_org.admin, "Send the revised quote"
    )
    assert set(vars(found[0]) if hasattr(found[0], "__dict__") else
               {f: getattr(found[0], f) for f in found[0].__slots__}) == {
        "id", "title", "status", "score"
    }


# --------------------------------------------------------------------------- determinism


def test_the_same_query_returns_the_same_order(
    scoped_session: Session, work_org: WorkOrg, roles: None
) -> None:
    """Trigram scores collide often on short titles, so the id tiebreak is what makes this stable.

    Without it the order depends on the plan, and "did you look" becomes an answer that changes
    between runs.
    """
    for title in (
        "Send the revised quote",
        "Send the revised quote again",
        "Send the revised quote to finance",
    ):
        a_work_item(scoped_session, work_org.org_id, title)

    runs = [
        [(row.id, row.score) for row in search(
            scoped_session, work_org.org_id, work_org.admin, "Send the revised quote"
        )]
        for _ in range(5)
    ]
    assert all(run == runs[0] for run in runs), "the ordering is not deterministic"
    assert len(runs[0]) >= 2


def test_results_are_capped(
    scoped_session: Session, work_org: WorkOrg, roles: None
) -> None:
    """Twenty weak matches answer "does this exist" worse than three strong ones."""
    for index in range(12):
        a_work_item(scoped_session, work_org.org_id, f"Send the revised quote {index}")
    found = search(
        scoped_session, work_org.org_id, work_org.admin, "Send the revised quote"
    )
    assert len(found) <= 5


# --------------------------------------------------------------------------- isolation


def test_another_organizations_work_is_never_found(
    scoped_session: Session, app_session_factory, work_org: WorkOrg, roles: None
) -> None:
    """The leak this would be: an agent probing titles until something matches."""
    other_org = uuid7()
    other = app_session_factory()
    other.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(other_org)}
    )
    other.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:id, 'Rival', :slug)"),
        {"id": other_org, "slug": f"rival-{uuid.uuid4().hex[:12]}"},
    )
    other.commit()
    a_work_item(other, other_org, "Send the revised quote")
    other.close()

    assert search(
        scoped_session, work_org.org_id, work_org.admin, "Send the revised quote"
    ) == []


def test_work_the_caller_cannot_read_is_not_found(
    scoped_session: Session, work_org: WorkOrg, roles: None
) -> None:
    """Visibility is applied by selecting fewer rows, not by trimming the answer.

    The search runs on `readable_work` — the same query the read API uses — so a caller cannot
    learn that something exists by watching whether a probe matches it.
    """
    a_work_item(
        scoped_session, work_org.org_id, "Send the confidential quote", visibility="team"
    )
    member = Principal(
        person_id=work_org.outsider, org_id=work_org.org_id, roles=frozenset({Role.MEMBER})
    )
    scoped_session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"),
        {"org": str(work_org.org_id)},
    )
    found = work.find_similar_work(
        scoped_session,
        member,
        work.reach_of(scoped_session, member),
        title="Send the confidential quote",
    )
    assert found == [], "team-visible work reached somebody outside the team"


def test_an_admin_does_find_it(
    scoped_session: Session, work_org: WorkOrg, roles: None
) -> None:
    """The control for the test above: the row exists and is findable by somebody entitled to it."""
    a_work_item(
        scoped_session, work_org.org_id, "Send the confidential quote", visibility="team"
    )
    assert search(
        scoped_session, work_org.org_id, work_org.admin, "Send the confidential quote"
    )
