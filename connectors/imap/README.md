# IMAP connector

Delivers email into WorkOS. It runs **outside** the WorkOS application, holds no database
credential, and contains no business or AI logic — it receives messages, normalises them, and posts
them to the capture API under the contract in ADR-0058.

```
mailbox --imaplib--> canonical message --HTTP--> POST /api/v1/events --> WorkOS
```

Everything after that arrow is WorkOS's: duplicate and revision detection (BR-E-02), identity
resolution and attribution (ADR-0054), extraction eligibility, and the whole agent path. The
connector never asks for an analysis.

## Running it

Standard library only. No dependency to install, and nothing is read from or written to this
repository at runtime.

```sh
export IMAP_HOST=imap.example.test
export IMAP_USERNAME=workos-ingest@example.test
export IMAP_PASSWORD=...                       # from your secret store, never from a file here
export WORKOS_URL=https://workos.example.test
export WORKOS_INGESTION_TOKEN=...              # an ingestion-role credential (ADR-0060)
export WORKOS_ORGANIZATION_ID=...

python -m connectors.imap.run                  # one pass, then exit
python -m connectors.imap.run --interval 60    # a service; stops cleanly on SIGINT/SIGTERM
```

Optional: `IMAP_PORT` (993), `IMAP_SECURITY` (`ssl`), `IMAP_MAILBOX` (INBOX), `IMAP_BATCH` (50),
`IMAP_INTERVAL`, `WORKOS_SOURCE_SYSTEM` (`email.imap`).

`IMAP_SECURITY` is `ssl` (implicit TLS, port 993), `starttls` (upgrade on 143 before the password
is sent), or `none`. `none` has to be asked for explicitly and is for a local test server: a
mailbox password in clear text is not something to arrive at by leaving a setting unset.

As a deployed service, `docker compose --profile pilot up imap-connector` runs exactly the above.
There is no build step because there is nothing to install — which is a statement about the
connector rather than a shortcut.

**Stopping it is safe.** A pass in flight finishes, so a message is never marked seen by a process
that was killed before WorkOS had it. The reverse — WorkOS has it and the flag was not set — is
harmless, because the redelivery carries the same derived key.

## What it sends

| Event field | Taken from |
|---|---|
| `source_system` | configuration, default `email.imap` |
| `source_ref` | `Message-ID`, which RFC 5322 defines to be globally unique |
| `occurred_at` | `Date`, in UTC; the server's INTERNALDATE if there is none |
| `title` | `Subject` |
| `body_text` | `text/plain`, or HTML stripped to its words |
| `participants` | `From` → speaker, `To`/`Cc` → recipient, as bare lowercased addresses |

`Idempotency-Key` is derived from `(source_system, source_ref, body)`. A retry of the same message
is the same action and replays; a *corrected* message carries the same `Message-ID` with different
content and is a revision, not a retry — which is why the key covers the body.

## What it will not do

- **Name a person.** `participants` carry an `external_handle` and never a `person_id`. A connector
  has no way to know who an address belongs to and no authority to assert it; resolution happens
  inside WorkOS against confirmed mappings (ADR-0054, BR-I-06), and an address nobody has vouched
  for resolves to nobody.
- **Invent a timestamp.** A message with no `Date` and no server receipt time is skipped and
  reported. CP15 reads deadlines against `occurred_at` (ADR-0055), so a date this code made up
  would become a real date on somebody's promise.
- **Decide what matters.** Quoted replies and signatures are delivered intact. Trimming them is
  interpretation, and interpretation belongs where it can be audited.
- **Retry a refusal.** A 4xx that is not "not now" means the message is wrong, not the moment;
  repeating it is how a connector turns its own defect into somebody else's outage.
- **Deliver attachments.** Attachments are a separate flow — a presigned upload against the Event,
  not a field on it (ADR-0039) — and this connector does not implement it. It *names* what it left
  behind in the log, so a message whose substance is in a PDF does not arrive as a covering note
  with no explanation for why the analysis found so little.

## The credential

An ingestion-role token (ADR-0060). It holds exactly one grant in the whole authorization matrix —
`EVENT.CREATE` — and cannot approve proposals, create Work or Commitments, run the agent, change
the agent policy, confirm an identity, or read anything back. Issue one per connector per
organization.

## Mapping an address to a person

Nothing is attributed until somebody vouches for the address. An administrator creates the mapping
and confirms it; until then a message from that address resolves to nobody and no commitment can be
attributed to them.

```sh
POST /api/v1/people/{person_id}/external-identities
     {"source_system": "email.imap", "external_id": "mai@example.test", "confidence": 95}
POST /api/v1/external-identities/{id}/confirm
```

`source_system` must match what the connector sends and the address must be lowercased the same
way, or the mapping simply never matches.

## Tests

Three layers, and the reason for each.

```sh
make connector-test      # normalising and delivery. No server, no network. Always runs.
make mail                # a real IMAP server (GreenMail), development only
make connector-smoke     # the IMAP conversation itself, against that server. Opt-in.
```

The first layer covers the halves that need nothing. The second covers the one that cannot be
covered any other way — the `imaplib` conversation and the loop around it, which was the only part
of this connector with no test at all when it first shipped. It is opt-in for the same reason the
real-provider tests are: a suite that needs a service to be up is a suite that gets skipped in the
environment which should run it, so what needs nothing must stay the default.

Pointing it at a real server immediately found a real defect: the INTERNALDATE fallback was being
relabelled rather than converted, so a message with no `Date` header arrived with a timestamp off by
the connector host's own UTC offset — seven hours, on the machine it was found on, and CP15 reads
deadlines against `occurred_at`. `test_run.py` now pins that without needing a server.

**A test server is not a real mailbox.** GreenMail is an independent implementation of RFC 3501, so
it catches assumptions a fake written alongside the client would have shared. It does not catch what
a particular provider does with folder names, flags or fetch responses. That is answered by pointing
this at a real mailbox, not by another test.

The end-to-end journey — an email becoming a Commitment somebody approved — lives in the backend
suite at `backend/tests/integration/test_imap_connector_journey.py`, and normalises its message with
this connector's own code so the two cannot drift apart.
