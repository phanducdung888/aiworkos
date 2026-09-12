"""Evidence: the join between the world and the model (BR-E-04 to BR-E-06).

Evidence is what makes the system explainable. Every claim it makes about Work, a Commitment or a
Risk should be answerable with "because this was said, here, in this Event" — and if it cannot be,
the product should say so rather than assert (domain-model §Evidence).

Two rules carry the weight.

**BR-E-05: the excerpt is verbatim.** Not a summary, not a paraphrase, not a tidied quote. It must
appear in the Event's `body_text` at the recorded locator, at the time of creation, and that is
checked here rather than trusted. A paraphrase that reads well is exactly the failure this
prevents: it looks like a citation and cannot be verified against anything.

**BR-E-14: an attachment cannot be quoted.** A PDF has no character range, so Evidence over an
attachment carries a `claim_summary` and no excerpt. The two are exclusive — a row with both would
leave a reader unable to tell which one was checked.
"""

from __future__ import annotations

import dataclasses
import enum
import uuid

from app.platform.errors import DomainRuleViolation


class Assertion(enum.StrEnum):
    CREATES = "creates"
    SUPPORTS = "supports"
    COMPLETES = "completes"
    UPDATES = "updates"
    REASSIGNS = "reassigns"
    RESCHEDULES = "reschedules"
    CONTRADICTS = "contradicts"
    CLOSES = "closes"


class EvidenceTarget(enum.StrEnum):
    WORK = "work"
    PROJECT = "project"
    MILESTONE = "milestone"
    DEPENDENCY = "dependency"
    COMMITMENT = "commitment"
    RISK = "risk"
    DECISION = "decision"


class ProducedBy(enum.StrEnum):
    PERSON = "person"
    AI_INTERACTION = "ai_interaction"


@dataclasses.dataclass(frozen=True, slots=True)
class TextLocator:
    """Where in the Event's text the excerpt was taken from."""

    char_start: int
    char_end: int

    def as_json(self) -> dict[str, object]:
        return {"char_start": self.char_start, "char_end": self.char_end}


@dataclasses.dataclass(frozen=True, slots=True)
class AttachmentLocator:
    """BR-E-14. An attachment is cited whole; there is no span to quote."""

    attachment_id: uuid.UUID

    def as_json(self) -> dict[str, object]:
        return {"attachment_id": str(self.attachment_id)}


Locator = TextLocator | AttachmentLocator


def validate_text_evidence(
    *, body_text: str | None, locator: TextLocator, excerpt: str
) -> None:
    """BR-E-05, checked against the Event rather than trusted from the caller.

    The locator and the excerpt have to agree *with each other and with the Event*. Checking only
    that the excerpt appears somewhere in the body would accept a locator pointing at an unrelated
    sentence, which is worse than no locator: it looks precise and is wrong.
    """
    if body_text is None:
        raise DomainRuleViolation(
            "BR-E-05", "the event has no body text, so nothing in it can be quoted"
        )
    if not excerpt:
        raise DomainRuleViolation("BR-E-05", "an excerpt may not be empty")
    if locator.char_start < 0 or locator.char_end <= locator.char_start:
        raise DomainRuleViolation("BR-E-05", "the locator does not describe a span")
    if locator.char_end > len(body_text):
        raise DomainRuleViolation(
            "BR-E-05", "the locator runs past the end of the event's body text"
        )
    if body_text[locator.char_start : locator.char_end] != excerpt:
        raise DomainRuleViolation(
            "BR-E-05",
            "the excerpt is not what the event says at that locator; paraphrase is not evidence",
        )


def validate_attachment_evidence(
    *, claim_summary: str | None, attachment_ids: frozenset[uuid.UUID], locator: AttachmentLocator
) -> None:
    if not (claim_summary and claim_summary.strip()):
        raise DomainRuleViolation(
            "BR-E-14", "evidence over an attachment requires a claim summary"
        )
    if locator.attachment_id not in attachment_ids:
        raise DomainRuleViolation(
            "BR-E-04", "the attachment does not belong to the cited event"
        )


def validate_confidence(confidence: int) -> None:
    if not 0 <= confidence <= 100:
        raise DomainRuleViolation("BR-E-04", "confidence is a percentage")


def assert_not_already_superseded(superseded_by_id: uuid.UUID | None) -> None:
    """BR-E-06.

    Superseding an already-superseded row would fork the chain: two corrections claiming to replace
    the same citation, with no way to say which one the record now reads as true.
    """
    if superseded_by_id is not None:
        raise DomainRuleViolation(
            "BR-E-06", "this evidence has already been superseded; supersede the current row"
        )


def assert_supersedes_a_different_row(original_id: uuid.UUID, replacement_id: uuid.UUID) -> None:
    if original_id == replacement_id:
        raise DomainRuleViolation("BR-E-06", "evidence cannot supersede itself")
