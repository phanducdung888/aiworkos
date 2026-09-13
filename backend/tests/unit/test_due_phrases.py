"""Reading a quoted deadline, and the many times it declines to (ADR-0055, BR-C-05).

The rule this file exists to hold: **the system never invents a date.** A model quotes the words
that name a deadline; `read_due_phrase` turns those words into a date only when they name one
unambiguously, against the day the promise was made. Everything else is `vague` with no date —
which is a complete answer, not a failure, because a date nobody set produces a missed-deadline
alert about a deadline that never existed.

Every case below fixes the reference day explicitly. A test that used today's date would pass on a
Tuesday and fail on a Sunday, which is exactly the class of defect this design removes from
production.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.contexts.commitment.domain import (
    MAX_DUE_PHRASE,
    DuePrecision,
    read_due_phrase,
)

#: Saturday 12 September 2026. Chosen so "by Friday" (the 18th) and "next Monday" (the 21st) land
#: in different weeks, and so the end of the week (the 13th) is tomorrow.
SATURDAY = dt.date(2026, 9, 12)
WEDNESDAY = dt.date(2026, 9, 16)


#: What "we could not place this deadline" looks like as a pair.
NOTHING = (None, DuePrecision.VAGUE)


def read(phrase: str | None, reference: dt.date = SATURDAY) -> tuple[dt.date | None, DuePrecision]:
    """The reading as a pair, because every assertion below is about both halves at once."""
    reading = read_due_phrase(phrase, reference=reference)
    assert reading == NOTHING or reading.date is not None or reading.precision is (
        DuePrecision.VAGUE
    )
    return reading.date, reading.precision


class TestExact:
    """Only three things are exact: a calendar date, today, and tomorrow."""

    def test_an_iso_date(self) -> None:
        assert read("on 2026-09-18") == (dt.date(2026, 9, 18), DuePrecision.EXACT)

    def test_today(self) -> None:
        assert read("today") == (SATURDAY, DuePrecision.EXACT)

    def test_tomorrow(self) -> None:
        assert read("tomorrow") == (dt.date(2026, 9, 13), DuePrecision.EXACT)

    def test_a_well_formed_string_that_is_not_a_day(self) -> None:
        """2026-02-30 parses as a pattern and is not a date, so it is not a deadline."""
        assert read("by 2026-02-30") == NOTHING


class TestWeekdays:
    """A named weekday is `week`, not `exact` — the domain's own example is "by Friday"."""

    def test_the_coming_weekday(self) -> None:
        assert read("by Friday") == (dt.date(2026, 9, 18), DuePrecision.WEEK)

    def test_today_counts_as_that_weekday(self) -> None:
        """"Saturday" said on a Saturday is today. A promise about the end of today is ordinary."""
        assert read("by Saturday") == (SATURDAY, DuePrecision.WEEK)

    def test_next_weekday_skips_this_week(self) -> None:
        # Monday the 14th is this coming Monday; "next Monday" is the 21st. The other reading
        # would quietly bring a deadline forward by a week.
        assert read("next Monday") == (dt.date(2026, 9, 21), DuePrecision.WEEK)

    def test_next_beats_the_bare_weekday(self) -> None:
        assert read("next Friday") == (dt.date(2026, 9, 25), DuePrecision.WEEK)

    def test_case_and_spacing_do_not_matter(self) -> None:
        assert read("  BY   FRIDAY  ") == read("by friday")


class TestWeeksAndMonths:
    def test_end_of_this_week_is_sunday(self) -> None:
        assert read("end of the week") == (dt.date(2026, 9, 13), DuePrecision.WEEK)

    def test_next_week_is_the_sunday_after(self) -> None:
        assert read("next week") == (dt.date(2026, 9, 20), DuePrecision.WEEK)

    def test_sometime_next_week_is_still_next_week(self) -> None:
        """Bounded, not exact. The vagueness is in the precision, not in dropping the date."""
        assert read("sometime next week") == (dt.date(2026, 9, 20), DuePrecision.WEEK)

    def test_end_of_the_month(self) -> None:
        assert read("by the end of the month") == (dt.date(2026, 9, 30), DuePrecision.MONTH)

    def test_next_month_is_the_end_of_the_next_one(self) -> None:
        assert read("next month") == (dt.date(2026, 10, 31), DuePrecision.MONTH)

    def test_month_end_arithmetic_survives_february(self) -> None:
        assert read("end of the month", dt.date(2028, 2, 3)) == (
            dt.date(2028, 2, 29),
            DuePrecision.MONTH,
        )

    def test_next_month_from_december(self) -> None:
        assert read("next month", dt.date(2026, 12, 20)) == (
            dt.date(2027, 1, 31),
            DuePrecision.MONTH,
        )


class TestDeclining:
    """The important half. Each of these is a real deadline the system cannot place."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "before the meeting",
            "soon",
            "as soon as possible",
            "when finance signs off",
            "in a couple of days",
            "shortly",
            "",
            "   ",
        ],
    )
    def test_an_unplaceable_phrase_yields_no_date(self, phrase: str) -> None:
        assert read(phrase) == NOTHING

    def test_no_phrase_at_all(self) -> None:
        assert read(None) == NOTHING

    def test_a_date_before_the_promise_is_refused(self) -> None:
        """Far likelier a date the message mentioned than a deadline somebody set in the past."""
        assert read("by 2020-01-01") == NOTHING

    def test_a_long_quote_is_not_a_deadline_phrase(self) -> None:
        """Where negation hides. "I will not manage this by Friday" contains "by Friday"."""
        sentence = "I will not manage this by Friday because finance has not signed off"
        assert len(sentence) > MAX_DUE_PHRASE
        assert read(sentence) == NOTHING

    def test_the_length_limit_is_the_only_thing_between_those(self) -> None:
        # The same words, short enough to be a deadline phrase, do resolve. Stated so the limit is
        # visibly the control rather than an accident of the corpus.
        assert read("by Friday")[0] is not None


class TestDeterminism:
    """Same message, same answer — on every run, in every process, forever."""

    def test_the_same_words_in_a_later_week_mean_a_later_day(self) -> None:
        # Both of these are "the coming Friday"; which Friday that is depends entirely on when the
        # promise was made, which is the whole reason `occurred_at` is the anchor.
        assert read("by Friday", SATURDAY) == (dt.date(2026, 9, 18), DuePrecision.WEEK)
        assert read("by Friday", dt.date(2026, 9, 19)) == (
            dt.date(2026, 9, 25),
            DuePrecision.WEEK,
        )

    def test_days_inside_one_week_agree(self) -> None:
        """Wednesday and the Saturday before it both look forward to the same Friday."""
        assert read("by Friday", WEDNESDAY) == read("by Friday", SATURDAY)

    def test_repeated_reads_agree(self) -> None:
        assert [read("next week") for _ in range(5)].count(read("next week")) == 5

    def test_every_resolved_date_is_on_or_after_the_promise(self) -> None:
        for phrase in (
            "today",
            "tomorrow",
            "by Monday",
            "by Sunday",
            "next week",
            "next month",
            "end of the month",
        ):
            for reference in (SATURDAY, WEDNESDAY, dt.date(2026, 12, 31)):
                date, _precision = read(phrase, reference)
                assert date is None or date >= reference, phrase


class TestPrecisionAgreesWithTheDomain:
    def test_a_precision_other_than_vague_always_carries_a_date(self) -> None:
        """BR-C-01 refuses the other combination, so producing it would be a latent 422."""
        for phrase in ("today", "by Friday", "next week", "next month", "nonsense at all"):
            date, precision = read(phrase)
            if precision is not DuePrecision.VAGUE:
                assert date is not None, phrase
            else:
                assert date is None, phrase


class TestACalendarDayItCannotPlace:
    """CP24. A phrase that names a day and a month is declined, not answered by a weekday rule.

    The failure was silent and the wrong direction. A real message read "I will finish the
    migration runbook and send it to you by Friday 18 September", the model quoted the whole
    phrase, and `_WEEKDAY` matched "Friday" — resolving to the Friday of the week the message was
    sent, the 11th. Seven days early, `week` precision, and nothing anywhere saying the date had
    been guessed.
    """

    REFERENCE = dt.date(2026, 9, 11)  # itself a Friday, which is what made it invisible

    @pytest.mark.parametrize(
        "phrase",
        [
            "by Friday 18 September",
            "Friday, September 18th",
            "18 September",
            "September 18",
            "by the 18th of September",
            "by Monday 5 Oct",
        ],
    )
    def test_it_is_vague_rather_than_a_date_nobody_agreed_to(self, phrase: str) -> None:
        reading = read_due_phrase(phrase, reference=self.REFERENCE)
        assert reading.date is None
        assert reading.precision is DuePrecision.VAGUE

    def test_an_iso_date_still_reads(self) -> None:
        """The guard sits after the ISO branch, so the one calendar form this table *can* read is
        untouched."""
        reading = read_due_phrase("by 2026-09-18", reference=self.REFERENCE)
        assert reading.date == dt.date(2026, 9, 18)
        assert reading.precision is DuePrecision.EXACT

    @pytest.mark.parametrize(
        ("phrase", "expected"),
        [
            ("by Friday", dt.date(2026, 9, 11)),
            ("next Friday", dt.date(2026, 9, 18)),
            ("it may be ready by Friday", dt.date(2026, 9, 11)),
        ],
    )
    def test_the_relative_readings_are_unchanged(self, phrase: str, expected: dt.date) -> None:
        """A day number is required, so "may" the modal is not May the month and the guard stays
        narrow enough to change nothing that already worked."""
        assert read_due_phrase(phrase, reference=self.REFERENCE).date == expected


class TestVietnamese:
    """CP26. The same table, in the language the mailbox is actually written in.

    Extraction already worked: a real Vietnamese message produced a Proposal quoting "Tôi sẽ gửi
    bản kế hoạch triển khai cho dự án Huế IOC" at full confidence. The deadline beside it did not,
    because the model quotes and *this* table reads (ADR-0055) — so every commitment from that
    mailbox came out dateless, nothing was ever overdue, and the Attention screen stayed empty
    however much mail arrived.
    """

    SUNDAY = dt.date(2026, 9, 13)
    WEDNESDAY = dt.date(2026, 9, 16)

    @pytest.mark.parametrize(
        ("phrase", "expected"),
        [
            ("thứ hai", dt.date(2026, 9, 14)),
            ("thứ ba", dt.date(2026, 9, 15)),
            ("thứ tư", dt.date(2026, 9, 16)),
            ("thứ năm", dt.date(2026, 9, 17)),
            ("thứ sáu", dt.date(2026, 9, 18)),
            ("thứ bảy", dt.date(2026, 9, 19)),
            ("chủ nhật", dt.date(2026, 9, 13)),
        ],
    )
    def test_every_weekday_reads(self, phrase: str, expected: dt.date) -> None:
        assert read_due_phrase(phrase, reference=self.SUNDAY).date == expected

    @pytest.mark.parametrize("phrase", ["thu sau", "thứ 6", "thu 6", "THỨ SÁU"])
    def test_the_spellings_people_actually_use(self, phrase: str) -> None:
        """Accented and not, named and numbered. The same thread contains both, and a reader that
        understood one would be right about half a mailbox."""
        assert read_due_phrase(phrase, reference=self.SUNDAY).date == dt.date(2026, 9, 18)

    @pytest.mark.parametrize(
        ("phrase", "expected"),
        [
            # The modifier follows the day in Vietnamese, which is why each needs its own rule:
            # "tuần này" alone means the end of the week, and that is not what naming a day meant.
            ("thứ sáu tuần này", dt.date(2026, 9, 18)),
            ("trước thứ Sáu tuần này", dt.date(2026, 9, 18)),
            ("thứ sáu tuần sau", dt.date(2026, 9, 25)),
            ("thứ sáu tuần tới", dt.date(2026, 9, 25)),
        ],
    )
    def test_a_weekday_qualified_by_a_week(self, phrase: str, expected: dt.date) -> None:
        assert read_due_phrase(phrase, reference=self.SUNDAY).date == expected

    @pytest.mark.parametrize(
        ("phrase", "expected", "precision"),
        [
            ("hôm nay", dt.date(2026, 9, 16), DuePrecision.EXACT),
            ("ngày mai", dt.date(2026, 9, 17), DuePrecision.EXACT),
            ("tuần này", dt.date(2026, 9, 20), DuePrecision.WEEK),
            ("tuần sau", dt.date(2026, 9, 27), DuePrecision.WEEK),
            ("cuối tuần", dt.date(2026, 9, 20), DuePrecision.WEEK),
            ("cuối tháng", dt.date(2026, 9, 30), DuePrecision.MONTH),
            ("tháng sau", dt.date(2026, 10, 31), DuePrecision.MONTH),
        ],
    )
    def test_the_relative_forms(
        self, phrase: str, expected: dt.date, precision: DuePrecision
    ) -> None:
        reading = read_due_phrase(phrase, reference=self.WEDNESDAY)
        assert reading.date == expected
        assert reading.precision is precision

    def test_a_bare_mai_is_not_tomorrow(self) -> None:
        """`mai` alone is also a very common given name. This module would rather lose a deadline
        than turn "gửi cho Mai" into tomorrow."""
        assert read_due_phrase("gửi cho Mai", reference=self.SUNDAY).date is None

    def test_the_real_message_that_prompted_this(self) -> None:
        """Verbatim from the pilot mailbox, and the reason CP26 did this at all."""
        reading = read_due_phrase("trước thứ Sáu tuần này", reference=self.SUNDAY)
        assert reading.date == dt.date(2026, 9, 18)
        assert reading.precision is DuePrecision.WEEK


class TestANumericDate:
    """`20/09/2026`, read only when it can mean one thing.

    Day-first and month-first are both in use and a quoted phrase says which only by accident. The
    ambiguous half is declined, because four months of error is not worth the convenience — and the
    great majority of real dates name a day past the twelfth, so most of them still read.
    """

    REFERENCE = dt.date(2026, 9, 13)

    @pytest.mark.parametrize(
        ("phrase", "expected"),
        [
            ("20/09/2026", dt.date(2026, 9, 20)),
            ("ngày 20/09/2026", dt.date(2026, 9, 20)),
            ("20-09-2026", dt.date(2026, 9, 20)),
            ("20.09.2026", dt.date(2026, 9, 20)),
            # The mirror case: month first is unambiguous when the *second* number cannot be one.
            ("09/20/2026", dt.date(2026, 9, 20)),
        ],
    )
    def test_an_unambiguous_date_reads_exactly(self, phrase: str, expected: dt.date) -> None:
        reading = read_due_phrase(phrase, reference=self.REFERENCE)
        assert reading.date == expected
        assert reading.precision is DuePrecision.EXACT

    @pytest.mark.parametrize("phrase", ["05/09/2026", "01/02/2026", "12/11/2026"])
    def test_an_ambiguous_date_is_declined(self, phrase: str) -> None:
        assert read_due_phrase(phrase, reference=self.REFERENCE).date is None

    @pytest.mark.parametrize("phrase", ["31/02/2026", "32/01/2026"])
    def test_a_well_formed_string_that_is_not_a_day(self, phrase: str) -> None:
        assert read_due_phrase(phrase, reference=self.REFERENCE).date is None

    def test_a_date_in_the_past_is_still_refused(self) -> None:
        """The rule that predates all of this: far likelier a quotation of something the message
        mentioned than a deadline somebody set backwards."""
        assert read_due_phrase("20/09/2020", reference=self.REFERENCE).date is None
