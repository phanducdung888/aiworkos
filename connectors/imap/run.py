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
import email.utils
import imaplib
import logging
import os
import sys
from collections.abc import Sequence

from connectors.imap.canonical import (
    DEFAULT_SOURCE_SYSTEM,
    Unnormalisable,
    parse_message,
)
from connectors.imap.client import DeliveryRefused, DeliveryUnavailable, IngestionClient

logger = logging.getLogger("connectors.imap")


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
    source_system: str = DEFAULT_SOURCE_SYSTEM
    batch: int = 50

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
            source_system=os.environ.get("WORKOS_SOURCE_SYSTEM", DEFAULT_SOURCE_SYSTEM),
            batch=int(os.environ.get("IMAP_BATCH", "50")),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class RunReport:
    delivered: int = 0
    already_known: int = 0
    skipped: int = 0
    refused: int = 0


def run_once(settings: Settings, *, client: IngestionClient | None = None) -> RunReport:
    """One pass over the unseen messages in the mailbox."""
    delivery = client or IngestionClient(
        settings.workos_url,
        token=settings.workos_token,
        organization_id=settings.organization_id,
    )
    delivered = known = skipped = refused = 0

    with imaplib.IMAP4_SSL(settings.host, settings.port) as mail:
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

            _mark_seen(mail, identifier)
            if result.created:
                delivered += 1
            else:
                known += 1

    return RunReport(
        delivered=delivered, already_known=known, skipped=skipped, refused=refused
    )


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
    return dt.datetime.fromtimestamp(email.utils.mktime_tz((*stamp[:9], 0)), dt.UTC)


def _mark_seen(mail: imaplib.IMAP4_SSL, identifier: bytes) -> None:
    mail.store(identifier.decode(), "+FLAGS", "\\Seen")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deliver email into WorkOS.")
    parser.add_argument(
        "--once", action="store_true", help="one pass, then exit (the default)"
    )
    parser.add_argument("--verbose", action="store_true")
    arguments = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if arguments.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )

    report = run_once(Settings.from_environment())
    logger.info(
        "delivered=%d already_known=%d skipped=%d refused=%d",
        report.delivered,
        report.already_known,
        report.skipped,
        report.refused,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point
    sys.exit(main())
