"""BR-AI-05 asks "does this already exist". CP14 fixes *where* it looks.

Every accepted intent used to be checked against Work titles, commitments included. Two failures
came out of that one line, and neither showed up as an error:

- a promise the same person had already made was proposed again, because no Work item resembled it;
- a real commitment was suppressed because an unrelated Work item happened to be worded alike.

These tests drive `_RuntimeServices._duplicate_search` directly, with stub searches, because the
routing decision is the thing under test and a database would only make it slower to read.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

import pytest

from app.api.v1.agent import _DUPLICATE_REFUSAL, _DUPLICATE_SEARCH_KEYS, _RuntimeServices
from app.platform.agentkit.contract import IntentKind

COMMITTER = uuid.uuid4()


@dataclasses.dataclass(frozen=True, slots=True)
class _Hit:
    id: uuid.UUID


@dataclasses.dataclass(frozen=True, slots=True)
class _Accepted:
    """Just enough of a `ValidatedIntent` for the router to decide."""

    kind: IntentKind
    summary: str
    arguments: dict[str, Any]


class _Searches:
    """A stand-in that records which corpus was asked, and answers with a hit either way.

    Both searches return something on purpose. A router that quietly fell back to Work would look
    correct against a stub that returned nothing for commitments.
    """

    def __init__(self) -> None:
        self.asked: list[str] = []

    def find_similar_work(self, title: str) -> list[_Hit]:
        self.asked.append("work")
        return [_Hit(id=uuid.uuid4())]

    def find_similar_commitments(
        self, statement: str, committed_by_person_id: uuid.UUID
    ) -> list[_Hit]:
        self.asked.append("commitment")
        assert committed_by_person_id == COMMITTER
        return [_Hit(id=uuid.uuid4())]


def _search(accepted: _Accepted) -> tuple[_Searches, str, list[uuid.UUID]]:
    searches = _Searches()
    name, hits = _RuntimeServices._duplicate_search(searches, accepted)  # type: ignore[arg-type]
    return searches, name, hits


def test_a_commitment_is_searched_among_commitments() -> None:
    searches, name, hits = _search(
        _Accepted(
            kind=IntentKind.CREATE_COMMITMENT,
            summary="I will send the revised quote on Friday",
            arguments={"statement": "…", "committed_by_person_id": str(COMMITTER)},
        )
    )
    assert searches.asked == ["commitment"]
    assert name == "find_similar_commitments"
    assert hits


def test_a_commitment_is_never_searched_among_work() -> None:
    """The regression itself: a promise is not a duplicate because a task is worded alike."""
    searches, _name, _hits = _search(
        _Accepted(
            kind=IntentKind.CREATE_COMMITMENT,
            summary="I will send the revised quote on Friday",
            arguments={"statement": "…", "committed_by_person_id": str(COMMITTER)},
        )
    )
    assert "work" not in searches.asked


def test_work_keeps_the_search_it_always_had() -> None:
    searches, name, hits = _search(
        _Accepted(
            kind=IntentKind.CREATE_WORK,
            summary="Check whether the delivery date still works",
            arguments={"title": "Check whether the delivery date still works"},
        )
    )
    assert searches.asked == ["work"]
    assert name == "find_similar_work"
    assert hits


def test_an_unrecognised_kind_still_gets_a_duplicate_check() -> None:
    """Conservative default. A new intent kind should be over-checked, never unchecked."""
    searches, name, _hits = _search(
        _Accepted(
            kind=IntentKind.ASSIGN_WORK,
            summary="Assign the quote to Mai",
            arguments={"role": "owner"},
        )
    )
    assert searches.asked == ["work"]
    assert name == "find_similar_work"


def test_a_commitment_without_a_resolved_committer_cannot_be_searched() -> None:
    """Not a fallback: an intent that reached here without a committer did not survive validation.

    `IntentValidator` puts `committed_by_person_id` there from a resolved participant and refuses
    the intent when it cannot (BR-AI-34, ADR-0052). Raising rather than degrading to a Work search
    keeps that guarantee falsifiable.
    """
    with pytest.raises(KeyError):
        _search(
            _Accepted(
                kind=IntentKind.CREATE_COMMITMENT,
                summary="Somebody will send the quote",
                arguments={"statement": "…"},
            )
        )


def test_every_search_declares_what_it_was_given_and_how_it_refuses() -> None:
    """The audit row has to say which question was asked.

    "Looked for duplicates" without saying where is not evidence that BR-AI-05 was satisfied — and
    "similar work already exists" on a commitment refusal is how the category error stayed
    invisible for three checkpoints.
    """
    assert set(_DUPLICATE_SEARCH_KEYS) == set(_DUPLICATE_REFUSAL)
    assert _DUPLICATE_SEARCH_KEYS["find_similar_commitments"] == [
        "statement",
        "committed_by_person_id",
    ]
    assert "work" not in _DUPLICATE_REFUSAL["find_similar_commitments"]
