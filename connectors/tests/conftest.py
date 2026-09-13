"""Shared fixtures for the connector tests.

The `imap` marker is what keeps the mail-server tests off a CI run. Same reasoning as the
real-provider smoke tests: a suite that needs a service to be up is a suite that gets skipped in the
environment which should run it, so the ones that need nothing must stay the default.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
from collections.abc import Iterator
from typing import Any

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "imap: talks to a real mail server; skipped unless RUN_IMAP_TESTS=1"
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if os.environ.get("RUN_IMAP_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="needs a mail server; set RUN_IMAP_TESTS=1 and `make mail`")
    for item in items:
        if "imap" in item.keywords:
            item.add_marker(skip)


class Capture:
    """A WorkOS that records what was posted to it, and a store that records what was uploaded.

    The point of these tests is the *mail* half — the IMAP conversation and the loop around it — so
    the far end is a recorder rather than the application. It has to model the attachment flow
    honestly all the same (ADR-0039): reserve, PUT the bytes somewhere that is not the API, then
    complete. A recorder that answered every call identically would let a connector that skipped a
    step pass.

    What the application does with a delivered message is covered by the contract and journey
    suites, against the real API.
    """

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.uploaded: list[dict[str, Any]] = []
        self.status = 201
        #: What the attachment endpoints answer. Separate from `status`, so a test can fail the
        #: files while the Event still succeeds — which is the interesting half.
        self.attachment_status = 201
        #: Bearer values this WorkOS refuses with a 401, as a realm refuses an expired token.
        #: Anything else is accepted, so a renewal is visibly what made the difference.
        self.reject_tokens: set[str] = set()
        self._server: http.server.HTTPServer | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def events(self) -> list[dict[str, Any]]:
        return [row for row in self.received if row["path"] == "/api/v1/events"]

    def start(self) -> None:
        capture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length", "0")))

            def _stale(self) -> bool:
                presented = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
                return presented in capture.reject_tokens

            def do_PUT(self) -> None:
                # The store is reached by a presigned URL and sees no bearer token at all, so an
                # expired WorkOS credential never reaches here. Nothing to check.
                # The presigned upload. Deliberately not under /api/v1: bytes do not pass through
                # the API, and a connector that posted them there would fail here.
                capture.uploaded.append(
                    {
                        "path": self.path,
                        "content_type": self.headers.get("Content-Type"),
                        "authorization": self.headers.get("Authorization"),
                        "content": self._read_body(),
                    }
                )
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self) -> None:
                body = self._read_body()
                if self._stale():
                    self._answer(401, {"detail": "the bearer token was not accepted"})
                    return
                capture.received.append(
                    {
                        "path": self.path,
                        "idempotency_key": self.headers.get("Idempotency-Key"),
                        "organization": self.headers.get("X-Organization-Id"),
                        "authorization": self.headers.get("Authorization"),
                        "event": json.loads(body or b"{}"),
                    }
                )
                if self.path.endswith("/complete"):
                    self._answer(200, {"status": "available"})
                elif self.path.endswith("/attachments"):
                    if capture.attachment_status >= 400:
                        self._answer(capture.attachment_status, {"detail": "no"})
                        return
                    index = len(capture.uploaded) + 1
                    self._answer(
                        capture.attachment_status,
                        {
                            "attachment": {"id": f"attachment-{index}", "status": "pending"},
                            "upload_url": f"{capture.url}/store/object-{index}",
                            "expires_at": "2026-09-13T10:00:00+00:00",
                        },
                    )
                else:
                    self._answer(
                        capture.status,
                        {
                            "id": f"event-{len(capture.events)}",
                            "revision_of_event_id": None,
                        },
                    )

            def log_message(self, *_: Any) -> None:
                return None

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        """Stop serving *and* close the socket.

        `shutdown()` alone leaves the listening socket open, so a client still connects and then
        waits for a reply that never comes — which turns "WorkOS is down" into "WorkOS is very
        slow", and makes a test about the first take minutes to prove the second.
        """
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


@pytest.fixture
def workos() -> Iterator[Capture]:
    capture = Capture()
    capture.start()
    yield capture
    capture.stop()
