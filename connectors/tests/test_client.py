"""Delivery, and what a connector does when the answer is not 201.

Retries are the whole subject. A connector that retries nothing loses messages when WorkOS restarts;
one that retries everything turns its own malformed request into a denial of service. The line
between those is what these tests hold.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import urllib.error
from email.message import Message
from typing import Any, Self

import pytest

from connectors.imap.canonical import CanonicalMessage
from connectors.imap.client import (
    DeliveryRefused,
    DeliveryUnavailable,
    IngestionClient,
)

MESSAGE = CanonicalMessage(
    source_system="email.imap",
    source_ref="abc123@example.test",
    thread_ref="abc123@example.test",
    occurred_at=dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC),
    title="Revised quote",
    body_text="I will send the revised quote by Friday.",
    participants=({"role": "speaker", "external_handle": "mai@example.test"},),
    idempotency_key="sha256:deadbeef",
)


class _Response:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class Opener:
    """A stand-in for `urlopen` that hands back one answer per call and records the requests."""

    def __init__(self, *answers: object) -> None:
        self.sent: list[Any] = []
        self._remaining = list(answers)

    def __call__(self, request: Any, timeout: float = 0.0) -> Any:
        self.sent.append(request)
        answer = self._remaining.pop(0) if self._remaining else _Response(201, {"id": "event-1"})
        if isinstance(answer, Exception):
            raise answer
        return answer


def opener(*answers: object) -> Opener:
    return Opener(*answers)


def a_client(open_with: Any, **over: Any) -> IngestionClient:
    return IngestionClient(
        "http://workos.test/",
        token="not-a-real-token",
        organization_id="11111111-1111-4111-8111-111111111111",
        backoff=0.0,
        opener=open_with,
        **over,
    )


class TestDelivery:
    def test_a_new_message_is_created(self) -> None:
        client = a_client(opener(_Response(201, {"id": "event-1"})))
        result = client.deliver(MESSAGE)
        assert (result.event_id, result.created, result.revision_of) == ("event-1", True, None)

    def test_a_redelivery_is_reported_as_already_known(self) -> None:
        """BR-E-02 answers 200 for a reference it has seen. Both answers mean "safely recorded"."""
        client = a_client(opener(_Response(200, {"id": "event-1"})))
        assert client.deliver(MESSAGE).created is False

    def test_a_revision_is_reported_as_one(self) -> None:
        client = a_client(
            opener(_Response(201, {"id": "event-2", "revision_of_event_id": "event-1"}))
        )
        assert client.deliver(MESSAGE).revision_of == "event-1"

    def test_the_request_carries_the_credential_and_the_derived_key(self) -> None:
        open_with = opener(_Response(201, {"id": "event-1"}))
        a_client(open_with).deliver(MESSAGE)

        request = open_with.sent[0]
        assert request.full_url == "http://workos.test/api/v1/events"
        assert request.get_header("Idempotency-key") == "sha256:deadbeef"
        assert request.get_header("X-organization-id").startswith("11111111")
        assert request.get_header("Authorization") == "Bearer not-a-real-token"
        assert json.loads(request.data)["source_ref"] == "abc123@example.test"


class TestRetries:
    def test_an_unreachable_api_is_retried(self) -> None:
        open_with = opener(
            urllib.error.URLError("connection refused"),
            urllib.error.URLError("connection refused"),
            _Response(201, {"id": "event-1"}),
        )
        assert a_client(open_with).deliver(MESSAGE).event_id == "event-1"
        assert len(open_with.sent) == 3

    def test_every_attempt_carries_the_same_key(self) -> None:
        """The point of deriving it. A retry after a timeout is the *same action*, so the API
        replays its first answer instead of recording a second Event."""
        open_with = opener(
            urllib.error.URLError("timeout"), _Response(200, {"id": "event-1"})
        )
        a_client(open_with).deliver(MESSAGE)
        keys = {request.get_header("Idempotency-key") for request in open_with.sent}
        assert keys == {"sha256:deadbeef"}

    @pytest.mark.parametrize("status", [500, 502, 503, 504, 429, 408, 409])
    def test_a_not_now_answer_is_retried(self, status: int) -> None:
        open_with = opener(
            urllib.error.HTTPError("u", status, "later", Message(), io.BytesIO(b'{"detail":"later"}')),
            _Response(201, {"id": "event-1"}),
        )
        assert a_client(open_with).deliver(MESSAGE).event_id == "event-1"

    @pytest.mark.parametrize("status", [400, 404, 422])
    def test_a_not_like_this_answer_is_not_retried(self, status: int) -> None:
        """Repeating a malformed request is how a connector turns its own bug into an outage."""
        open_with = opener(
            urllib.error.HTTPError("u", status, "no", Message(), io.BytesIO(b'{"detail":"no","rule":"BR-E-13"}'))
        )
        with pytest.raises(DeliveryRefused) as refusal:
            a_client(open_with).deliver(MESSAGE)
        assert refusal.value.status == status
        assert "BR-E-13" in refusal.value.detail
        assert len(open_with.sent) == 1

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_credential_is_neither_retried_nor_treated_as_a_bad_message(
        self, status: int
    ) -> None:
        """401 and 403 sat in the list above until CP24, and that was silent data loss.

        A refusal makes the caller mark the message seen. When the pilot's token reached its
        lifespan the connector consumed every message it read and delivered none of them. The
        credential is the connector's problem, not the message's, so this is unavailability — and
        still not retried, because retrying a 401 achieves nothing but delay.
        """
        open_with = opener(
            urllib.error.HTTPError("u", status, "no", Message(), io.BytesIO(b'{"detail":"nope"}'))
        )
        with pytest.raises(DeliveryUnavailable) as outage:
            a_client(open_with).deliver(MESSAGE)
        assert "credential" in str(outage.value)
        assert len(open_with.sent) == 1

    def test_giving_up_is_distinguishable_from_being_refused(self) -> None:
        """The caller keeps an undelivered message for the next pass and quarantines a refused one,
        so the two cannot be one exception."""
        open_with = opener(*[urllib.error.URLError("down")] * 5)
        with pytest.raises(DeliveryUnavailable):
            a_client(open_with, attempts=5).deliver(MESSAGE)
        assert len(open_with.sent) == 5


class TestSecrets:
    def test_the_token_is_not_in_the_representation_of_anything(self) -> None:
        """A credential in a log file is still a credential."""
        client = a_client(opener(_Response(201, {"id": "event-1"})))
        assert "not-a-real-token" not in repr(client)
        with pytest.raises(DeliveryRefused) as refusal:
            a_client(
                opener(
                    urllib.error.HTTPError("u", 422, "no", Message(), io.BytesIO(b"{}"))
                )
            ).deliver(MESSAGE)
        assert "not-a-real-token" not in str(refusal.value)
        # And in the answer that is specifically *about* the credential, which is the one place a
        # well-meaning error message would be tempted to quote it.
        with pytest.raises(DeliveryUnavailable) as outage:
            a_client(
                opener(
                    urllib.error.HTTPError("u", 401, "no", Message(), io.BytesIO(b"{}"))
                )
            ).deliver(MESSAGE)
        assert "not-a-real-token" not in str(outage.value)
