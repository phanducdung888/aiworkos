"""ADR-0038, proved at the database.

Every test here writes raw SQL as the **owner** role, deliberately bypassing the services, the ORM
and every line of Python that might be enforcing the rule politely. That is the point: an Event is
the system's record of what happened, and a record that holds only because the application layer
remembered to be careful is not a record. The extraction worker, the retention sweep and the
projector are all still to be written, and each is a plausible place for an UPDATE nobody reviews.

The mutable allow-list is asserted as carefully as the frozen part. A trigger that refused every
update would be easy to write and would make the entity unusable — BR-E-01 names three categories of
field that must stay writable, and a test that only checked the refusals would pass against a broken
implementation of exactly that kind.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.platform.ids import uuid7

pytestmark = pytest.mark.integration

#: BR-E-01 as the migration states it. Kept here as a literal rather than imported from the
#: migration so that widening the allow-list has to be written down twice, in two places a reviewer
#: reads for different reasons.
FROZEN_COLUMNS = {
    "source_system": "'forged'",
    "source_ref": "'forged-ref'",
    "content_hash": "'0000'",
    "origin": "'internal'",
    "type": "'COMMENT'",
    "channel": "'forged'",
    "sender_external_id": "'+84900000000'",
    "occurred_at": "now()",
    "observed_at": "now()",
    "title": "'rewritten'",
    "body_text": "'rewritten'",
    "sensitivity": "'restricted'",
    "captured_by_person_id": "NULL",
    "org_id": "gen_random_uuid()",
}

MUTABLE_COLUMNS = {
    "processing_status": "'extracted'",
    "processing_error": "'boom'",
    "participant_count": "7",
    "retention_expires_at": "now()",
    "raw_payload_uri": "NULL",
    "deleted_at": "now()",
}


@dataclasses.dataclass(frozen=True)
class Fixture:
    org_id: uuid.UUID
    event_id: uuid.UUID
    person_id: uuid.UUID


@pytest.fixture
def an_event(owner_session: Session) -> Iterator[Fixture]:
    """One organization, one Person, one captured Event.

    The org context is set with `is_local => false` rather than the `true` the application uses.
    These tests commit repeatedly and then keep querying, and a transaction-local setting is
    discarded at every commit — which would make the *fixture's* own rows invisible and turn a
    refusal that never happened into a passing test.

    Session-level means it outlives the transaction *and* the session: the connection goes back to
    the pool still carrying it, and the next test to borrow that connection starts inside an
    organization it never chose. So it is cleared on the way out, and that teardown is the reason
    `test_the_org_context_function_exists_and_defaults_to_null` still passes.
    """
    org_id, event_id, person_id = uuid7(), uuid7(), uuid7()
    owner_session.execute(
        text("SELECT set_config('app.current_org_id', :org, false)"), {"org": str(org_id)}
    )
    owner_session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:id, 'Imm', :slug)"),
        {"id": org_id, "slug": f"imm-{uuid.uuid4().hex[:12]}"},
    )
    owner_session.execute(
        text(
            "INSERT INTO person (id, org_id, display_name, status) "
            "VALUES (:id, :org, 'Cara Capturer', 'active')"
        ),
        {"id": person_id, "org": org_id},
    )
    owner_session.execute(
        text(
            "INSERT INTO event (id, org_id, source_system, content_hash, origin, type, "
            "occurred_at, body_text, captured_by_person_id) VALUES (:id, :org, 'web', 'h1', "
            "'external', 'MANUAL_CAPTURE', now(), 'what was originally said', :person)"
        ),
        {"id": event_id, "org": org_id, "person": person_id},
    )
    owner_session.commit()
    try:
        yield Fixture(org_id=org_id, event_id=event_id, person_id=person_id)
    finally:
        owner_session.rollback()
        owner_session.execute(text("SELECT set_config('app.current_org_id', '', false)"))
        owner_session.commit()


@pytest.mark.parametrize(("column", "value"), sorted(FROZEN_COLUMNS.items()))
def test_a_frozen_column_cannot_be_edited(
    owner_session: Session, an_event: Fixture, column: str, value: str
) -> None:
    """Not by the owner, not by anyone. The trigger is not a permission."""
    with pytest.raises(DBAPIError) as error:
        owner_session.execute(
            text(f"UPDATE event SET {column} = {value} WHERE id = :id"), {"id": an_event.event_id}
        )
    assert "immutable" in str(error.value).lower()
    owner_session.rollback()


@pytest.mark.parametrize(("column", "value"), sorted(MUTABLE_COLUMNS.items()))
def test_a_pipeline_owned_column_stays_writable(
    owner_session: Session, an_event: Fixture, column: str, value: str
) -> None:
    """BR-E-01's exceptions. Extraction, participant resolution and retention all need these."""
    owner_session.execute(
        text(f"UPDATE event SET {column} = {value} WHERE id = :id"), {"id": an_event.event_id}
    )
    owner_session.rollback()


def test_an_event_cannot_be_deleted(owner_session: Session, an_event: Fixture) -> None:
    with pytest.raises(DBAPIError) as error:
        owner_session.execute(text("DELETE FROM event WHERE id = :id"), {"id": an_event.event_id})
    assert "immutable" in str(error.value).lower()
    owner_session.rollback()


def test_the_event_table_cannot_be_truncated(owner_session: Session, an_event: Fixture) -> None:
    """A row trigger never fires for TRUNCATE, so the table would empty in silence without this."""
    with pytest.raises(DBAPIError) as error:
        owner_session.execute(text("TRUNCATE event CASCADE"))
    assert "truncate" in str(error.value).lower()
    owner_session.rollback()


def test_the_trigger_freezes_by_subtraction_not_by_naming_columns(
    owner_session: Session,
) -> None:
    """A column added by a later migration must be frozen without anyone remembering to say so.

    Asserted against the installed function body rather than by adding a column: an `ALTER TABLE`
    here needs an ACCESS EXCLUSIVE lock and deadlocks against the other sessions this suite keeps
    open. What matters is the mechanism — the trigger subtracts an allow-list from the whole row,
    so anything not on the list is frozen by construction — and the mechanism is readable.
    """
    body = owner_session.execute(
        text("SELECT prosrc FROM pg_proc WHERE proname = 'event_is_immutable'")
    ).scalar_one()

    assert "to_jsonb(OLD)" in body and "to_jsonb(NEW)" in body, (
        "the trigger must compare whole rows; a list of named columns silently permits any field "
        "added after it was written"
    )
    named = {
        column
        for column in FROZEN_COLUMNS
        if f"'{column}'" in body
    }
    assert not named, f"frozen columns must not be enumerated in the trigger: {sorted(named)}"
    for column in MUTABLE_COLUMNS:
        assert f"'{column}'" in body, f"{column} is documented as mutable but is not exempted"


def test_a_participants_raw_handle_survives_resolution(
    owner_session: Session, an_event: Fixture
) -> None:
    """BR-E-12. Resolving who somebody is may not edit the evidence of how they appeared."""
    org_id = an_event.org_id
    participant_id = uuid7()
    owner_session.execute(
        text(
            "INSERT INTO event_participant (id, org_id, event_id, external_handle, role) "
            "VALUES (:id, :org, :event, '+84900000001', 'speaker')"
        ),
        {"id": participant_id, "org": org_id, "event": an_event.event_id},
    )
    owner_session.commit()

    with pytest.raises(DBAPIError) as error:
        owner_session.execute(
            text(
                "UPDATE event_participant SET external_handle = '+84900000002' WHERE id = :id"
            ),
            {"id": participant_id},
        )
    assert "immutable" in str(error.value).lower()
    owner_session.rollback()


def test_resolving_a_participant_to_a_person_is_permitted(
    owner_session: Session, an_event: Fixture
) -> None:
    """The other half of BR-E-12: resolution is a process and has to be able to run."""
    org_id = an_event.org_id
    person_id, participant_id = an_event.person_id, uuid7()
    owner_session.execute(
        text(
            "INSERT INTO event_participant (id, org_id, event_id, external_handle, role) "
            "VALUES (:id, :org, :event, '+84900000003', 'speaker')"
        ),
        {"id": participant_id, "org": org_id, "event": an_event.event_id},
    )
    owner_session.commit()

    owner_session.execute(
        text(
            "UPDATE event_participant SET person_id = :person, match_confidence = 85, "
            "resolved_at = now() WHERE id = :id"
        ),
        {"person": person_id, "id": participant_id},
    )
    owner_session.commit()
    handle = owner_session.execute(
        text("SELECT external_handle FROM event_participant WHERE id = :id"),
        {"id": participant_id},
    ).scalar_one()
    assert handle == "+84900000003", "resolution must not overwrite the raw sender identifier"


def test_evidence_may_only_be_superseded(owner_session: Session, an_event: Fixture) -> None:
    """BR-E-06. A correction is a new row the old one points forward to."""
    org_id = an_event.org_id
    first, second = uuid7(), uuid7()
    for evidence_id in (first, second):
        owner_session.execute(
            text(
                "INSERT INTO evidence (id, org_id, event_id, locator, excerpt, target_type, "
                "target_id, assertion, produced_by_type) VALUES (:id, :org, :event, "
                "'{\"char_start\": 0, \"char_end\": 4}'::jsonb, 'what', 'work', :target, "
                "'supports', 'person')"
            ),
            {"id": evidence_id, "org": org_id, "event": an_event.event_id, "target": uuid7()},
        )
    owner_session.commit()

    with pytest.raises(DBAPIError) as error:
        owner_session.execute(
            text("UPDATE evidence SET excerpt = 'paraphrased' WHERE id = :id"), {"id": first}
        )
    assert "immutable" in str(error.value).lower()
    owner_session.rollback()

    owner_session.execute(
        text("UPDATE evidence SET superseded_by_id = :second WHERE id = :first"),
        {"second": second, "first": first},
    )
    owner_session.commit()
