"""A reservation that can no longer complete (CP25).

Reserving an attachment writes a row and issues a presigned URL with an expiry. If the upload never
happens, the row sits at `pending` and the URL dies — and CP24 left four of those in the pilot
database with nothing in the system able to say what they meant.

`pending` for ever tells a person to keep waiting for a file that is not coming. This is the rule
that turns it into an answer, and it is a pure function of two columns and the clock so that being
true does not depend on anything being scheduled (CLAUDE.md rule 5).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.contexts.signal.public import (
    UPLOAD_WINDOW,
    AttachmentStatus,
    effective_attachment_status,
)

RESERVED = dt.datetime(2026, 9, 13, 9, 0, tzinfo=dt.UTC)


def status(written: str, *, after: dt.timedelta) -> str:
    return effective_attachment_status(
        written, RESERVED, window=UPLOAD_WINDOW, now=RESERVED + after
    )


class TestAReservationAges:
    def test_inside_the_window_it_is_still_pending(self) -> None:
        assert status("pending", after=UPLOAD_WINDOW / 2) == AttachmentStatus.PENDING

    def test_exactly_at_the_window_it_is_still_pending(self) -> None:
        """The boundary is inclusive, because a URL valid for fifteen minutes is valid at
        fifteen minutes."""
        assert status("pending", after=UPLOAD_WINDOW) == AttachmentStatus.PENDING

    def test_past_the_window_it_is_expired(self) -> None:
        assert status("pending", after=UPLOAD_WINDOW * 2) == AttachmentStatus.EXPIRED

    def test_long_past_it_is_still_just_expired(self) -> None:
        assert status("pending", after=dt.timedelta(days=400)) == AttachmentStatus.EXPIRED


class TestWhatTimeDoesNotChange:
    @pytest.mark.parametrize("written", ["available", "purged"])
    def test_a_settled_attachment_is_never_reinterpreted(self, written: str) -> None:
        """`available` and `purged` are facts about what happened. No amount of elapsed time
        makes a file that arrived un-arrive."""
        assert status(written, after=dt.timedelta(days=400)) == written

    def test_an_unknown_status_is_returned_untouched(self) -> None:
        """A status this function does not know is not something to guess about."""
        assert status("quarantined", after=dt.timedelta(days=400)) == "quarantined"


def test_expired_is_reported_and_never_stored() -> None:
    """The column's CHECK constraint permits three values and this is not one of them.

    Written down, it would need a migration, a sweeper to do the writing, and a reason to trust
    that the sweeper had run. Derived, it is true the moment it is true.
    """
    assert AttachmentStatus.EXPIRED.value not in {"pending", "available", "purged"}
