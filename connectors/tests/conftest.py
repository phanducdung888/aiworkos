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
    """A WorkOS that records what was posted to it.

    The point of these tests is the *mail* half — the IMAP conversation and the loop around it —
    so the far end is a recorder rather than the application. What the application does with a
    delivered message is covered by the contract and journey suites, against the real API.
    """

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.status = 201
        self._server: http.server.HTTPServer | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> None:
        capture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                capture.received.append(
                    {
                        "path": self.path,
                        "idempotency_key": self.headers.get("Idempotency-Key"),
                        "organization": self.headers.get("X-Organization-Id"),
                        "authorization": self.headers.get("Authorization"),
                        "event": json.loads(body or b"{}"),
                    }
                )
                payload = json.dumps(
                    {"id": f"event-{len(capture.received)}", "revision_of_event_id": None}
                ).encode()
                self.send_response(capture.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

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
