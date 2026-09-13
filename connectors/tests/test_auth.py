"""The credential, and what happens when it ages out (CP25, ADR-0066).

CP24 ran the connector against a real mailbox with a static token. Fifteen minutes later the realm
stopped honouring it and delivery stopped. These tests are about the half of that which CP24 left
open: obtaining a token, caching it, and getting another one when the first is refused.
"""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message
from typing import Any, Self

import pytest

from connectors.imap.auth import ClientCredentials, StaticToken, Unauthenticated
from connectors.imap.client import DeliveryUnavailable, IngestionClient

from .conftest import Capture

TOKEN_URL = "https://idp.test/realms/workos/protocol/openid-connect/token"


class _Answer:
    """One canned HTTP response for the injected opener."""

    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def opener(*answers: Any) -> Any:
    """An `urlopen` substitute that replays `answers` in order and records what it was sent."""
    sent: list[Any] = []
    remaining = list(answers)

    def call(request: Any, timeout: float | None = None) -> Any:
        sent.append(request)
        answer = remaining.pop(0) if remaining else answers[-1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    call.sent = sent  # type: ignore[attr-defined]
    return call


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestClientCredentials:
    def test_it_exchanges_the_secret_for_a_token(self) -> None:
        call = opener(_Answer({"access_token": "at-1", "expires_in": 900}))
        source = ClientCredentials(
            TOKEN_URL, client_id="workos-connector", client_secret="s", opener=call
        )
        assert source.token() == "at-1"
        request = call.sent[0]
        assert request.get_method() == "POST"
        body = request.data.decode()
        assert "grant_type=client_credentials" in body
        assert "client_id=workos-connector" in body

    def test_a_cached_token_costs_no_round_trip(self) -> None:
        call = opener(_Answer({"access_token": "at-1", "expires_in": 900}))
        source = ClientCredentials(TOKEN_URL, client_id="c", client_secret="s", opener=call)
        assert source.token() == source.token() == "at-1"
        assert len(call.sent) == 1

    def test_it_asks_again_before_the_token_actually_expires(self) -> None:
        """`leeway` has to cover the slowest request the token will be attached to.

        A token fetched at T with a 900 second life is not used at T+899: an attachment upload
        started then would be carrying a credential that expires mid-transfer.
        """
        clock = _Clock()
        call = opener(
            _Answer({"access_token": "at-1", "expires_in": 900}),
            _Answer({"access_token": "at-2", "expires_in": 900}),
        )
        source = ClientCredentials(
            TOKEN_URL, client_id="c", client_secret="s", opener=call, clock=clock, leeway=60
        )
        assert source.token() == "at-1"
        clock.now += 800  # still inside the window
        assert source.token() == "at-1"
        clock.now += 60  # now within the leeway of expiry
        assert source.token() == "at-2"

    def test_renew_discards_the_cache(self) -> None:
        call = opener(
            _Answer({"access_token": "at-1", "expires_in": 900}),
            _Answer({"access_token": "at-2", "expires_in": 900}),
        )
        source = ClientCredentials(TOKEN_URL, client_id="c", client_secret="s", opener=call)
        assert source.token() == "at-1"
        assert source.renew() == "at-2"
        assert source.token() == "at-2"

    def test_a_provider_that_names_no_lifetime_is_assumed_to_mean_a_short_one(self) -> None:
        """Being early costs a round trip. Being late costs a delivery."""
        clock = _Clock()
        call = opener(
            _Answer({"access_token": "at-1"}),
            _Answer({"access_token": "at-2"}),
        )
        source = ClientCredentials(
            TOKEN_URL, client_id="c", client_secret="s", opener=call, clock=clock, leeway=0
        )
        assert source.token() == "at-1"
        clock.now += 61
        assert source.token() == "at-2"

    def test_a_refused_client_is_unauthenticated_and_does_not_quote_the_secret(self) -> None:
        error = urllib.error.HTTPError(
            TOKEN_URL, 401, "no", Message(),
            io.BytesIO(b'{"error":"invalid_client","error_description":"Invalid client secret"}'),
        )
        source = ClientCredentials(
            TOKEN_URL, client_id="c", client_secret="hunter2", opener=opener(error)
        )
        with pytest.raises(Unauthenticated) as refusal:
            source.token()
        assert "invalid_client" in str(refusal.value) or "Invalid client" in str(refusal.value)
        assert "hunter2" not in str(refusal.value)

    def test_an_unreachable_provider_is_unauthenticated_rather_than_a_crash(self) -> None:
        source = ClientCredentials(
            TOKEN_URL, client_id="c", client_secret="s",
            opener=opener(urllib.error.URLError("no route")),
        )
        with pytest.raises(Unauthenticated):
            source.token()

    def test_an_answer_with_no_token_is_refused(self) -> None:
        source = ClientCredentials(
            TOKEN_URL, client_id="c", client_secret="s", opener=opener(_Answer({"ok": True}))
        )
        with pytest.raises(Unauthenticated):
            source.token()

    def test_the_secret_is_not_in_the_representation(self) -> None:
        source = ClientCredentials(TOKEN_URL, client_id="c", client_secret="hunter2")
        assert "hunter2" not in repr(source)


class TestStaticToken:
    def test_it_is_the_token_it_was_given(self) -> None:
        assert StaticToken("abc").token() == "abc"

    def test_it_says_plainly_that_it_cannot_renew(self) -> None:
        """The failure mode CP24 hit. A source that silently returned the same dead token would
        make an expired credential look like a broken API."""
        with pytest.raises(Unauthenticated) as refusal:
            StaticToken("abc").renew()
        assert "WORKOS_OIDC_TOKEN_URL" in str(refusal.value)

    def test_it_is_not_in_the_representation(self) -> None:
        assert "abc" not in repr(StaticToken("abc"))


class _Aging:
    """A source whose first token is stale, as a real one is after fifteen minutes."""

    def __init__(self) -> None:
        self.renewals = 0

    def token(self) -> str:
        return "stale" if self.renewals == 0 else "fresh"

    def renew(self) -> str:
        self.renewals += 1
        return "fresh"


class TestRenewalThroughTheClient:
    def test_an_expired_token_is_renewed_and_the_message_delivered(
        self, workos: Capture
    ) -> None:
        """The whole point. A token ageing out is the ordinary case for a service left running,
        and it should cost one extra round trip rather than an operator."""
        from connectors.imap.canonical import parse_message

        from .test_pilot_regressions import message

        workos.reject_tokens = {"stale"}
        source = _Aging()
        client = IngestionClient(workos.url, token=source, organization_id="org", attempts=3)
        delivered = client.deliver(parse_message(message()))
        assert delivered.event_id
        assert source.renewals == 1

    def test_a_credential_that_cannot_be_renewed_leaves_the_message_alone(
        self, workos: Capture
    ) -> None:
        from connectors.imap.canonical import parse_message

        from .test_pilot_regressions import message

        workos.reject_tokens = {"handed-in"}
        client = IngestionClient(
            workos.url, token=StaticToken("handed-in"), organization_id="org", attempts=2
        )
        with pytest.raises(DeliveryUnavailable) as outage:
            client.deliver(parse_message(message()))
        assert "could not be renewed" in str(outage.value)
        assert "WORKOS_OIDC_TOKEN_URL" in str(outage.value)

    def test_an_attachment_upload_renews_too(self, workos: Capture) -> None:
        from connectors.imap.canonical import parse_message

        from .test_pilot_regressions import message

        workos.reject_tokens = {"stale"}
        source = _Aging()
        client = IngestionClient(workos.url, token=source, organization_id="org", attempts=3)
        parsed = parse_message(message(filename="invoice.pdf"))
        outcome = client.deliver_attachments("event-1", parsed.attachments)
        assert outcome.delivered == 1
        assert source.renewals == 1
