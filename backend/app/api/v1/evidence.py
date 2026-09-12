"""Evidence: why the system believes what it says.

Every citation names an Event and quotes it verbatim, or names an attachment and summarises it.
There is no path here that records a belief without a source, which is the entire purpose of the
resource: if a query cannot be answered with evidence, the product should say so rather than assert.

Corrections do not edit (BR-E-06). Superseding writes a new citation and points the old one at it,
so "we used to believe this, because of that" stays answerable — a deleted mistake is a mistake
nobody can learn from.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Response, status

from app.api.v1.schemas import (
    PROBLEM_RESPONSES,
    EvidenceCreate,
    EvidenceList,
    EvidenceResource,
)
from app.contexts.signal.public import (
    AttachmentLocator,
    CreateEvidence,
    EvidenceService,
    EvidenceTarget,
    ServiceContext,
    SupersedeEvidence,
    TextLocator,
    evidence_for_target,
    get_evidence,
)
from app.platform.errors import EntityNotFound
from app.platform.http.deps import (
    ActorDep,
    IdempotencyKeyDep,
    ObjectStoreDep,
    PrincipalDep,
    SessionDep,
)
from app.platform.http.etag import etag_for
from app.platform.http.idempotency import Idempotency, replay_response

router = APIRouter(prefix="/api/v1/evidence", tags=["evidence"], responses=PROBLEM_RESPONSES)


def _command(body: EvidenceCreate) -> CreateEvidence:
    locator: TextLocator | AttachmentLocator
    if body.text_locator is not None:
        locator = TextLocator(
            char_start=body.text_locator.char_start, char_end=body.text_locator.char_end
        )
    else:
        assert body.attachment_locator is not None  # the schema validator guarantees one
        locator = AttachmentLocator(attachment_id=body.attachment_locator.attachment_id)
    return CreateEvidence(
        event_id=body.event_id,
        target_type=body.target_type.value,
        target_id=body.target_id,
        assertion=body.assertion.value,
        locator=locator,
        excerpt=body.excerpt,
        claim_summary=body.claim_summary,
        confidence=body.confidence,
    )


def _context(
    session: SessionDep, principal: PrincipalDep, actor: ActorDep, store: ObjectStoreDep
) -> ServiceContext:
    return ServiceContext(
        session=session, principal=principal, actor=actor, object_store=store
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=EvidenceResource)
def create_evidence(
    body: EvidenceCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    store: ObjectStoreDep,
    idempotency_key: IdempotencyKeyDep = None,
) -> Any:
    guard = Idempotency(
        session,
        org_id=principal.org_id,
        endpoint="POST /api/v1/evidence",
        key=idempotency_key,
        payload=body.model_dump(mode="json"),
    )
    if (stored := guard.replay()) is not None:
        return replay_response(stored)

    evidence = EvidenceService(_context(session, principal, actor, store)).create(
        _command(body)
    )
    guard.record(201, EvidenceResource.model_validate(evidence).model_dump(mode="json"))
    response.headers["Location"] = f"/api/v1/evidence/{evidence.id}"
    response.headers["ETag"] = etag_for(evidence.version)
    return evidence


@router.get("/{evidence_id}", response_model=EvidenceResource)
def read_evidence(
    evidence_id: uuid.UUID, response: Response, session: SessionDep, principal: PrincipalDep
) -> Any:
    evidence = get_evidence(session, org_id=principal.org_id, evidence_id=evidence_id)
    if evidence is None:
        raise EntityNotFound("evidence", evidence_id)
    response.headers["ETag"] = etag_for(evidence.version)
    return evidence


@router.get("", response_model=EvidenceList)
def list_evidence_for_target(
    target_type: EvidenceTarget,
    target_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> EvidenceList:
    """Every citation for one entity, superseded rows included.

    Superseded evidence is not hidden. A reader asking why the system believes something is also
    entitled to see what it used to believe and what replaced it.
    """
    return EvidenceList(
        items=[
            EvidenceResource.model_validate(row)
            for row in evidence_for_target(
                session,
                org_id=principal.org_id,
                target_type=target_type.value,
                target_id=target_id,
            )
        ]
    )


@router.post(
    "/{evidence_id}/supersede",
    status_code=status.HTTP_201_CREATED,
    response_model=EvidenceResource,
)
def supersede_evidence(
    evidence_id: uuid.UUID,
    body: EvidenceCreate,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    actor: ActorDep,
    store: ObjectStoreDep,
) -> Any:
    """BR-E-06. Returns the replacement; the original is retained, pointing forward to it."""
    replacement = EvidenceService(_context(session, principal, actor, store)).supersede(
        SupersedeEvidence(evidence_id=evidence_id, replacement=_command(body))
    )
    response.headers["Location"] = f"/api/v1/evidence/{replacement.id}"
    response.headers["ETag"] = etag_for(replacement.version)
    return replacement
