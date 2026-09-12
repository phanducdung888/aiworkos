"""The boundary an agent cannot cross (ADR-0052).

An intent is a request to consider a tool call. Everything here is about the validator refusing
one, because the accepting case is a single test and the refusals are the design.

The most important test in this file is `test_the_forbidden_check_is_actually_invoked`. Checkpoint 9
shipped `assert_within_agent_authority` holding BR-AI-08 and BR-AI-23, wrote unit tests proving the
function behaved correctly, and never called it from anything. The tests passed for two checkpoints
while the control did nothing. Asserting that a control *runs* is a different assertion from
asserting it *works*, and only one of them would have caught that.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

from app.contexts.intelligence.intents import (
    IntentValidator,
    ResolvedParticipant,
)
from app.platform.agentkit import (
    AgentAnalysis,
    ConfidenceBand,
    ConfidenceNormalizer,
    ConfidencePolicy,
    ConfidenceSource,
    EvidenceSpan,
    IntentKind,
    PersonReference,
    ToolIntent,
)
from app.platform.authz import Principal, Role
from app.platform.authz.agent import (
    AgentCapability,
    AgentIdentity,
    AgentPrincipal,
    AutonomyMode,
    CapabilityPolicy,
    PolicyCell,
)
from app.platform.authz.model import Action

BODY_LENGTH = 200
NORMALIZER = ConfidenceNormalizer()
PARTICIPANT = uuid.uuid4()
PERSON = uuid.uuid4()


def a_principal(*enabled: tuple[str, str]) -> AgentPrincipal:
    return AgentPrincipal(
        identity=AgentIdentity(
            name="extractor", capabilities=frozenset({AgentCapability.EXTRACT})
        ),
        delegated=Principal(
            person_id=uuid.uuid4(), org_id=uuid.uuid4(), roles=frozenset({Role.MEMBER})
        ),
        policy=CapabilityPolicy(
            cells=tuple(
                PolicyCell(
                    capability=AgentCapability.EXTRACT,
                    entity_type=entity,
                    action=Action(action),
                    mode=AutonomyMode.PROPOSE,
                )
                for entity, action in (enabled or (("work", "create"),))
            )
        ),
    )


def a_validator(
    principal: AgentPrincipal | None = None,
    *,
    participants: tuple[ResolvedParticipant, ...] | None = None,
    minimum: ConfidenceBand = ConfidenceBand.MEDIUM,
) -> IntentValidator:
    return IntentValidator(
        principal or a_principal(),
        participants=(
            participants
            if participants is not None
            else (
                ResolvedParticipant(
                    participant_id=PARTICIPANT, person_id=PERSON, role="speaker"
                ),
            )
        ),
        confidence_policy=ConfidencePolicy(minimum_band=minimum),
        body_length=BODY_LENGTH,
    )


def an_intent(**over: object) -> ToolIntent:
    fields: dict[str, object] = {
        "kind": IntentKind.CREATE_WORK,
        "summary": "Send the revised quote",
        "rationale": "The message says so.",
        "evidence": EvidenceSpan(char_start=0, char_end=22),
        "confidence": NORMALIZER.from_value(90, source=ConfidenceSource.PROVIDER_REPORTED),
        "arguments": {"title": "Send the revised quote"},
        "people": (),
    }
    fields.update(over)
    return ToolIntent(**fields)  # type: ignore[arg-type]


def outcome(validator: IntentValidator, *intents: ToolIntent):  # type: ignore[no-untyped-def]
    return validator.validate(AgentAnalysis(intents=intents))


class TestAcceptance:
    def test_a_well_formed_intent_becomes_a_validated_one(self) -> None:
        """The control. Without it, a validator that refused everything would pass this file."""
        result = outcome(a_validator(), an_intent())
        assert len(result.accepted) == 1
        assert result.refused == ()
        assert result.accepted[0].tool == "create_work"
        assert result.accepted[0].target_type == "work"

    def test_the_agent_never_names_the_tool(self) -> None:
        """It names a *kind*; WorkOS resolves the tool.

        An agent that could name a tool directly could name one the registry gained for a
        different caller, and the capability check would be all that stood between it and a call
        nobody meant it to make.
        """
        assert not hasattr(an_intent(), "tool")
        assert outcome(a_validator(), an_intent()).accepted[0].tool == "create_work"


class TestForbiddenActions:
    def test_the_forbidden_check_is_actually_invoked(self) -> None:
        """G1. The regression this whole module exists around.

        `assert_within_agent_authority` shipped in Checkpoint 9, was unit-tested, and was called by
        nothing for two checkpoints. This patches it and asserts the validator reached it — a
        different claim from "the function is correct", and the only one that would have caught it.
        """
        with patch(
            "app.contexts.intelligence.intents.assert_within_agent_authority"
        ) as guard:
            outcome(a_validator(), an_intent())
        guard.assert_called_once()
        action, resource = guard.call_args.args
        assert action is Action.CREATE
        assert resource.value == "work"

    def test_it_is_checked_for_every_kind(self) -> None:
        for kind, entity in (
            (IntentKind.CREATE_WORK, "work"),
            (IntentKind.CREATE_COMMITMENT, "commitment"),
            (IntentKind.ASSIGN_WORK, "work_assignment"),
        ):
            with patch(
                "app.contexts.intelligence.intents.assert_within_agent_authority"
            ) as guard:
                outcome(
                    a_validator(a_principal((entity, "create"), (entity, "assign"))),
                    an_intent(
                        kind=kind,
                        arguments={"title": "x"} if kind is IntentKind.CREATE_WORK else {},
                        people=(
                            PersonReference(participant_id=PARTICIPANT, role="speaker"),
                        ),
                    ),
                )
            assert guard.called, f"the forbidden check was skipped for {kind}"

    def test_a_refusal_from_the_guard_refuses_the_intent(self) -> None:
        from app.platform.authz.agent import AgentAuthorityError

        with patch(
            "app.contexts.intelligence.intents.assert_within_agent_authority",
            side_effect=AgentAuthorityError("nope"),
        ):
            result = outcome(a_validator(), an_intent())
        assert result.accepted == ()
        assert "BR-AI-23" in result.refused[0][1]


class TestPersonReferences:
    def test_an_agent_cannot_name_a_person_directly(self) -> None:
        """G2. The argument allow-list holds no identifier of any kind.

        Refused rather than dropped: silently discarding a field means storing something other than
        what was asked for, which is its own class of defect.
        """
        result = outcome(
            a_validator(),
            an_intent(arguments={"title": "x", "committed_by_person_id": str(uuid.uuid4())}),
        )
        assert result.accepted == ()
        assert "BR-AI-17" in result.refused[0][1]

    def test_a_commitment_resolves_its_committer_through_the_event(self) -> None:
        result = outcome(
            a_validator(a_principal(("commitment", "create"))),
            an_intent(
                kind=IntentKind.CREATE_COMMITMENT,
                arguments={"statement": "I will send it"},
                people=(PersonReference(participant_id=PARTICIPANT, role="speaker"),),
            ),
        )
        assert result.accepted[0].arguments["committed_by_person_id"] == str(PERSON)

    def test_a_commitment_naming_nobody_is_refused(self) -> None:
        """BR-AI-34. Not defaulted to the analyst, not guessed, not nearest-matched."""
        result = outcome(
            a_validator(a_principal(("commitment", "create"))),
            an_intent(
                kind=IntentKind.CREATE_COMMITMENT,
                arguments={"statement": "I will send it"},
                people=(),
            ),
        )
        assert result.accepted == ()
        assert "BR-AI-34" in result.refused[0][1]

    def test_a_participant_from_another_event_is_refused(self) -> None:
        """The reference is resolved against *this* Event's participants, not looked up globally.

        A person existing in the organization is not evidence that they made this promise.
        """
        result = outcome(
            a_validator(a_principal(("commitment", "create"))),
            an_intent(
                kind=IntentKind.CREATE_COMMITMENT,
                arguments={"statement": "x"},
                people=(PersonReference(participant_id=uuid.uuid4(), role="speaker"),),
            ),
        )
        assert "BR-AI-34" in result.refused[0][1]

    def test_an_unresolved_participant_is_refused(self) -> None:
        """A handle nobody matched to a Person stays a handle (BR-E-12)."""
        result = outcome(
            a_validator(
                a_principal(("commitment", "create")),
                participants=(
                    ResolvedParticipant(
                        participant_id=PARTICIPANT, person_id=None, role="speaker"
                    ),
                ),
            ),
            an_intent(
                kind=IntentKind.CREATE_COMMITMENT,
                arguments={"statement": "x"},
                people=(PersonReference(participant_id=PARTICIPANT, role="speaker"),),
            ),
        )
        assert "BR-AI-34" in result.refused[0][1]


class TestOtherRefusals:
    def test_a_kind_outside_the_policy_is_refused(self) -> None:
        result = outcome(
            a_validator(a_principal(("work", "create"))),
            an_intent(
                kind=IntentKind.CREATE_COMMITMENT,
                arguments={"statement": "x"},
                people=(PersonReference(participant_id=PARTICIPANT, role="speaker"),),
            ),
        )
        assert "BR-AI-03" in result.refused[0][1]

    def test_low_confidence_is_refused(self) -> None:
        result = outcome(
            a_validator(),
            an_intent(
                confidence=NORMALIZER.from_value(30, source=ConfidenceSource.HEURISTIC)
            ),
        )
        assert "BR-AI-09" in result.refused[0][1]

    def test_unknown_confidence_is_refused(self) -> None:
        from app.platform.agentkit import UNKNOWN_CONFIDENCE

        result = outcome(a_validator(), an_intent(confidence=UNKNOWN_CONFIDENCE))
        assert "BR-AI-09" in result.refused[0][1]

    def test_a_span_outside_the_text_is_refused(self) -> None:
        """Checked here as well as at the provider boundary, because an intent may arrive from
        somewhere that never passed through a provider adapter."""
        result = outcome(
            a_validator(),
            an_intent(evidence=EvidenceSpan(char_start=0, char_end=BODY_LENGTH + 1)),
        )
        assert "BR-E-05" in result.refused[0][1]

    def test_a_structured_argument_is_refused(self) -> None:
        """Scalars only. A nested object is a place to hide something the allow-list cannot see."""
        result = outcome(
            a_validator(), an_intent(arguments={"title": {"nested": "value"}})
        )
        assert "BR-AI-17" in result.refused[0][1]

    def test_refusals_are_collected_rather_than_aborting_the_batch(self) -> None:
        """One bad intent must not discard the good ones — the agent is not being punished."""
        result = outcome(
            a_validator(),
            an_intent(),
            an_intent(arguments={"title": "x", "forbidden": "y"}),
            an_intent(summary="Another thing"),
        )
        assert len(result.accepted) == 2
        assert len(result.refused) == 1


class TestIntentIsNotExecution:
    def test_an_intent_has_no_way_to_execute(self) -> None:
        """ADR-0052. Every type in the contract is data; none of them does anything."""
        intent = an_intent()
        for attribute in dir(intent):
            if attribute.startswith("_"):
                continue
            assert not callable(getattr(intent, attribute)), (
                f"ToolIntent.{attribute} is callable; an intent is a request, not an action"
            )

    def test_a_validated_intent_still_executes_nothing(self) -> None:
        accepted = outcome(a_validator(), an_intent()).accepted[0]
        for attribute in dir(accepted):
            if attribute.startswith("_"):
                continue
            assert not callable(getattr(accepted, attribute))
