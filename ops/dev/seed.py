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

#: Matches the usernames in `ops/keycloak/realm.json`, so somebody who signs in through the dev
#: realm lands on the Person this created rather than on a 404.
PEOPLE = [
    ("avery.admin", "Avery Admin", "org_admin"),
    ("tomas.lead", "Tomas Lead", "team_lead"),
    ("mira.member", "Mira Member", "member"),
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
        return describe(session, existing)

    org_id = uuid7()
    scope(session, org_id)
    session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:i, :n, :s)"),
        {"i": org_id, "n": slug.replace("-", " ").title(), "s": slug},
    )

    people: dict[str, uuid.UUID] = {}
    for subject, display_name, role in PEOPLE:
        person_id = uuid7()
        people[subject] = person_id
        session.execute(
            text(
                "INSERT INTO person (id, org_id, display_name, email, keycloak_subject, status) "
                "VALUES (:i, :o, :n, :e, :k, 'active')"
            ),
            {
                "i": person_id,
                "o": org_id,
                "n": display_name,
                "e": f"{subject}@example.test",
                "k": subject,
            },
        )
        session.execute(
            text(
                "INSERT INTO organization_membership (id, org_id, person_id, status) "
                "VALUES (:i, :o, :p, 'active')"
            ),
            {"i": uuid7(), "o": org_id, "p": person_id},
        )
        session.execute(
            text(
                "INSERT INTO role_assignment (id, org_id, person_id, role, scope_type) "
                "VALUES (:i, :o, :p, :r, 'organization')"
            ),
            {"i": uuid7(), "o": org_id, "p": person_id, "r": role},
        )

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
    for subject in ("mira.member", "tomas.lead"):
        session.execute(
            text(
                "INSERT INTO team_membership (id, org_id, team_id, person_id, role) "
                "VALUES (:i, :o, :t, :p, 'member')"
            ),
            {"i": uuid7(), "o": org_id, "t": team_id, "p": people[subject]},
        )

    result = describe(session, org_id)
    session.commit()
    return result


def describe(session: Session, org_id: uuid.UUID) -> dict[str, str]:
    rows = session.execute(
        text("SELECT keycloak_subject, id FROM person WHERE org_id = :o"), {"o": org_id}
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
