"""Confidence semantics (ADR-0050).

Checkpoint 9 left `HIGH = 85` and `MIN_CONFIDENCE = 60` as bare integers chosen so the fake
provider crossed the threshold. The numbers were never the problem — the missing sentence was, and
these tests are mostly about that sentence.

The one that matters most is `test_a_missing_confidence_never_becomes_a_score`. A provider that
reports nothing and an adapter that invents a plausible number are indistinguishable downstream,
and the invented number lands at exactly the value that decides the outcome.
"""

from __future__ import annotations

import pytest

from app.agent.providers.confidence import (
    BAND_FLOOR,
    UNKNOWN_CONFIDENCE,
    ConfidenceAssessment,
    ConfidenceBand,
    ConfidenceNormalizer,
    ConfidencePolicy,
    ConfidenceSource,
)

NORMALIZER = ConfidenceNormalizer()


class TestBands:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (100, ConfidenceBand.HIGH),
            (85, ConfidenceBand.HIGH),
            (84, ConfidenceBand.MEDIUM),
            (60, ConfidenceBand.MEDIUM),
            (59, ConfidenceBand.LOW),
            (0, ConfidenceBand.LOW),
        ],
    )
    def test_a_value_lands_in_its_band(
        self, value: int, expected: ConfidenceBand
    ) -> None:
        assessment = NORMALIZER.from_value(
            value, source=ConfidenceSource.PROVIDER_REPORTED
        )
        assert assessment.band is expected
        assert assessment.value == value

    def test_the_floors_are_the_checkpoint_nine_numbers(self) -> None:
        """Carried forward unchanged on purpose.

        This checkpoint changes what the numbers *mean*, and changing the numbers at the same time
        would make it impossible to tell which of the two moved the behaviour.
        """
        assert BAND_FLOOR[ConfidenceBand.HIGH] == 85
        assert BAND_FLOOR[ConfidenceBand.MEDIUM] == 60

    def test_a_value_outside_the_range_is_clamped(self) -> None:
        assert NORMALIZER.from_value(
            250, source=ConfidenceSource.PROVIDER_REPORTED
        ).value == 100


class TestUnknown:
    def test_a_missing_confidence_never_becomes_a_score(self) -> None:
        """The failure this whole module exists to prevent.

        Not HIGH, not a default, not a midpoint. An adapter that filled in a number would put an
        invented value at exactly the point that decides whether a Proposal is raised.
        """
        assessment = NORMALIZER.from_value(
            None, source=ConfidenceSource.PROVIDER_REPORTED
        )
        assert assessment.band is ConfidenceBand.UNKNOWN
        assert assessment.value is None
        assert assessment.source is ConfidenceSource.UNAVAILABLE

    def test_an_unavailable_source_yields_unknown_whatever_the_value(self) -> None:
        assert (
            NORMALIZER.from_value(95, source=ConfidenceSource.UNAVAILABLE)
            is UNKNOWN_CONFIDENCE
        )

    def test_unknown_reaches_no_band_including_itself(self) -> None:
        """"At least unknown" is not a threshold anything should pass."""
        for band in ConfidenceBand:
            assert not UNKNOWN_CONFIDENCE.at_least(band)

    def test_unknown_is_not_known(self) -> None:
        assert not UNKNOWN_CONFIDENCE.is_known
        assert NORMALIZER.from_value(
            70, source=ConfidenceSource.HEURISTIC
        ).is_known

    def test_an_unknown_band_cannot_carry_a_value(self) -> None:
        """The type refuses the inconsistent state rather than trusting callers to avoid it."""
        with pytest.raises(ValueError, match="unknown band"):
            ConfidenceAssessment(
                band=ConfidenceBand.UNKNOWN,
                source=ConfidenceSource.PROVIDER_REPORTED,
                value=90,
            )

    def test_an_unavailable_source_cannot_carry_a_value(self) -> None:
        with pytest.raises(ValueError, match="unavailable"):
            ConfidenceAssessment(
                band=ConfidenceBand.HIGH,
                source=ConfidenceSource.UNAVAILABLE,
                value=90,
            )


class TestSource:
    def test_a_heuristic_says_it_is_a_heuristic(self) -> None:
        """Nothing in this system presents a derived number as a calibrated probability."""
        assessment = NORMALIZER.from_value(90, source=ConfidenceSource.HEURISTIC)
        assert assessment.source is ConfidenceSource.HEURISTIC
        assert assessment.band is ConfidenceBand.HIGH

    def test_the_source_survives_into_the_record(self) -> None:
        rendered = NORMALIZER.from_value(
            90, source=ConfidenceSource.HEURISTIC
        ).as_json()
        assert rendered == {"band": "high", "source": "heuristic", "value": 90}

    def test_a_provider_score_and_a_heuristic_are_distinguishable(self) -> None:
        """Same number, different claims — and an evaluation must be able to separate them."""
        reported = NORMALIZER.from_value(90, source=ConfidenceSource.PROVIDER_REPORTED)
        derived = NORMALIZER.from_value(90, source=ConfidenceSource.HEURISTIC)
        assert reported.band is derived.band
        assert reported.source is not derived.source


class TestPolicy:
    def test_the_threshold_lives_in_one_place(self) -> None:
        policy = ConfidencePolicy(minimum_band=ConfidenceBand.MEDIUM)
        assert policy.admits(NORMALIZER.from_value(90, source=ConfidenceSource.HEURISTIC))
        assert policy.admits(NORMALIZER.from_value(60, source=ConfidenceSource.HEURISTIC))
        assert not policy.admits(
            NORMALIZER.from_value(59, source=ConfidenceSource.HEURISTIC)
        )

    def test_unknown_is_refused(self) -> None:
        """BR-AI-09. The case where the system knows least about what it is doing."""
        assert not ConfidencePolicy().admits(UNKNOWN_CONFIDENCE)

    def test_a_stricter_policy_refuses_medium(self) -> None:
        strict = ConfidencePolicy(minimum_band=ConfidenceBand.HIGH)
        assert not strict.admits(
            NORMALIZER.from_value(70, source=ConfidenceSource.PROVIDER_REPORTED)
        )
        assert strict.admits(
            NORMALIZER.from_value(90, source=ConfidenceSource.PROVIDER_REPORTED)
        )
