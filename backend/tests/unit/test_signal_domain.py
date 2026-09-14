"""Event rules without a database (BR-E-01 to BR-E-16).

The database enforces several of these too — the check constraints, the partial unique index
behind BR-E-02, the immutability trigger — and the duplication is deliberate. The
constraint is the backstop and cannot explain itself; this is where the rule is stated
and where a caller gets an error naming the rule they broke.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.contexts.signal.domain import (
    CAPTURABLE_TYPES,
    MAX_FUTURE_SKEW,
    EventOrigin,
    EventType,
    ExistingEvent,
    Sensitivity,
    assert_capturable,
    content_hash_for,
    decide_capture,
    may_extract,
    validate_capture,
    validate_participant,
    visible_to,
)
from app.platform.errors import DomainRuleViolation

NOW = dt.datetime(2026, 9, 12, 12, 0, tzinfo=dt.UTC)


def capture(**over: object) -> None:
    kwargs: dict[str, object] = {
        "event_type": EventType.MANUAL_CAPTURE,
        "origin": EventOrigin.EXTERNAL,
        "occurred_at": NOW,
        "now": NOW,
        "body_text": "something was said",
        "raw_payload_uri": None,
        "source_ref": None,
        "thread_ref": None,
        "origin_domain_event_id": None,
    }
    kwargs.update(over)
    validate_capture(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- BR-E-03


class TestAnEventMustRecordSomething:
    def test_text_is_enough(self) -> None:
        capture(body_text="a note", raw_payload_uri=None)

    def test_a_payload_is_enough(self) -> None:
        capture(body_text=None, raw_payload_uri="s3://bucket/key")

    @pytest.mark.parametrize("empty", [None, "", "   ", "\n\t "])
    def test_neither_is_refused(self, empty: str | None) -> None:
        """An Event with no content records that something happened and nothing about what."""
        with pytest.raises(DomainRuleViolation, match="BR-E-03"):
            capture(body_text=empty, raw_payload_uri=None)


# --------------------------------------------------------------------------- BR-E-09


class TestOccurrenceTime:
    def test_the_past_is_always_acceptable(self) -> None:
        capture(occurred_at=NOW - dt.timedelta(days=3650))

    def test_a_little_clock_skew_is_tolerated(self) -> None:
        """Clocks disagree and time zones get applied twice. A day is room for both."""
        capture(occurred_at=NOW + MAX_FUTURE_SKEW - dt.timedelta(minutes=1))

    def test_the_boundary_itself_is_allowed(self) -> None:
        capture(occurred_at=NOW + MAX_FUTURE_SKEW)

    def test_further_out_is_a_scheduling_intention_not_an_occurrence(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-E-09"):
            capture(occurred_at=NOW + MAX_FUTURE_SKEW + dt.timedelta(minutes=1))


# --------------------------------------------------------------------------- BR-E-10, BR-E-15


class TestOrigin:
    def test_an_internal_event_names_the_domain_event_it_came_from(self) -> None:
        capture(
            event_type=EventType.COMMENT,
            origin=EventOrigin.INTERNAL,
            origin_domain_event_id=uuid.uuid4(),
        )

    def test_an_internal_event_without_a_source_is_refused(self) -> None:
        """Without it, BR-E-11's loop prevention has nothing to trace back through."""
        with pytest.raises(DomainRuleViolation, match="BR-E-10"):
            capture(
                event_type=EventType.COMMENT,
                origin=EventOrigin.INTERNAL,
                origin_domain_event_id=None,
            )

    def test_an_external_event_carrying_one_is_refused(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-E-10"):
            capture(origin=EventOrigin.EXTERNAL, origin_domain_event_id=uuid.uuid4())

    def test_a_comment_is_internal_by_definition(self) -> None:
        """BR-E-15, ADR-0033. There is no Comment entity; a comment is an Event of this type."""
        with pytest.raises(DomainRuleViolation, match="BR-E-15"):
            capture(event_type=EventType.COMMENT, origin=EventOrigin.EXTERNAL)


# --------------------------------------------------------------------------- BR-E-13


class TestWhatAPersonMayAssert:
    @pytest.mark.parametrize("event_type", sorted(CAPTURABLE_TYPES))
    def test_a_person_may_record_what_they_observed(self, event_type: EventType) -> None:
        assert_capturable(event_type)

    def test_a_person_may_not_fabricate_the_systems_account_of_itself(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-E-13"):
            assert_capturable(EventType.SYSTEM_ACTIVITY)

    def test_the_six_binding_types_are_all_present(self) -> None:
        """Resolution Pack v1.1 fixes these six. The enum may grow; it may not shrink."""
        assert {t.value for t in EventType} >= {
            "EXTERNAL_MESSAGE",
            "MANUAL_CAPTURE",
            "MEETING_NOTE",
            "COMMENT",
            "ATTACHMENT",
            "SYSTEM_ACTIVITY",
        }


# --------------------------------------------------------------------------- BR-E-02


class TestIngestionIsIdempotent:
    def test_a_manual_capture_is_never_a_duplicate(self) -> None:
        """Two people writing down the same meeting wrote down two observations of it.

        Deciding they are one thing is a judgement the system is not entitled to make from a hash.
        """
        decision = decide_capture(source_ref=None, content_hash="same", existing=None)
        assert decision.is_new and decision.duplicate_of is None

    def test_the_same_reference_and_the_same_content_is_the_same_event(self) -> None:
        original = ExistingEvent(id=uuid.uuid4(), content_hash="abc", source_ref="msg-1")
        decision = decide_capture(
            source_ref="msg-1", content_hash="abc", existing=original
        )
        assert not decision.is_new
        assert decision.duplicate_of == original.id
        assert decision.revision_of is None

    def test_the_same_reference_with_changed_content_is_a_revision(self) -> None:
        """An upstream system that edits a message in place is not lying to us.

        It is telling us the message changed, and both versions are true of their own moment.
        """
        original = ExistingEvent(id=uuid.uuid4(), content_hash="abc", source_ref="msg-1")
        decision = decide_capture(
            source_ref="msg-1", content_hash="def", existing=original
        )
        assert decision.is_new
        assert decision.revision_of == original.id
        assert decision.duplicate_of is None

    def test_a_first_sighting_of_a_reference_is_new(self) -> None:
        decision = decide_capture(source_ref="msg-9", content_hash="abc", existing=None)
        assert decision.is_new and decision.revision_of is None

    def test_a_blank_reference_is_not_a_reference(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-E-02"):
            capture(source_ref="   ")


class TestContentHash:
    def test_the_same_content_hashes_the_same(self) -> None:
        assert content_hash_for(title="t", body_text="b") == content_hash_for(
            title="t", body_text="b"
        )

    def test_moving_text_across_the_title_boundary_changes_the_hash(self) -> None:
        """Without a separator, ("ab", "c") and ("a", "bc") would be one Event.

        BR-E-02 would then read a genuine edit as a duplicate and drop it.
        """
        assert content_hash_for(title="ab", body_text="c") != content_hash_for(
            title="a", body_text="bc"
        )

    def test_an_absent_title_and_an_empty_title_are_the_same_event(self) -> None:
        assert content_hash_for(title=None, body_text="b") == content_hash_for(
            title="", body_text="b"
        )


# --------------------------------------------------------------------------- BR-E-11


class TestLoopPrevention:
    def test_external_events_are_extractable(self) -> None:
        assert may_extract(EventOrigin.EXTERNAL, Sensitivity.NORMAL) is True

    def test_internal_events_are_never_extractable(self) -> None:
        """The rule the whole loop-prevention design rests on.

        Without it an AI-proposed Work item becomes a DomainEvent, becomes an Event, and is
        re-extracted into another proposal, forever.
        """
        assert may_extract(EventOrigin.INTERNAL, Sensitivity.NORMAL) is False

    def test_a_restricted_event_is_withheld_from_extraction(self) -> None:
        """BR-E-08's policy exclusion, applied where extraction will ask."""
        assert may_extract(EventOrigin.EXTERNAL, Sensitivity.RESTRICTED) is False

    def test_confidential_is_still_extractable(self) -> None:
        """Only `restricted` is named by the rule. Inventing a second tier would mean the label
        means something nobody wrote down."""
        assert may_extract(EventOrigin.EXTERNAL, Sensitivity.CONFIDENTIAL) is True


# --------------------------------------------------------------------------- BR-E-08


class TestSensitivity:
    @pytest.mark.parametrize(
        "sensitivity", [Sensitivity.NORMAL, Sensitivity.CONFIDENTIAL]
    )
    def test_an_unrestricted_event_is_visible_to_the_organization(
        self, sensitivity: Sensitivity
    ) -> None:
        assert visible_to(sensitivity=sensitivity, is_participant=False, is_auditor=False)

    def test_a_restricted_event_reaches_its_participants(self) -> None:
        assert visible_to(
            sensitivity=Sensitivity.RESTRICTED, is_participant=True, is_auditor=False
        )

    def test_a_restricted_event_reaches_an_auditor(self) -> None:
        assert visible_to(
            sensitivity=Sensitivity.RESTRICTED, is_participant=False, is_auditor=True
        )

    def test_a_restricted_event_reaches_nobody_else(self) -> None:
        assert not visible_to(
            sensitivity=Sensitivity.RESTRICTED, is_participant=False, is_auditor=False
        )


# --------------------------------------------------------------------------- BR-E-12


class TestParticipants:
    def test_a_resolved_person_is_a_participant(self) -> None:
        validate_participant(person_id=uuid.uuid4(), external_handle=None, match_confidence=80)

    def test_an_unresolved_handle_is_a_participant(self) -> None:
        """A commitment is often made by a phone number nobody has matched to a person yet."""
        validate_participant(
            person_id=None, external_handle="+84900000001", match_confidence=0
        )

    def test_a_participant_nobody_can_name_is_refused(self) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-E-12"):
            validate_participant(person_id=None, external_handle=None, match_confidence=0)

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_handle_is_not_a_name(self, blank: str) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-E-12"):
            validate_participant(person_id=None, external_handle=blank, match_confidence=0)

    @pytest.mark.parametrize("confidence", [-1, 101])
    def test_confidence_is_a_percentage(self, confidence: int) -> None:
        with pytest.raises(DomainRuleViolation, match="BR-E-12"):
            validate_participant(
                person_id=uuid.uuid4(), external_handle=None, match_confidence=confidence
            )

    def test_confidence_without_a_resolution_is_meaningless(self) -> None:
        """Confident about what? The number describes a match to a person."""
        with pytest.raises(DomainRuleViolation, match="BR-E-12"):
            validate_participant(
                person_id=None, external_handle="+84900000001", match_confidence=90
            )


class TestTheConversationAMessageBelongsTo:
    """BR-E-19, ADR-0072.

    The same argument `source_ref` gets, and it matters more here. A blank `source_ref` would defeat
    the partial unique index that makes deduplication work; a blank `thread_ref` would join every
    other blank one in the candidate resolver's index, which is the one place a meaningless value
    does real damage — unrelated conversations would look like the same conversation, and ADR-0073
    would then propose links between them.
    """

    def test_absent_is_fine(self) -> None:
        capture(thread_ref=None)

    def test_a_reference_is_fine(self) -> None:
        capture(thread_ref="<thread-1@example.test>")

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
    def test_a_blank_conversation_is_not_a_conversation(self, blank: str) -> None:
        with pytest.raises(DomainRuleViolation) as raised:
            capture(thread_ref=blank)
        assert raised.value.rule == "BR-E-19"
