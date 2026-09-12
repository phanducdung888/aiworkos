"""Spans are located by the quote, not by the model's arithmetic.

Every case in the first class is **real data**, captured from a GPT-4o run during the CP13
verification. The model quoted correctly and counted badly: the first span's end was nine
characters short, and the second span's offsets had drifted by nine at both ends. Slicing the text
at those offsets produced a correct sentence cut off mid-word — `'...once finance has si'` — which
satisfies BR-E-05 mechanically (the excerpt really is what the text says there) and is a citation a
reviewer would read as broken.

The fix trusts the quote where the quote is verifiable. BR-E-05 is unchanged: the excerpt must
still be exactly what the text says at the recorded locator. What changed is that the locator now
agrees with the words the model actually quoted.
"""

from __future__ import annotations

import json

import pytest

from app.agent.providers.errors import (
    ProviderContractViolation,
    ProviderInvalidResponse,
)
from app.agent.providers.structured import parse_spans

#: The exact message sent to GPT-4o during verification. 160 characters.
MESSAGE = (
    "Hi Mai, thanks for the call earlier. I will send the revised quote on Friday "
    "once finance has signed off. Could you check whether the delivery date still works?"
)

#: What GPT-4o actually returned, verbatim. Summaries exact, offsets wrong.
REAL_RESPONSE = {
    "spans": [
        {
            "kind": "commitment",
            "summary": "I will send the revised quote on Friday once finance has signed off.",
            "char_start": 37,
            "char_end": 96,
            "confidence": 100,
        },
        {
            "kind": "work",
            "summary": "Could you check whether the delivery date still works?",
            "char_start": 97,
            "char_end": 149,
            "confidence": 100,
        },
    ]
}


def parse(payload: dict[str, object], text: str = MESSAGE, limit: int = 25):  # type: ignore[no-untyped-def]
    return parse_spans(json.dumps(payload), source_text=text, max_spans=limit)


class TestTheRealFailure:
    def test_the_recorded_offsets_really_were_wrong(self) -> None:
        """The premise, asserted so the rest of this file cannot be testing a fiction.

        If this ever stops failing, the captured data no longer reproduces what was seen and the
        regression below is guarding nothing.
        """
        first = REAL_RESPONSE["spans"][0]  # type: ignore[index]
        sliced = MESSAGE[first["char_start"] : first["char_end"]]  # type: ignore[index]
        assert sliced != first["summary"]  # type: ignore[index]
        assert sliced.endswith("has si"), "the real failure was a mid-word truncation"

    def test_the_first_span_is_realigned_to_its_quote(self) -> None:
        span = parse(REAL_RESPONSE)[0]
        assert MESSAGE[span.char_start : span.char_end] == span.summary
        assert (span.char_start, span.char_end) == (37, 105)

    def test_the_second_span_is_realigned_despite_cumulative_drift(self) -> None:
        """The second span was wrong at both ends, not just the end.

        Drift accumulates: a model that miscounts once tends to carry the error forward, so a fix
        that only corrected the end would have left this one starting mid-word.
        """
        span = parse(REAL_RESPONSE)[1]
        assert MESSAGE[span.char_start : span.char_end] == span.summary
        assert (span.char_start, span.char_end) == (106, 160)

    def test_no_span_is_cut_mid_word(self) -> None:
        """The symptom a reviewer would have seen."""
        for span in parse(REAL_RESPONSE):
            excerpt = MESSAGE[span.char_start : span.char_end]
            assert not excerpt.endswith(("si", "s", "da")) or excerpt == span.summary
            assert excerpt == span.summary


class TestRealignment:
    def test_correct_offsets_are_left_alone(self) -> None:
        """The common case must not be disturbed by the fix."""
        payload = {
            "spans": [
                {
                    "kind": "work",
                    "summary": "revised quote",
                    "char_start": MESSAGE.index("revised quote"),
                    "char_end": MESSAGE.index("revised quote") + len("revised quote"),
                    "confidence": 80,
                }
            ]
        }
        span = parse(payload)[0]
        assert (span.char_start, span.char_end) == (
            MESSAGE.index("revised quote"),
            MESSAGE.index("revised quote") + len("revised quote"),
        )

    def test_a_repeated_phrase_resolves_to_the_nearest_occurrence(self) -> None:
        """A phrase can legitimately appear twice, and the model's offsets are approximately right.

        Taking the first occurrence would move a correctly-located second quote to the top of the
        document — a worse error than the one being fixed, and a silent one.
        """
        text = "send the quote. and later, send the quote again."
        second = text.index("send the quote", 10)
        payload = {
            "spans": [
                {
                    "kind": "work",
                    "summary": "send the quote",
                    # Off by three, but unambiguously nearer the second occurrence.
                    "char_start": second + 3,
                    "char_end": second + 17,
                    "confidence": 80,
                }
            ]
        }
        span = parse(payload, text=text)[0]
        assert span.char_start == second

    def test_a_summary_not_in_the_text_keeps_the_model_offsets(self) -> None:
        """A paraphrase is not located; it is left where the model said and judged there.

        Inventing a position for a quote that does not appear would manufacture the fabricated
        citation BR-E-05 exists to refuse.
        """
        payload = {
            "spans": [
                {
                    "kind": "work",
                    "summary": "Nam promised to send something",
                    "char_start": 10,
                    "char_end": 20,
                    "confidence": 80,
                }
            ]
        }
        span = parse(payload)[0]
        assert (span.char_start, span.char_end) == (10, 20)
        assert MESSAGE[10:20] != span.summary

    def test_realignment_does_not_rescue_an_impossible_range(self) -> None:
        """BR-E-05's validation is unchanged and still runs."""
        payload = {
            "spans": [
                {
                    "kind": "work",
                    "summary": "not present anywhere",
                    "char_start": 50,
                    "char_end": 10,
                    "confidence": 80,
                }
            ]
        }
        with pytest.raises(ProviderContractViolation):
            parse(payload)

    def test_an_empty_summary_is_not_realigned_to_position_zero(self) -> None:
        """`"" in text` is true everywhere, which would silently anchor every empty quote at 0."""
        payload = {
            "spans": [
                {
                    "kind": "work",
                    "summary": "",
                    "char_start": 40,
                    "char_end": 50,
                    "confidence": 80,
                }
            ]
        }
        span = parse(payload)[0]
        assert (span.char_start, span.char_end) == (40, 50)


class TestExistingBehaviourIsUnchanged:
    def test_malformed_json_is_still_a_contract_failure(self) -> None:
        with pytest.raises(ProviderInvalidResponse):
            parse_spans("not json at all", source_text=MESSAGE, max_spans=25)

    def test_a_missing_field_is_still_refused(self) -> None:
        with pytest.raises(ProviderInvalidResponse):
            parse({"spans": [{"kind": "work"}]})

    def test_max_spans_is_still_respected(self) -> None:
        payload = {
            "spans": [
                {
                    "kind": "work",
                    "summary": "revised quote",
                    "char_start": 1,
                    "char_end": 2,
                    "confidence": 80,
                }
            ]
            * 10
        }
        assert len(parse(payload, limit=3)) == 3
