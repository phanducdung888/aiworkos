"""The loop: read a mailbox, deliver what is in it, mark what was delivered.

`imaplib` and `email` from the standard library, against RFC 3501 — a protocol with an actual
specification and no vendor behind it, which is why email is the first connector (ADR-0061).

Every setting comes from the environment. Nothing is read from a file in the repository and nothing
is written to one: a connector's credential belongs to wherever it is deployed, and a default here
would be a credential in a repository.

The flow is deliberately unsophisticated. Fetch unseen messages, normalise each, deliver it, and
mark it seen only once WorkOS has it. A message that fails to normalise is left unseen and reported;
a message the API refuses is marked seen and reported, because repeating a malformed request is how
a connector turns its own defect into somebody else's outage.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import imaplib
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Sequence

from connectors.imap.canonical import (
    DEFAULT_SOURCE_SYSTEM,
    Unnormalisable,
    parse_message,
)
from connectors.imap.client import (
    DEFAULT_ATTEMPTS,
    DEFAULT_BACKOFF,
    DeliveryRefused,
    DeliveryUnavailable,
    IngestionClient,
)

logger = logging.getLogger("connectors.imap")


#: How the connection is protected. `ssl` is implicit TLS on 993 and is the default because it is
#: what a connector should use; `starttls` is the 143 upgrade many servers offer instead.
#:
#: `none` exists so the client can be exercised against a local test server, and it is the one
#: value that has to be asked for explicitly. A mailbox password in clear text over a network is
#: not a thing to arrive at by leaving a setting unset.
SECURITY = ("ssl", "starttls", "none")


@dataclasses.dataclass(frozen=True, slots=True)
class Settings:
    """Everything the connector needs, and nothing it could guess."""

    host: str
    username: str
    password: str
    workos_url: str
    workos_token: str
    organization_id: str
    mailbox: str = "INBOX"
    port: int = 993
    security: str = "ssl"
    source_system: str = DEFAULT_SOURCE_SYSTEM
    batch: int = 50
    #: Seconds between passes when running as a service. `None` is one pass and exit.
    interval: float | None = None

    def __post_init__(self) -> None:
        if self.security not in SECURITY:
            raise SystemExit(f"IMAP_SECURITY must be one of {', '.join(SECURITY)}")

    @classmethod
    def from_environment(cls) -> Settings:
        """Read the environment, naming anything missing rather than failing halfway through."""
        required = {
            "host": "IMAP_HOST",
            "username": "IMAP_USERNAME",
            "password": "IMAP_PASSWORD",
            "workos_url": "WORKOS_URL",
            "workos_token": "WORKOS_INGESTION_TOKEN",
            "organization_id": "WORKOS_ORGANIZATION_ID",
        }
        values = {field: os.environ.get(name, "") for field, name in required.items()}
        missing = sorted(required[field] for field, value in values.items() if not value)
        if missing:
            raise SystemExit(f"missing required environment: {', '.join(missing)}")
        return cls(
            **values,
            mailbox=os.environ.get("IMAP_MAILBOX", "INBOX"),
            port=int(os.environ.get("IMAP_PORT", "993")),
            security=os.environ.get("IMAP_SECURITY", "ssl"),
            source_system=os.environ.get("WORKOS_SOURCE_SYSTEM", DEFAULT_SOURCE_SYSTEM),
            batch=int(os.environ.get("IMAP_BATCH", "50")),
            interval=_optional_float(os.environ.get("IMAP_INTERVAL")),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class RunReport:
    delivered: int = 0
    already_known: int = 0
    skipped: int = 0
    refused: int = 0


def run_once(
    settings: Settings,
    *,
    client: IngestionClient | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF,
) -> RunReport:
    """One pass over the unseen messages in the mailbox."""
    delivery = client or IngestionClient(
        settings.workos_url,
        token=settings.workos_token,
        organization_id=settings.organization_id,
        attempts=attempts,
        backoff=backoff,
    )
    delivered = known = skipped = refused = 0

    with _connect(settings) as mail:
        mail.login(settings.username, settings.password)
        mail.select(settings.mailbox)
        _status, data = mail.search(None, "UNSEEN")
        identifiers = data[0].split()[: settings.batch]

        for identifier in identifiers:
            # PEEK, so a fetch that fails before delivery does not silently consume the message.
            _status, fetched = mail.fetch(identifier, "(BODY.PEEK[] INTERNALDATE)")
            raw, received_at = _unpack(fetched)
            if raw is None:
                skipped += 1
                continue

            try:
                message = parse_message(
                    raw, source_system=settings.source_system, received_at=received_at
                )
            except Unnormalisable as error:
                # Left unseen on purpose: this is a message somebody may want to look at, and a
                # connector that swallowed it would make it disappear without a record anywhere.
                logger.warning("skipping a message that cannot be normalised: %s", error)
                skipped += 1
                continue

            try:
                result = delivery.deliver(message)
            except DeliveryRefused as error:
                logger.error("WorkOS refused %s: %s", message.source_ref, error)
                _mark_seen(mail, identifier)
                refused += 1
                continue
            except DeliveryUnavailable as error:
                # Left unseen, so the next pass tries again. The idempotency key makes that safe.
                logger.error("WorkOS unreachable for %s: %s", message.source_ref, error)
                break

            if message.dropped_attachments:
                # Said out loud rather than swallowed. A message whose substance is in a PDF
                # arrives as its covering note, and the analysis will find correspondingly little.
                logger.warning(
                    "%s carried %d attachment(s) this connector does not deliver: %s",
                    message.source_ref,
                    len(message.dropped_attachments),
                    ", ".join(message.dropped_attachments),
                )

            _mark_seen(mail, identifier)
            if result.created:
                delivered += 1
            else:
                known += 1

    return RunReport(
        delivered=delivered, already_known=known, skipped=skipped, refused=refused
    )


def _connect(settings: Settings) -> imaplib.IMAP4:
    """The connection, protected the way the deployment says.

    `starttls` upgrades before `login`, so the password never crosses in clear; `none` is for a
    local test server and is why this returns `IMAP4` rather than `IMAP4_SSL`.
    """
    if settings.security == "ssl":
        return imaplib.IMAP4_SSL(settings.host, settings.port)
    mail = imaplib.IMAP4(settings.host, settings.port)
    if settings.security == "starttls":
        mail.starttls()
    return mail


def _optional_float(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    return float(raw)


def _unpack(fetched: Sequence[object]) -> tuple[bytes | None, dt.datetime | None]:
    """The bytes and the server's receipt time, out of imaplib's awkward tuple soup."""
    raw: bytes | None = None
    received_at: dt.datetime | None = None
    for item in fetched:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
            raw = item[1]
            received_at = _internaldate(item[0])
    return raw, received_at


def _internaldate(header: object) -> dt.datetime | None:
    if not isinstance(header, (bytes, str)):
        return None
    text = header.decode(errors="replace") if isinstance(header, bytes) else header
    if "INTERNALDATE" not in text.upper():
        return None
    try:
        stamp = imaplib.Internaldate2tuple(
            text.encode() if isinstance(text, str) else text
        )
    except (ValueError, TypeError):
        return None
    if stamp is None:
        return None
    # `Internaldate2tuple` hands back a **local-time** struct for the correct instant, so the
    # conversion back to an epoch has to be the local one. Treating those fields as UTC — which is
    # what this did until a real mail server was pointed at it — shifts every fallback timestamp by
    # the connector host's own offset, and CP15 reads deadlines against `occurred_at` (ADR-0055).
    return dt.datetime.fromtimestamp(time.mktime(stamp), dt.UTC)


def _mark_seen(mail: imaplib.IMAP4, identifier: bytes) -> None:
    mail.store(identifier.decode(), "+FLAGS", "\\Seen")


def run_forever(settings: Settings, *, stop: threading.Event | None = None) -> RunReport:
    """Pass after pass, until something says stop.

    A pass that fails is logged and slept off rather than ending the service: a mail server that is
    briefly unreachable, or a WorkOS that is restarting, is an ordinary Tuesday and nothing is lost
    — an undelivered message stays unseen, and the derived idempotency key makes the retry safe.

    The totals returned are cumulative, which is what a supervisor asking "has this been doing
    anything" wants to know.
    """
    assert settings.interval is not None, "run_forever needs an interval"
    halt = stop or threading.Event()
    totals = RunReport()

    while not halt.is_set():
        try:
            report = run_once(settings)
        except Exception:
            logger.exception("pass failed; continuing with the next one")
        else:
            totals = RunReport(
                delivered=totals.delivered + report.delivered,
                already_known=totals.already_known + report.already_known,
                skipped=totals.skipped + report.skipped,
                refused=totals.refused + report.refused,
            )
            _log(report)
        halt.wait(settings.interval)

    return totals


def _log(report: RunReport) -> None:
    logger.info(
        "delivered=%d already_known=%d skipped=%d refused=%d",
        report.delivered,
        report.already_known,
        report.skipped,
        report.refused,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deliver email into WorkOS.")
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        metavar="SECONDS",
        help="keep running, pausing this long between passes (default: one pass, then exit)",
    )
    parser.add_argument("--verbose", action="store_true")
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if arguments.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )

    settings = Settings.from_environment()
    if arguments.interval is not None:
        settings = dataclasses.replace(settings, interval=arguments.interval)

    if settings.interval is None:
        _log(run_once(settings))
        return 0

    # SIGTERM is how a container is asked to stop. Answering it means the pass in flight finishes
    # and the mailbox is left consistent, rather than a message being marked seen by a process that
    # was killed before WorkOS had it.
    halt = threading.Event()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, lambda *_: halt.set())

    logger.info("delivering every %.0fs; stop with SIGINT or SIGTERM", settings.interval)
    _log(run_forever(settings, stop=halt))
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    sys.exit(main())
