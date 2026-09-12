"""What every provider asks for, and how every provider's answer is read.

Vendors differ in their URL, their headers, the shape of the envelope and the words they use for
"the model stopped". They do not differ in what this system needs from them: spans that index into
the text we sent, with a confidence the model either reported or did not.

That part lives here, once. Duplicating it per adapter would mean two places enforcing BR-E-05's
span contract, and they would drift the first time one was fixed — which is a worse outcome than
the coupling, because the thing that drifts is a safety rule.

What is deliberately *not* here: anything that knows a vendor. No URL, no header, no field name, no
stop-reason vocabulary. An adapter extracts its own text from its own envelope and hands the plain
string to `parse_spans`.
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.providers.errors import (
    ProviderContractViolation,
    ProviderInvalidResponse,
)
from app.agent.providers.port import ExtractedSpan
from app.platform.agentkit.confidence import (
    UNKNOWN_CONFIDENCE,
    ConfidenceAssessment,
    ConfidenceNormalizer,
    ConfidenceSource,
)

#: What the model is asked to return, appended to every provider's system prompt.
#:
#: Constrained to what BR-E-05 can verify: a span the excerpt can be sliced from, not a summary
#: that would read well and be unverifiable. The word "JSON" appears deliberately — some providers
#: require it in the prompt before they will honour a JSON response format.
SCHEMA_INSTRUCTION = (
    'Return JSON only: {"spans": [{"kind": "commitment"|"work", '
    '"summary": string, "char_start": int, "char_end": int, '
    '"confidence": int 0-100, "due_phrase": string|null}]}. '
    "char_start and char_end must index the supplied text exactly. "
    # The model quotes; it never calculates (ADR-0055). Asking for a date would be asking for
    # arithmetic against a "today" the model does not know, and it would answer anyway.
    'due_phrase must be copied verbatim from the text, short, and only the words naming the '
    'deadline ("by Friday", "next week"). Use null when no deadline is stated. '
    "Never a date you worked out yourself. "
    "Return an empty list rather than guessing."
)

_NORMALIZER = ConfidenceNormalizer()


def assess_confidence(value: Any) -> ConfidenceAssessment:
    """A model's reported number, labelled as the model's own (ADR-0050).

    A self-reported confidence is a value the model produced, not a measured frequency — which is
    what `PROVIDER_REPORTED` says and `HEURISTIC` would not. A field that is absent or is not a
    number is `UNKNOWN`: inventing a score at the value that decides the outcome is the failure
    ADR-0050 exists to prevent.
    """
    if value is None:
        return UNKNOWN_CONFIDENCE
    try:
        return _NORMALIZER.from_value(
            int(value), source=ConfidenceSource.PROVIDER_REPORTED
        )
    except (TypeError, ValueError):
        return UNKNOWN_CONFIDENCE


def _realign(summary: str, source_text: str, claimed_start: int) -> tuple[int, int] | None:
    """Where `summary` actually appears in `source_text`, if it appears exactly.

    Models are reliably good at quoting and reliably bad at counting characters. A real GPT-4o run
    returned the right words with offsets nine characters short, drifting further on the second
    span — so the excerpt sliced at those offsets was a correct sentence cut off mid-word.

    When the model's own summary is an exact substring, it is better evidence of where it meant
    than its arithmetic is. The occurrence *nearest the claimed position* is chosen rather than the
    first: a phrase can legitimately repeat, and the model's offsets are approximately right even
    when they are not exactly right, so proximity is the signal that survives the miscounting.

    Returns None when the summary is not an exact substring — a paraphrase, or a quote from
    somewhere other than the text we sent. The model's own offsets are then left alone and the
    existing validation decides, because inventing a location for a quote we cannot find would be
    exactly the fabricated citation BR-E-05 exists to refuse.
    """
    if not summary:
        # An empty string is a substring of everything, so realigning one would collapse it to a
        # zero-length range at whatever position the model named. It is not a quote; leave it where
        # it was and let the existing validation refuse it for what it is.
        return None

    positions = []
    start = source_text.find(summary)
    while start != -1:
        positions.append(start)
        start = source_text.find(summary, start + 1)
    if not positions:
        return None
    best = min(positions, key=lambda p: abs(p - claimed_start))
    return best, best + len(summary)


def parse_spans(
    payload: str, *, source_text: str, max_spans: int
) -> tuple[ExtractedSpan, ...]:
    """Read a provider's JSON answer into spans, or refuse it.

    `payload` is the model's output with the envelope already removed — each adapter knows where
    its own vendor keeps the string, and nothing about that reaches here. `source_text` is what was
    sent to the model, used to check the quoting against reality rather than trusting the model's
    arithmetic (see `_realign`).

    Two different refusals, and the distinction is load-bearing (ADR-0049):

    * `ProviderInvalidResponse` — the answer could not be read. A broken integration, not a model
      that was unsure. Collapsing the two would make an outage indistinguishable from a quiet day.
    * `ProviderContractViolation` — the answer was read and said something it was not allowed to
      say. A span that is not a range is not a low-confidence span; it is a wrong one.
    """
    try:
        parsed = json.loads(payload)
        raw_spans = parsed["spans"]
    except (ValueError, KeyError, TypeError) as error:
        raise ProviderInvalidResponse(
            "the provider did not return the requested JSON shape"
        ) from error

    if not isinstance(raw_spans, list):
        raise ProviderInvalidResponse("`spans` is not a list")

    spans: list[ExtractedSpan] = []
    for raw in raw_spans[:max_spans]:
        try:
            start, end = int(raw["char_start"]), int(raw["char_end"])
            kind, summary = str(raw["kind"]), str(raw["summary"])
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderInvalidResponse("a span was missing required fields") from error
        # Trust the quote over the arithmetic where the quote is verifiable. BR-E-05 is unchanged
        # and unrelaxed: the excerpt must still be what the text says at the recorded locator —
        # this makes the locator agree with the words the model actually quoted, rather than
        # accepting a correct sentence sliced at the wrong place.
        realigned = _realign(summary, source_text, start)
        if realigned is not None:
            start, end = realigned

        if not 0 <= start < end:
            raise ProviderContractViolation(f"span [{start}:{end}] is not a range")
        spans.append(
            ExtractedSpan(
                kind=kind,
                summary=summary,
                char_start=start,
                char_end=end,
                confidence=assess_confidence(raw.get("confidence")),
                attributes=_attributes(raw),
            )
        )
    return tuple(spans)
def _attributes(raw: dict[str, Any]) -> dict[str, Any]:
    """Optional structured details, carried without being understood (ADR-0055).

    Additive by construction: a provider that answers the older schema omits the key and gets an
    empty dict, which is what every existing caller already handles. Nothing here interprets the
    value — `due_phrase` is a quotation on its way to WorkOS, and reading it is a domain decision
    made somewhere an untrusted answer cannot reach.

    A non-string, or a string of whitespace, is dropped rather than passed on. The absence of a
    deadline and an unreadable one are the same outcome downstream, and dropping it here means the
    validator's input is always either a quote or nothing.
    """
    phrase = raw.get("due_phrase")
    if isinstance(phrase, str) and phrase.strip():
        return {"due_phrase": phrase.strip()}
    return {}
