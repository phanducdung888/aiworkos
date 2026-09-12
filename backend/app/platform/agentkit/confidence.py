"""Confidence, with its provenance attached (ADR-0050).

Checkpoint 9 left `HIGH = 85` and `MIN_CONFIDENCE = 60` as bare integers chosen so the fake
provider's output crossed the threshold. The number was never the problem; the missing sentence
was. A threshold tuned against a mixture of calibrated probabilities, adapter heuristics and
defaults means nothing, and BR-AI-09 — "below the threshold produces no Proposal" — is only as
meaningful as the provenance of the number it compares.

So confidence is never a bare number here. It carries where it came from, and a confidence that
could not be assessed says so rather than becoming a plausible-looking score.
"""

from __future__ import annotations

import dataclasses
import enum


class ConfidenceSource(enum.StrEnum):
    """Where a confidence value came from. Recorded, never inferred."""

    #: The model reported a number and we are passing it through. Still not a calibrated
    #: probability — a model's self-reported confidence is a number it produced, not a measured
    #: frequency — but at least it is the model's own claim.
    PROVIDER_REPORTED = "provider_reported"
    #: An adapter derived it from something else: a pattern match, a finish reason, a length. An
    #: honest guess, labelled as one.
    HEURISTIC = "heuristic"
    #: The provider said nothing about confidence and nothing could be derived.
    UNAVAILABLE = "unavailable"


class ConfidenceBand(enum.StrEnum):
    """Coarse bands, because two decimal places would claim a precision nobody has."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    #: A first-class state, not an error and not a default. An extraction whose confidence could
    #: not be assessed is one the system has no grounds to act on — which is the same answer a
    #: genuinely low-confidence span gets, arrived at honestly rather than by picking a number.
    UNKNOWN = "unknown"


#: Band boundaries, as the lowest value belonging to each band.
#:
#: Deliberately not tuned: these are the CP9 numbers, carried forward unchanged so that this
#: checkpoint changes the *semantics* without silently also changing behaviour. Tuning them needs
#: outcome data that does not exist yet, and a value invented here would be exactly the
#: uncalibrated number ADR-0050 is about.
BAND_FLOOR: dict[ConfidenceBand, int] = {
    ConfidenceBand.HIGH: 85,
    ConfidenceBand.MEDIUM: 60,
    ConfidenceBand.LOW: 0,
}

_ORDER: tuple[ConfidenceBand, ...] = (
    ConfidenceBand.UNKNOWN,
    ConfidenceBand.LOW,
    ConfidenceBand.MEDIUM,
    ConfidenceBand.HIGH,
)


@dataclasses.dataclass(frozen=True, slots=True)
class ConfidenceAssessment:
    """A confidence, and the answer to "says who".

    `value` is absent when `source` is `UNAVAILABLE`. The two are kept consistent by
    `ConfidenceNormalizer`, which is the only thing that builds one of these.
    """

    band: ConfidenceBand
    source: ConfidenceSource
    value: int | None = None

    def __post_init__(self) -> None:
        if self.value is not None and not 0 <= self.value <= 100:
            raise ValueError("a confidence value is a percentage")
        if self.source is ConfidenceSource.UNAVAILABLE and self.value is not None:
            raise ValueError("an unavailable confidence has no value")
        if self.band is ConfidenceBand.UNKNOWN and self.value is not None:
            raise ValueError("an unknown band has no value")

    @property
    def is_known(self) -> bool:
        return self.band is not ConfidenceBand.UNKNOWN

    def at_least(self, band: ConfidenceBand) -> bool:
        """Whether this reaches `band`. `UNKNOWN` reaches nothing, including itself."""
        if self.band is ConfidenceBand.UNKNOWN or band is ConfidenceBand.UNKNOWN:
            return False
        return _ORDER.index(self.band) >= _ORDER.index(band)

    def as_json(self) -> dict[str, object]:
        return {"band": self.band.value, "source": self.source.value, "value": self.value}


#: What the system has when nothing could be assessed. Explicit rather than a `None` somewhere,
#: so the absence travels with its own explanation.
UNKNOWN_CONFIDENCE = ConfidenceAssessment(
    band=ConfidenceBand.UNKNOWN, source=ConfidenceSource.UNAVAILABLE
)


class ConfidenceNormalizer:
    """Provider score → assessment. The only place a number becomes a band.

    Provider-specific mapping lives here, outside the domain (ADR-0050), so changing how a vendor's
    output becomes a band never touches Proposal, Approval or the Tool Gateway.
    """

    def from_value(
        self, value: int | None, *, source: ConfidenceSource
    ) -> ConfidenceAssessment:
        """Normalize one reported number.

        A missing value is `UNKNOWN`, always, whatever the source claimed. There is no default, no
        midpoint and no "assume it meant to say something" — inventing a score at exactly the value
        that decides the outcome is the failure this whole module exists to prevent.
        """
        if value is None or source is ConfidenceSource.UNAVAILABLE:
            return UNKNOWN_CONFIDENCE
        clamped = max(0, min(100, value))
        for band in (ConfidenceBand.HIGH, ConfidenceBand.MEDIUM, ConfidenceBand.LOW):
            if clamped >= BAND_FLOOR[band]:
                return ConfidenceAssessment(band=band, source=source, value=clamped)
        return UNKNOWN_CONFIDENCE  # pragma: no cover - LOW's floor is 0


@dataclasses.dataclass(frozen=True, slots=True)
class ConfidencePolicy:
    """BR-AI-09's threshold, in one place.

    The runtime asks this rather than comparing integers, so "what counts as confident enough" is a
    decision with a name instead of a number repeated wherever somebody needed it.
    """

    minimum_band: ConfidenceBand = ConfidenceBand.MEDIUM

    def admits(self, assessment: ConfidenceAssessment) -> bool:
        """Whether an extraction at this confidence may become a Proposal.

        `UNKNOWN` is refused. Guessing quietly is worse than silence (BR-AI-09), and an
        unassessable confidence is the case where the system knows least about what it is doing.
        """
        return assessment.at_least(self.minimum_band)
