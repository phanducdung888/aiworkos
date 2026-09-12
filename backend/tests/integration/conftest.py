"""An organization with enough structure to authorize against.

The service tests need more than two rows. Authorization here turns on department subtrees, team
membership and assignment, so a fixture that produces only "a person in an org" can prove that a
call succeeds and nothing about why. This one builds the smallest shape in which every grant in the
matrix is distinguishable from every other:

    Delivery (department, led by dept_lead)
      └── Platform (team, led by team_lead)
            └── member

plus an `outsider` who is in the organization and in none of it, and a `departed` person, because
"cannot be assigned work" is a rule about somebody who exists (BR-I-05).
"""

from __future__ import annotations

import base64
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.main import create_app
from app.platform.auth import JwksCache, TokenVerifier
from app.platform.db import set_org_context
from app.platform.http import deps
from app.platform.ids import uuid7
from app.platform.storage import InMemoryObjectStore
from tests.conftest import APP_URL


@dataclass(frozen=True, slots=True)
class WorkOrg:
    org_id: uuid.UUID
    department_id: uuid.UUID
    team_id: uuid.UUID
    other_team_id: uuid.UUID
    admin: uuid.UUID
    dept_lead: uuid.UUID
    team_lead: uuid.UUID
    member: uuid.UUID
    outsider: uuid.UUID
    departed: uuid.UUID


def _scope(session: Session, org_id: uuid.UUID) -> None:
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )


def _person(session: Session, org_id: uuid.UUID, name: str, status: str = "active") -> uuid.UUID:
    person_id = uuid7()
    # UUIDv7 is time-ordered, so its leading hex digits are the millisecond and are identical for
    # every row this fixture creates. Unique test values have to come from somewhere random.
    tag = uuid.uuid4().hex[:12]
    session.execute(
        text(
            "INSERT INTO person (id, org_id, display_name, email, keycloak_subject, status) "
            "VALUES (:id, :org, :name, :email, :sub, :status)"
        ),
        {
            "id": person_id,
            "org": org_id,
            "name": name,
            "email": f"{name}-{tag}@example.test",
            "sub": f"sub-{tag}",
            "status": status,
        },
    )
    session.execute(
        text(
            "INSERT INTO organization_membership (id, org_id, person_id, status) "
            "VALUES (:id, :org, :person, 'active')"
        ),
        {"id": uuid7(), "org": org_id, "person": person_id},
    )
    return person_id


@pytest.fixture
def work_org(app_session_factory: sessionmaker[Session]) -> Iterator[WorkOrg]:
    org_id = uuid7()
    session = app_session_factory()
    _scope(session, org_id)
    session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": org_id, "name": "Acme", "slug": f"acme-{uuid.uuid4().hex[:12]}"},
    )

    admin = _person(session, org_id, "Avery Admin")
    dept_lead = _person(session, org_id, "Dana Department")
    team_lead = _person(session, org_id, "Tomas Team")
    member = _person(session, org_id, "Mira Member")
    outsider = _person(session, org_id, "Otto Outside")
    departed = _person(session, org_id, "Devi Departed", status="departed")

    department_id = uuid7()
    session.execute(
        text(
            "INSERT INTO department (id, org_id, name, lead_person_id, status) "
            "VALUES (:id, :org, 'Delivery', :lead, 'active')"
        ),
        {"id": department_id, "org": org_id, "lead": dept_lead},
    )
    team_id = uuid7()
    session.execute(
        text(
            "INSERT INTO team (id, org_id, department_id, name, lead_person_id, status) "
            "VALUES (:id, :org, :dept, 'Platform', :lead, 'active')"
        ),
        {"id": team_id, "org": org_id, "dept": department_id, "lead": team_lead},
    )
    # A second team outside the department, so "a team lead reaches their own team" is a claim that
    # can fail rather than one that is true by construction.
    other_team_id = uuid7()
    session.execute(
        text(
            "INSERT INTO team (id, org_id, name, status) "
            "VALUES (:id, :org, 'Unrelated', 'active')"
        ),
        {"id": other_team_id, "org": org_id},
    )
    session.execute(
        text(
            "INSERT INTO team_membership (id, org_id, team_id, person_id, role) "
            "VALUES (:id, :org, :team, :person, 'member')"
        ),
        {"id": uuid7(), "org": org_id, "team": team_id, "person": member},
    )
    session.commit()
    session.close()

    yield WorkOrg(
        org_id=org_id,
        department_id=department_id,
        team_id=team_id,
        other_team_id=other_team_id,
        admin=admin,
        dept_lead=dept_lead,
        team_lead=team_lead,
        member=member,
        outsider=outsider,
        departed=departed,
    )


@pytest.fixture
def scoped_session(
    app_session_factory: sessionmaker[Session], work_org: WorkOrg
) -> Iterator[Session]:
    """A session bound to the fixture organization, and bound to it again after every commit.

    The org context is set with `is_local => true`, so PostgreSQL discards it when the transaction
    ends. A test that commits and then reads would silently be an unscoped session, which RLS
    correctly shows nothing — a confusing way to discover a control is working. Re-establishing it
    on every `after_begin` keeps the fixture behaving like one long-lived request, which is what a
    test is written as.
    """
    session = app_session_factory()

    @event.listens_for(session, "after_begin")
    def _rescope(_: Session, __: object, connection: Connection) -> None:
        connection.execute(
            text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(work_org.org_id)},
        )

    _scope(session, work_org.org_id)
    try:
        yield session
        session.commit()
    finally:
        session.close()

# --------------------------------------------------------------------------- HTTP fixtures


@dataclass(frozen=True, slots=True)
class Realm:
    """A throwaway identity provider: one RSA key, one JWKS document, one token factory.

    T-3, resolved as option (a). Keycloak is not started for tests. What is *not* stubbed is the
    verification: `TokenVerifier` runs its real checks — signature, issuer, audience, expiry — so an
    expired or mis-audienced token is rejected here by the same code that rejects it in production.
    Only the key distribution is local.
    """

    issuer: str
    audience: str
    private_key: Any
    jwks: dict[str, Any]
    kid: str

    def token(
        self,
        subject: str,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        expires_in: int = 900,
        key: Any = None,
        kid: str | None = None,
    ) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "sub": subject,
                "iss": issuer or self.issuer,
                "aud": audience or self.audience,
                "iat": now,
                "exp": now + expires_in,
                "email": f"{subject}@example.test",
            },
            key if key is not None else self.private_key,
            algorithm="RS256",
            headers={"kid": kid or self.kid},
        )


def _rsa_jwks(public_key: Any, kid: str) -> dict[str, Any]:
    numbers = public_key.public_numbers()

    def b64(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": b64(numbers.n),
                "e": b64(numbers.e),
            }
        ]
    }


@lru_cache(maxsize=1)
def test_realm() -> Realm:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kid = "test-signing-key"
    return Realm(
        issuer="https://idp.test/realms/workos",
        audience="workos-api",
        private_key=private_key,
        jwks=_rsa_jwks(private_key.public_key(), kid),
        kid=kid,
    )


@lru_cache(maxsize=1)
def test_application() -> FastAPI:
    """The real application, with the real verifier, pointed at the fixture realm's keys.

    Built once and at import time rather than per test, because schemathesis has to read the
    OpenAPI document from the same object the requests go to: two apps would mean generating cases
    from one contract and exercising another.

    The dependency overridden is the session factory alone, so requests land in the same test
    database as everything else. Authentication, principal resolution, RLS, authorization, audit and
    the outbox are all production paths — a test that bypassed them would prove the router works and
    nothing about whether the system does.
    """
    realm = test_realm()
    application = create_app()
    # ADR-0039. The attachment path runs end to end against a real implementation of the store
    # protocol that happens to keep objects in memory, so these tests need no MinIO — the same
    # arrangement as the generated realm above, which is why they need no Keycloak either.
    application.state.object_store = InMemoryObjectStore()
    application.state.token_verifier = TokenVerifier(
        issuer=realm.issuer,
        audience=realm.audience,
        jwks=JwksCache("https://idp.test/jwks", fetch=lambda _: realm.jwks),
    )

    maker = sessionmaker(
        bind=create_engine(APP_URL, future=True), expire_on_commit=False, future=True
    )

    def _session(org_id: Annotated[uuid.UUID, Depends(deps.organization_id)]) -> Iterator[Session]:
        session = maker()
        try:
            set_org_context(session, org_id)
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    application.dependency_overrides[deps.scoped_session] = _session
    return application


@pytest.fixture(scope="session")
def realm() -> Realm:
    return test_realm()


@pytest.fixture
def api(realm: Realm, migrated_database: str) -> Iterator[TestClient]:
    # `raise_server_exceptions=False` so an unhandled error is rendered by the 500 handler and
    # asserted on, instead of escaping into the test as a Python traceback. That handler's silence
    # is part of the contract (contract §5) and has to be observable.
    with TestClient(test_application(), raise_server_exceptions=False) as client:
        yield client


def auth(realm: Realm, subject: str, org_id: uuid.UUID) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {realm.token(subject)}",
        "X-Organization-Id": str(org_id),
    }


# --------------------------------------------------------------------------- API principals


def subject_of(session: Session, person_id: uuid.UUID) -> str:
    return str(
        session.execute(
            text("SELECT keycloak_subject FROM person WHERE id = :id"), {"id": person_id}
        ).scalar_one()
    )


@pytest.fixture
def as_admin(
    api: TestClient, realm: Realm, work_org: WorkOrg, scoped_session: Session
) -> dict[str, str]:
    return auth(realm, subject_of(scoped_session, work_org.admin), work_org.org_id)


@pytest.fixture
def as_member(
    api: TestClient, realm: Realm, work_org: WorkOrg, scoped_session: Session
) -> dict[str, str]:
    return auth(realm, subject_of(scoped_session, work_org.member), work_org.org_id)


def grant(
    session: Session,
    org_id: uuid.UUID,
    person_id: uuid.UUID,
    role: str,
    scope_type: str = "organization",
    scope_id: uuid.UUID | None = None,
) -> None:
    # The org context is set with `is_local => true`, so it is discarded at commit. Re-establishing
    # it per statement is not ceremony: without it the second insert has no organization to claim
    # and RLS refuses the row, which is the control working exactly as intended.
    session.execute(
        text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )
    session.execute(
        text(
            "INSERT INTO role_assignment "
            "(id, org_id, person_id, role, scope_type, scope_id) "
            "VALUES (:id, :org, :person, :role, :scope_type, :scope_id)"
        ),
        {
            "id": uuid7(),
            "org": org_id,
            "person": person_id,
            "role": role,
            "scope_type": scope_type,
            "scope_id": scope_id,
        },
    )
    session.commit()


@pytest.fixture
def roles(scoped_session: Session, work_org: WorkOrg) -> None:
    grant(scoped_session, work_org.org_id, work_org.admin, "org_admin")
    grant(scoped_session, work_org.org_id, work_org.member, "member")
    grant(scoped_session, work_org.org_id, work_org.outsider, "member")
    grant(scoped_session, work_org.org_id, work_org.team_lead, "team_lead")


@pytest.fixture
def other_org_person(app_session_factory: sessionmaker[Session]) -> Iterator[uuid.UUID]:
    """A Person belonging to somebody else's organization.

    Created through its own session and its own org context, because the point is that the fixture
    organization's session can never see it — building it any other way would prove nothing.
    """
    org_id = uuid7()
    session = app_session_factory()
    _scope(session, org_id)
    session.execute(
        text("INSERT INTO organization (id, name, slug) VALUES (:id, 'Rival', :slug)"),
        {"id": org_id, "slug": f"rival-{uuid.uuid4().hex[:12]}"},
    )
    person_id = _person(session, org_id, "Rival Person")
    session.commit()
    session.close()
    yield person_id


@pytest.fixture
def object_store(api: TestClient) -> InMemoryObjectStore:
    """The store the application under test is using.

    Handed to tests so they can perform the upload the way a client does — through the presigned
    URL the API issued — rather than by reaching past it and asserting on a mock.
    """
    store = api.app.state.object_store  # type: ignore[attr-defined]
    assert isinstance(store, InMemoryObjectStore)
    return store
