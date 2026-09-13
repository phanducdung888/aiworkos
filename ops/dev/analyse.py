"""Ask the AI to read the messages a connector delivered.

Capturing an Event and analysing one are deliberately separate acts (ADR-0034, and the Capture
surface says so in as many words): a message arriving is not consent to spend a model call on it,
and a system that analysed everything on arrival would be making that decision for the organization
rather than offering it.

Nothing automates the second act yet. The web surface can analyse a message a person just pasted in
and nothing else, so mail the connector delivers becomes an Event and stops there. That is a real
gap and it is a product decision to close — whether ingestion should trigger analysis, for which
capabilities, under what cost ceiling. Until somebody makes it, this is the operator doing by hand
what the product will eventually do by policy, and it is a script rather than a feature so that
nobody mistakes it for the answer.

    make analyse                 # every delivered message nobody has analysed yet
    make analyse ARGS=--dry-run  # say what would be analysed, spend nothing
    make analyse ARGS=--all      # including ones already analysed

Standard library only. It talks to the published API with a real person's token and no shortcuts:
everything it does, a signed-in human could do through the browser.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

#: Only messages that came from outside. An internal Event is not extraction-eligible (BR-E-11),
#: and asking anyway would spend a model call to be refused.
EXTERNAL_ORIGIN = "external"


def settings() -> dict[str, str]:
    """`.env` if it is there, overridden by the real environment.

    Parsed the way Compose parses it rather than by sourcing it as a shell script: the values
    include an app password, and a shell would try to execute half of one.
    """
    values: dict[str, str] = {}
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    import os

    values.update({k: v for k, v in os.environ.items() if k in values or k.startswith("WORKOS_")})
    return values


class Api:
    """The published API, as a signed-in person."""

    def __init__(self, base: str, token: str, organization: str) -> None:
        self._base = base.rstrip("/")
        self._token = token
        self._org = organization

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        request = urllib.request.Request(
            f"{self._base}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "X-Organization-Id": self._org,
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read() or b"{}")

    def pages(self, path: str) -> list[dict[str, Any]]:
        """Every page, not the first one. A cursor nobody follows is a silent truncation."""
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            joiner = "&" if "?" in path else "?"
            page = self._call(
                "GET", f"{path}{joiner}cursor={urllib.parse.quote(cursor)}" if cursor else path
            )
            items.extend(page.get("items", []))
            cursor = page.get("next_cursor")
            if not cursor:
                return items

    def analyse(self, event_id: str) -> dict[str, Any]:
        return self._call("POST", f"/api/v1/events/{event_id}/analyze", {})


def token_for(config: dict[str, str], username: str, password: str) -> str:
    """A person's access token from the realm, by direct grant.

    The development realm enables direct grants on `workos-web` for exactly this kind of thing. It
    is a development convenience and is why this script lives under `ops/dev`: a deployment that
    wanted a scheduled version of this would give it a client of its own, not a person's password.
    """
    issuer = config.get("WORKOS_OIDC_ISSUER", "http://localhost:8080/realms/workos")
    body = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": config.get("WORKOS_WEB_CLIENT_ID", "workos-web"),
            "username": username,
            "password": password,
            "scope": "openid",
        }
    ).encode()
    request = urllib.request.Request(
        f"{issuer}/protocol/openid-connect/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = json.loads(error.read() or b"{}").get("error_description", error.reason)
        raise SystemExit(f"could not sign in as {username}: {detail}") from error
    except urllib.error.URLError as error:
        raise SystemExit(f"could not reach the identity provider at {issuer}: {error.reason}")
    return str(payload["access_token"])


def main() -> int:
    config = settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--organization", default=config.get("WORKOS_ORGANIZATION_ID", ""))
    parser.add_argument("--url", default=config.get("WORKOS_PUBLIC_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--as", dest="username", default=config.get("WORKOS_ANALYSE_AS", "po"))
    parser.add_argument("--password", default=config.get("WORKOS_ANALYSE_PASSWORD", "workos"))
    parser.add_argument(
        "--source", default="email.imap", help="only messages from this connector"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="analyse messages that have been analysed before as well",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="name what would be analysed and stop"
    )
    args = parser.parse_args()

    if not args.organization:
        raise SystemExit(
            "no organization: set WORKOS_ORGANIZATION_ID in .env, or pass --organization. "
            "`make seed` prints it."
        )

    api = Api(args.url, token_for(config, args.username, args.password), args.organization)

    events = [
        event
        for event in api.pages(f"/api/v1/events?source_system={args.source}&limit=200")
        if event.get("origin") == EXTERNAL_ORIGIN
    ]
    already = (
        set()
        if args.all
        else {
            run["trigger_ref"]
            for run in api.pages("/api/v1/ai-interactions?limit=200")
            if run.get("trigger_ref")
        }
    )
    pending = [event for event in events if event["id"] not in already]

    print(f"{len(events)} message(s) from {args.source}; {len(pending)} not yet analysed")
    if not pending:
        return 0
    if args.dry_run:
        for event in pending:
            print(f"  would analyse  {event['occurred_at'][:16]}  {event.get('title') or '(no subject)'}")
        return 0

    proposals = 0
    for event in pending:
        title = event.get("title") or "(no subject)"
        try:
            result = api.analyse(event["id"])
        except urllib.error.HTTPError as error:
            detail = json.loads(error.read() or b"{}").get("detail", error.reason)
            print(f"  refused        {title[:46]:<46}  {error.code}: {detail}")
            continue
        raised = len(result.get("proposal_ids") or [])
        proposals += raised
        # "Nothing was proposed" is an answer, not a failure: most messages are not commitments,
        # and a run that says so is the system working (BR-AI-09).
        summary = (
            f"{raised} proposal(s), {len(result.get('evidence_ids') or [])} evidence"
            if raised
            else "nothing to propose"
        )
        print(f"  analysed       {title[:46]:<46}  {summary}")

    print(f"\n{proposals} proposal(s) now waiting for a decision at {args.url.replace(':8000', ':5173')}/proposals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
