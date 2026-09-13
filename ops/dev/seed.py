"""Seed a development organization.

Phase 1 needs real rows before any surface is worth looking at: a Project must belong to a Team
(BR-P-01), an assignment must name a Person, and `resolve_principal` refuses a subject with no
Person in the requested organization. None of that can be created through the API, because Identity
writes are not implemented — so it is created here, deliberately and visibly, rather than by a
migration. Seed data is environment, not schema, for the same reason `ops/db/dev-roles.sql` is.

Idempotent by slug: running it twice leaves one organization and prints the same ids, so a harness
can call it without first working out whether it has already run.

**Runs as the cluster superuser, and has to.** "Which organization has this slug?" is a question from
outside every tenant, and no tenant-scoped role can answer it: RLS defaults to deny, so an unscoped
`SELECT` sees nothing and the script would try to create the organization it already created. Neither
`workos_app` nor `workos_owner` is an escape hatch here — `workos_owner` is deliberately NOSUPERUSER
and NOBYPASSRLS (Checkpoint 3.5), which is exactly the property that makes it unsuitable. Seeding is
an administrative act performed from outside the boundary, like `ops/db/dev-roles.sql`, and it is a
development script rather than anything the application can call.

    python ops/dev/seed.py                  # prints the ids as JSON
    python ops/dev/seed.py --slug acme-e2e
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.platform.ids import uuid7
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

#: The people in the development realm, keyed by the subject Keycloak actually puts in a token.
#:
#: The subjects are the pinned `id`s in `ops/keycloak/realm.json`, not the usernames. Until CP24
#: this list held usernames, and `resolve_principal` therefore found no Person for any real token:
#: Keycloak issues a UUID as `sub`. Every test signed its own tokens with whatever subject it had
#: seeded, so nothing failed until the stack was run end to end.
#:
#: `service-account-workos-connector` is the connector's identity (ADR-0060). It is a Person with
#: the `ingestion` role and nothing else — it may capture Events and attach to its own, and cannot
#: read anyone else's, approve anything, or execute a tool.
PEOPLE = [
    ("00000000-0000-4000-8000-00000000a001", "avery.admin", "Avery Admin", "org_admin"),
    ("00000000-0000-4000-8000-00000000a002", "tomas.lead", "Tomas Lead", "team_lead"),
    ("00000000-0000-4000-8000-00000000a003", "mira.member", "Mira Member", "member"),
    (
        "00000000-0000-4000-8000-00000000a009",
        "service-account-workos-connector",
        "Mail Connector",
        "ingestion",
    ),
]


def scope(session: Session, org_id: uuid.UUID) -> None:
    """Bind the transaction to one organization, as every request does.

    Set with `is_local => true`, so it lasts exactly as long as the transaction. Everything below
    runs in one transaction and commits once, which is why this is called once.
    """
    session.execute(
        text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org_id)}
    )


def seed(session: Session, slug: str) -> dict[str, str]:
    existing = session.execute(
        text("SELECT id FROM organization WHERE slug = :slug"), {"slug": slug}
    ).scalar_one_or_none()
    if existing is not None:
        scope(session, existing)
        align_subjects(session, existing)
        # People are added to this list as the system grows a new kind of principal — the
        # connector's service account is the first — so an organization seeded by an earlier
        # checkpoint is missing them. Idempotent means "ends in the state this script describes",
        # not "does nothing the second time".
        upsert_people(session, existing)
        session.commit()
        scope(session, existing)
        return describe(session, existing)

    org_id = uuid7()
    scope(session, org_id)
    session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:i, :n, :s)"),
        {"i": org_id, "n": slug.replace("-", " ").title(), "s": slug},
    )

    people = upsert_people(session, org_id)

    department_id = uuid7()
    session.execute(
        text(
            "INSERT INTO department (id, org_id, name, lead_person_id, status) "
            "VALUES (:i, :o, 'Delivery', :l, 'active')"
        ),
        {"i": department_id, "o": org_id, "l": people["tomas.lead"]},
    )
    team_id = uuid7()
    session.execute(
        text(
            "INSERT INTO team (id, org_id, department_id, name, lead_person_id, status) "
            "VALUES (:i, :o, :d, 'Platform', :l, 'active')"
        ),
        {"i": team_id, "o": org_id, "d": department_id, "l": people["tomas.lead"]},
    )
    # Humans only. The connector's service account belongs to the organization and to no team:
    # nothing is ever assigned to it and it leads nothing.
    for username in ("mira.member", "tomas.lead"):
        session.execute(
            text(
                "INSERT INTO team_membership (id, org_id, team_id, person_id, role) "
                "VALUES (:i, :o, :t, :p, 'member')"
            ),
            {"i": uuid7(), "o": org_id, "t": team_id, "p": people[username]},
        )

    result = describe(session, org_id)
    session.commit()
    return result


def upsert_people(session: Session, org_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Every person in `PEOPLE`, with their membership and their organization-scoped role.

    Keyed on email, which is what identifies a seeded person across runs: the id is minted here and
    the subject is the thing most likely to have been wrong.
    """
    people: dict[str, uuid.UUID] = {}
    for subject, username, display_name, role in PEOPLE:
        email = f"{username}@example.test"
        person_id = session.execute(
            text("SELECT id FROM person WHERE org_id = :o AND email = :e"),
            {"o": org_id, "e": email},
        ).scalar_one_or_none()
        if person_id is None:
            person_id = uuid7()
            session.execute(
                text(
                    "INSERT INTO person "
                    "(id, org_id, display_name, email, keycloak_subject, status) "
                    "VALUES (:i, :o, :n, :e, :k, 'active')"
                ),
                {"i": person_id, "o": org_id, "n": display_name, "e": email, "k": subject},
            )
        people[username] = person_id
        session.execute(
            text(
                "INSERT INTO organization_membership (id, org_id, person_id, status) "
                "SELECT :i, :o, :p, 'active' WHERE NOT EXISTS ("
                "  SELECT 1 FROM organization_membership WHERE org_id = :o AND person_id = :p)"
            ),
            {"i": uuid7(), "o": org_id, "p": person_id},
        )
        session.execute(
            text(
                "INSERT INTO role_assignment (id, org_id, person_id, role, scope_type) "
                "SELECT :i, :o, :p, :r, 'organization' WHERE NOT EXISTS ("
                "  SELECT 1 FROM role_assignment "
                "  WHERE org_id = :o AND person_id = :p AND role = :r)"
            ),
            {"i": uuid7(), "o": org_id, "p": person_id, "r": role},
        )
    return people


def align_subjects(session: Session, org_id: uuid.UUID) -> list[str]:
    """Repair a Person whose `keycloak_subject` predates the realm's pinned ids.

    Development databases seeded before CP24 hold usernames where a token carries a UUID, which
    makes every real sign-in a 404 on an organization the person is plainly a member of. Matching
    on email rather than on the old subject, because the old subject is exactly what is wrong.
    """
    repaired: list[str] = []
    for subject, username, _display_name, _role in PEOPLE:
        changed = session.execute(
            text(
                "UPDATE person SET keycloak_subject = :k "
                "WHERE org_id = :o AND email = :e AND keycloak_subject <> :k"
            ),
            {"k": subject, "o": org_id, "e": f"{username}@example.test"},
        ).rowcount
        if changed:
            repaired.append(username)
    if repaired:
        session.commit()
    return repaired


def describe(session: Session, org_id: uuid.UUID) -> dict[str, str]:
    rows = session.execute(
        text("SELECT split_part(email, '@', 1), id FROM person WHERE org_id = :o"), {"o": org_id}
    ).all()
    team = session.execute(
        text("SELECT id FROM team WHERE org_id = :o AND name = 'Platform'"), {"o": org_id}
    ).scalar_one()
    return {
        "org_id": str(org_id),
        "team_id": str(team),
        **{f"person:{subject}": str(person_id) for subject, person_id in rows},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a development organization.")
    parser.add_argument("--slug", default="acme-dev")
    parser.add_argument(
        "--database-url",
        # The superuser, for the reason in the module docstring. Deliberately not
        # WORKOS_APP_DATABASE_URL: that role is subject to RLS and cannot find an existing
        # organization by slug, which is what makes this script idempotent.
        default=os.environ.get(
            "WORKOS_SEED_DATABASE_URL",
            "postgresql+psycopg://workos:workos@127.0.0.1:5432/workos",
        ),
    )
    args = parser.parse_args()

    session = sessionmaker(
        bind=create_engine(args.database_url, future=True), future=True, expire_on_commit=False
    )()
    try:
        print(json.dumps(seed(session, args.slug), indent=2))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
