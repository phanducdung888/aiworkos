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

from connectors.imap.auth import ClientCredentials, StaticToken, TokenSource
from connectors.imap.canonical import (
    DEFAULT_SOURCE_SYSTEM,
    MAX_ATTACHMENT_BYTES,
    TooLarge,
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

#: The keyword this connector puts on a message it could not normalise.
#:
#: An IMAP keyword rather than a flag of its own invention: `PERMANENTFLAGS` advertising `\*` means
#: the server accepts client keywords, which Gmail and GreenMail both do. Prefixed with `$`, which
#: is the convention for a keyword with meaning outside one client.
UNPROCESSABLE = "$WorkOSUnprocessable"


@dataclasses.dataclass(frozen=True, slots=True)
class Settings:
    """Everything the connector needs, and nothing it could guess."""

    host: str
    username: str
    password: str
    workos_url: str
    #: How the bearer token is obtained. `ClientCredentials` renews itself and is what unattended
    #: operation needs; `StaticToken` is a credential handed in from outside and expires with no
    #: way back (ADR-0066).
    tokens: TokenSource
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
            "organization_id": "WORKOS_ORGANIZATION_ID",
        }
        values = {field: os.environ.get(name, "") for field, name in required.items()}
        missing = sorted(required[field] for field, value in values.items() if not value)
        if missing:
            raise SystemExit(f"missing required environment: {', '.join(missing)}")
        return cls(
            **values,
            tokens=_token_source(),
            mailbox=os.environ.get("IMAP_MAILBOX", "INBOX"),
            port=int(os.environ.get("IMAP_PORT", "993")),
            security=os.environ.get("IMAP_SECURITY", "ssl"),
            source_system=os.environ.get("WORKOS_SOURCE_SYSTEM", DEFAULT_SOURCE_SYSTEM),
            batch=int(os.environ.get("IMAP_BATCH", "50")),
            interval=_optional_float(os.environ.get("IMAP_INTERVAL")),
        )


def _token_source() -> TokenSource:
    """Client credentials if the connector can obtain its own token, otherwise one handed to it.

    Preferring the renewable one when both are configured, because the only reason to prefer a
    static token is that renewal is unavailable. CP24 ran with a static token against a real
    mailbox and the fifteen-minute lifespan ended the run; nothing that needs an operator four
    times an hour can be left alone for a week (ADR-0066).
    """
    token_url = os.environ.get("WORKOS_OIDC_TOKEN_URL", "").strip()
    client_id = os.environ.get("WORKOS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("WORKOS_CLIENT_SECRET", "")
    if token_url and client_id and client_secret:
        return ClientCredentials(
            token_url, client_id=client_id, client_secret=client_secret
        )
    static = os.environ.get("WORKOS_INGESTION_TOKEN", "").strip()
    if static:
        logger.warning(
            "running with WORKOS_INGESTION_TOKEN: this token cannot be renewed and delivery will "
            "stop when it expires. Set WORKOS_OIDC_TOKEN_URL, WORKOS_CLIENT_ID and "
            "WORKOS_CLIENT_SECRET for unattended operation."
        )
        return StaticToken(static)
    raise SystemExit(
        "no credential: set WORKOS_OIDC_TOKEN_URL, WORKOS_CLIENT_ID and WORKOS_CLIENT_SECRET, "
        "or WORKOS_INGESTION_TOKEN"
    )


@dataclasses.dataclass(frozen=True, slots=True)
class RunReport:
    delivered: int = 0
    already_known: int = 0
    skipped: int = 0
    refused: int = 0
    #: Files delivered alongside the messages above. Counted separately because an Event with a
    #: missing attachment is still an Event, and an operator needs to see both numbers.
    attachments: int = 0


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
        token=settings.tokens,
        organization_id=settings.organization_id,
        attempts=attempts,
        backoff=backoff,
    )
    delivered = known = skipped = refused = attachments = 0

    with _connect(settings) as mail:
        mail.login(settings.username, settings.password)
        mail.select(settings.mailbox)
        # Unseen, and not already found unprocessable. Without the second half a message that
        # cannot be normalised is re-read on every pass for ever, and fifty of them fill the batch
        # and starve every healthy message behind them — which CP24 watched happen with one.
        _status, data = mail.search(None, "UNSEEN", "UNKEYWORD", UNPROCESSABLE)
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
            except TooLarge as error:
                # Marked seen, unlike everything else that cannot be normalised. A message does not
                # get smaller, so leaving it unseen would retry it every interval forever — which
                # is what "repeating a malformed request is how a connector turns its own defect
                # into somebody else's outage" says about the refusal case, and says here too.
                logger.error("%s; leaving it in the mailbox and moving on", error)
                _mark_seen(mail, identifier)
                refused += 1
                continue
            except Unnormalisable as error:
                # Left unseen on purpose: this is a message somebody may want to look at, and a
                # connector that swallowed it would make it disappear without a record anywhere.
                # Flagged, though, so the next pass does not read it again — unread for the human,
                # done with for the connector.
                logger.warning("skipping a message that cannot be normalised: %s", error)
                _mark_unprocessable(mail, identifier)
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

            if message.oversized_attachments:
                logger.warning(
                    "%s: %s left behind, over the %d-byte per-file limit",
                    message.source_ref,
                    ", ".join(
                        f"{name} ({size} bytes)" for name, size in message.oversized_attachments
                    ),
                    MAX_ATTACHMENT_BYTES,
                )

            if message.attachments:
                # After the Event exists, because an attachment is attached *to* something
                # (ADR-0039). A file that fails is named and the Event still stands: three of four
                # attachments is a better record than no record.
                outcome = delivery.deliver_attachments(result.event_id, message.attachments)
                attachments += outcome.delivered
                if outcome.failed:
                    logger.warning(
                        "%s: %d attachment(s) were not delivered: %s",
                        message.source_ref,
                        len(outcome.failed),
                        ", ".join(outcome.failed),
                    )

            _mark_seen(mail, identifier)
            if result.created:
                delivered += 1
            else:
                known += 1

    return RunReport(
        delivered=delivered,
        already_known=known,
        skipped=skipped,
        refused=refused,
        attachments=attachments,
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


def _mark_unprocessable(mail: imaplib.IMAP4, identifier: bytes) -> None:
    """Quarantine a message this connector cannot read, without marking it read.

    A keyword rather than `\\Seen`, because the two say different things: `\\Seen` means a person
    has looked at it, and this connector is not a person. The message stays bold in whoever's
    mailbox it is, and stays out of the connector's search.

    A server that refuses the keyword is reported and not fought with. Arbitrary keywords are
    advertised by `PERMANENTFLAGS` containing `\\*`, which Gmail and GreenMail both do; a server
    that does not is back to re-reading the message each pass, which is where this started, and
    saying so is more use than an exception.
    """
    try:
        typ, _ = mail.store(identifier.decode(), "+FLAGS", UNPROCESSABLE)
    except imaplib.IMAP4.error as error:
        logger.warning("this server will not store %s: %s", UNPROCESSABLE, error)
        return
    if typ != "OK":
        logger.warning(
            "this server would not store %s; the message will be read again next pass",
            UNPROCESSABLE,
        )


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
                attachments=totals.attachments + report.attachments,
            )
            _log(report)
        halt.wait(settings.interval)

    return totals


def _log(report: RunReport) -> None:
    logger.info(
        "delivered=%d already_known=%d skipped=%d refused=%d attachments=%d",
        report.delivered,
        report.already_known,
        report.skipped,
        report.refused,
        report.attachments,
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
