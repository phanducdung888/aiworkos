"""The loop's own parsing and settings — the parts that need no mail server.

`test_mailbox` drives all of this against a real server, but only when somebody turns it on. These
run always, and exist because the defect they pin was invisible to every test the connector had
when CP20 shipped.
"""

from __future__ import annotations

import datetime as dt

import pytest

from connectors.imap.auth import StaticToken
from connectors.imap.run import (
    Settings,
    _internaldate,
    _optional_float,
    _unpack,
)


class TestInternaldate:
    """The fallback timestamp, and the offset bug a real mail server found.

    `imaplib.Internaldate2tuple` returns a **local-time** struct for the correct instant. Treating
    those fields as UTC — which the connector did until it was pointed at GreenMail — shifts every
    fallback timestamp by the connector host's own offset. On a UTC machine that is invisible; on
    the machine this was found on it was seven hours, and CP15 reads deadlines against
    `occurred_at` (ADR-0055), so it would have become a wrong date on somebody's promise.

    The second case is the one that fails under the old code on *any* host, which is what makes
    this a regression test rather than a test that happened to catch something once.
    """

    def test_a_utc_internaldate_is_that_instant(self) -> None:
        header = b'1 (INTERNALDATE "13-Sep-2026 03:41:27 +0000" BODY[] {449}'
        assert _internaldate(header) == dt.datetime(2026, 9, 13, 3, 41, 27, tzinfo=dt.UTC)

    def test_an_offset_internaldate_is_converted_not_relabelled(self) -> None:
        header = b'1 (INTERNALDATE "13-Sep-2026 10:41:27 +0700" BODY[] {449}'
        assert _internaldate(header) == dt.datetime(2026, 9, 13, 3, 41, 27, tzinfo=dt.UTC)

    def test_the_two_spellings_of_one_instant_agree(self) -> None:
        assert _internaldate(
            b'1 (INTERNALDATE "13-Sep-2026 10:41:27 +0700" BODY[] {1}'
        ) == _internaldate(b'1 (INTERNALDATE "13-Sep-2026 03:41:27 +0000" BODY[] {1}')

    @pytest.mark.parametrize(
        "header",
        [
            b"1 (BODY[] {449}",
            b'1 (INTERNALDATE "not a date" BODY[] {449}',
            b"",
        ],
    )
    def test_an_unusable_header_yields_nothing_rather_than_a_guess(
        self, header: bytes
    ) -> None:
        assert _internaldate(header) is None

    def test_a_header_of_the_wrong_type_yields_nothing(self) -> None:
        assert _internaldate(None) is None
        assert _internaldate(42) is None


class TestUnpack:
    """imaplib's fetch response is a list of tuples and loose bytes, in no promised order."""

    def test_the_message_and_its_receipt_time_are_found(self) -> None:
        fetched = [
            (b'1 (INTERNALDATE "13-Sep-2026 03:41:27 +0000" BODY[] {7}', b"a body\n"),
            b")",
        ]
        raw, received_at = _unpack(fetched)
        assert raw == b"a body\n"
        assert received_at == dt.datetime(2026, 9, 13, 3, 41, 27, tzinfo=dt.UTC)

    def test_a_response_with_no_body_is_not_a_message(self) -> None:
        assert _unpack([b")"]) == (None, None)
        assert _unpack([]) == (None, None)


class TestSettings:
    def a_setting(self, **over: object) -> Settings:
        fields: dict[str, object] = {
            "host": "imap.example.test",
            "username": "ingest@example.test",
            "password": "secret",
            "workos_url": "https://workos.example.test",
            "tokens": StaticToken("token"),
            "organization_id": "org",
        }
        fields.update(over)
        return Settings(**fields)  # type: ignore[arg-type]

    def test_implicit_tls_is_the_default(self) -> None:
        """A mailbox password in clear text is not something to arrive at by leaving a setting
        unset, so the safe value is the one you get for free."""
        settings = self.a_setting()
        assert settings.security == "ssl"
        assert settings.port == 993

    @pytest.mark.parametrize("security", ["ssl", "starttls", "none"])
    def test_the_three_ways_to_connect(self, security: str) -> None:
        assert self.a_setting(security=security).security == security

    def test_an_unknown_security_setting_is_refused_at_startup(self) -> None:
        """Rather than at the first connection, which is after the credential has been read."""
        with pytest.raises(SystemExit):
            self.a_setting(security="probably-fine")

    def test_one_pass_is_the_default(self) -> None:
        assert self.a_setting().interval is None


class TestOptionalFloat:
    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_absent_means_absent(self, raw: str | None) -> None:
        assert _optional_float(raw) is None

    def test_a_number_is_a_number(self) -> None:
        assert _optional_float("30") == 30.0


class TestTheService:
    """`--interval` turns one pass into a service (CP21)."""

    def test_it_stops_when_asked(self) -> None:
        """SIGTERM is how a container is asked to stop, and the pass in flight has to finish.

        A process killed between "WorkOS has it" and "mark seen" would redeliver, which the
        idempotency key makes harmless — but one killed the other way round would lose a message,
        and losing one is the failure with no recovery.
        """
        import threading

        from connectors.imap.run import RunReport, run_forever

        passes = []
        halt = threading.Event()

        def one_pass(settings: Settings, **_: object) -> RunReport:
            passes.append(1)
            if len(passes) == 3:
                halt.set()
            return RunReport(delivered=1)

        import connectors.imap.run as module

        original, module.run_once = module.run_once, one_pass
        try:
            totals = run_forever(
                Settings(
                    host="h",
                    username="u",
                    password="p",
                    workos_url="http://workos.test",
                    tokens=StaticToken("t"),
                    organization_id="o",
                    interval=0.0,
                ),
                stop=halt,
            )
        finally:
            module.run_once = original

        assert len(passes) == 3
        assert totals.delivered == 3, "the totals a supervisor reads are cumulative"

    def test_a_failing_pass_does_not_end_the_service(self) -> None:
        """A mail server that is briefly unreachable is an ordinary Tuesday.

        Nothing is lost by continuing: an undelivered message stays unseen, and the derived key
        makes the next attempt safe.
        """
        import threading

        from connectors.imap.run import RunReport, run_forever

        attempts = []
        halt = threading.Event()

        def one_pass(settings: Settings, **_: object) -> RunReport:
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("the mail server went away")
            halt.set()
            return RunReport(delivered=2)

        import connectors.imap.run as module

        original, module.run_once = module.run_once, one_pass
        try:
            totals = run_forever(
                Settings(
                    host="h",
                    username="u",
                    password="p",
                    workos_url="http://workos.test",
                    tokens=StaticToken("t"),
                    organization_id="o",
                    interval=0.0,
                ),
                stop=halt,
            )
        finally:
            module.run_once = original

        assert len(attempts) == 2
        assert totals.delivered == 2
