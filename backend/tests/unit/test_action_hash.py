"""The canonical action and its hash (ADR-0041).

This is the smallest and most load-bearing thing in Checkpoint 7. Every guarantee about "the
approval is for this exact action" reduces to: the same action always produces the same digest, and
a different action never produces the same one.

It is also the easiest thing to break silently. A change to how actions are serialised would not
fail anything obvious — it would quietly invalidate every stored approval, and the symptom would be
approvals that refuse to execute for no visible reason. Hence the pinned digest below.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.contexts.intelligence.action import Action, hash_of
from app.platform.errors import DomainRuleViolation


def an_action(**arguments: object) -> Action:
    return Action(tool="create_work", tool_version="v1", arguments=dict(arguments))


class TestStability:
    def test_the_same_action_hashes_the_same(self) -> None:
        assert an_action(title="Ship it").digest() == an_action(title="Ship it").digest()

    def test_argument_order_does_not_change_the_action(self) -> None:
        """Two documents differing only in key order are the same action."""
        first = Action(
            tool="t", tool_version="v1", arguments={"a": 1, "b": 2, "nested": {"x": 1, "y": 2}}
        )
        second = Action(
            tool="t", tool_version="v1", arguments={"b": 2, "nested": {"y": 2, "x": 1}, "a": 1}
        )
        assert first.digest() == second.digest()

    def test_a_uuid_hashes_the_same_however_it_is_supplied(self) -> None:
        """A round trip through the database turns UUIDs into strings.

        If that changed the digest, every approval would stop matching the moment it was read back.
        """
        identifier = uuid.uuid4()
        assert an_action(person_id=identifier).digest() == an_action(
            person_id=str(identifier)
        ).digest()

    def test_a_date_hashes_the_same_however_it_is_supplied(self) -> None:
        when = dt.date(2026, 9, 12)
        assert an_action(due_date=when).digest() == an_action(due_date="2026-09-12").digest()

    def test_a_stored_action_round_trips_to_the_same_digest(self) -> None:
        original = an_action(title="Ship it", due_date=dt.date(2026, 9, 12))
        assert hash_of(original.as_json()) == original.digest()

    def test_the_digest_is_pinned(self) -> None:
        """A canary for the serialisation format itself.

        If this fails, the canonical form changed and every ApprovalRecord already in a database
        now disagrees with its own action. That is a migration, not a passing test — so the
        expected value is written down rather than computed.
        """
        action = Action(
            tool="create_work",
            tool_version="v1",
            arguments={"title": "Ship it", "project_id": None},
        )
        assert action.canonical_form() == (
            '{"arguments":{"project_id":null,"title":"Ship it"},'
            '"tool":"create_work","tool_version":"v1"}'
        )


class TestDiscrimination:
    def test_a_changed_argument_changes_the_digest(self) -> None:
        assert an_action(title="Ship it").digest() != an_action(title="Ship it!").digest()

    def test_a_changed_nested_value_changes_the_digest(self) -> None:
        first = Action(tool="t", tool_version="v1", arguments={"o": {"deep": {"x": 1}}})
        second = Action(tool="t", tool_version="v1", arguments={"o": {"deep": {"x": 2}}})
        assert first.digest() != second.digest()

    def test_an_added_argument_changes_the_digest(self) -> None:
        assert an_action(title="x").digest() != an_action(title="x", description="y").digest()

    def test_a_different_tool_changes_the_digest(self) -> None:
        same = {"title": "x"}
        assert Action(tool="a", tool_version="v1", arguments=same).digest() != Action(
            tool="b", tool_version="v1", arguments=same
        ).digest()

    def test_a_different_tool_version_changes_the_digest(self) -> None:
        """An action approved against v1 must not execute against a v2 that means something else."""
        same = {"title": "x"}
        assert Action(tool="a", tool_version="v1", arguments=same).digest() != Action(
            tool="a", tool_version="v2", arguments=same
        ).digest()

    def test_null_and_absent_are_different_actions(self) -> None:
        """"Clear the project" and "leave the project alone" are not the same instruction."""
        assert an_action(title="x", project_id=None).digest() != an_action(title="x").digest()

    def test_a_string_and_a_number_are_different(self) -> None:
        assert an_action(v=1).digest() != an_action(v="1").digest()


class TestRefusals:
    def test_a_float_is_refused(self) -> None:
        """Floats do not round-trip identically through JSON in every runtime.

        An action whose hash depended on one would be a hash that occasionally disagrees with
        itself, which is worse than refusing a value nothing in this system needs.
        """
        with pytest.raises(DomainRuleViolation, match="floating-point"):
            an_action(confidence=0.9).digest()

    def test_an_arbitrary_object_is_refused(self) -> None:
        with pytest.raises(DomainRuleViolation, match="ADR-0041"):
            an_action(thing=object()).digest()

    @pytest.mark.parametrize("missing", ["tool", "tool_version", "arguments"])
    def test_a_truncated_stored_action_is_refused(self, missing: str) -> None:
        payload = an_action(title="x").as_json()
        del payload[missing]
        with pytest.raises(DomainRuleViolation, match="ADR-0041"):
            hash_of(payload)

    def test_stored_arguments_must_be_an_object(self) -> None:
        with pytest.raises(DomainRuleViolation, match="arguments"):
            hash_of({"tool": "t", "tool_version": "v1", "arguments": ["not", "an", "object"]})
