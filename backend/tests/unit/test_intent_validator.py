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

import datetime as dt
import uuid
from unittest.mock import patch

from app.contexts.intelligence.intents import (
    IntentValidator,
    ResolvedParticipant,
    SourceEvent,
)
from app.contexts.intelligence.linking import (
    LinkCandidates,
    ProjectCandidate,
    WorkCandidate,
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

#: A Saturday, so "by Friday" and "next Monday" land in different weeks.
OCCURRED_AT = dt.datetime(2026, 9, 12, 9, 0, tzinfo=dt.UTC)

#: Long enough for every span these tests cite, and real text so a quoted deadline can be checked
#: against the words it was supposedly taken from.
BODY = "Send the revised quote by Friday. " + ("x" * (BODY_LENGTH - 34))
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
    body: str = BODY,
    occurred_at: dt.datetime = OCCURRED_AT,
    timezone: str = "UTC",
    candidates: LinkCandidates | None = None,
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
        source=SourceEvent(body_text=body, occurred_at=occurred_at, timezone=timezone),
        candidates=candidates,
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


#: A commitment quoting its own deadline, as the runtime would build it.
BY_FRIDAY = {"statement": "Send the revised quote by Friday", "due_phrase": "by Friday"}


class TestDeadlines:
    """ADR-0055. The agent quotes; WorkOS reads the quote against the Event's own clock.

    The narrowing is the point: `due_date` and `due_precision` used to be things an agent was
    allowed to supply and never did. Now it may not, and what it may supply instead is checked
    against the words it cited.
    """

    def validator(self, **over: object) -> IntentValidator:
        """A validator whose organization has actually enabled commitment extraction."""
        return a_validator(a_principal(("commitment", "create")), **over)  # type: ignore[arg-type]

    def a_commitment(self, **over: object) -> ToolIntent:
        fields: dict[str, object] = {
            "kind": IntentKind.CREATE_COMMITMENT,
            "summary": "Send the revised quote by Friday",
            "arguments": {"statement": "Send the revised quote by Friday"},
            # The span covers "Send the revised quote by Friday." in BODY.
            "evidence": EvidenceSpan(char_start=0, char_end=33),
            "people": (PersonReference(participant_id=PARTICIPANT, role="speaker"),),
        }
        fields.update(over)
        return an_intent(**fields)

    def test_a_quoted_deadline_becomes_a_date_and_a_precision(self) -> None:
        accepted = outcome(
            self.validator(),
            self.a_commitment(
                arguments=BY_FRIDAY,
            ),
        ).accepted[0]
        # Saturday 12 September 2026 + "by Friday" = the 18th, at week precision.
        assert accepted.arguments["due_date"] == "2026-09-18"
        assert accepted.arguments["due_precision"] == "week"
        assert "due_phrase" not in accepted.arguments, (
            "the quote is read here and does not travel into the action"
        )

    def test_an_agent_may_not_supply_a_date(self) -> None:
        """The whole rule in one assertion: dates are computed in WorkOS or not at all."""
        result = outcome(
            self.validator(),
            self.a_commitment(
                arguments={"statement": "Send the revised quote", "due_date": "2026-09-18"}
            ),
        )
        assert result.accepted == ()
        assert "BR-AI-17" in result.refused[0][1]

    def test_an_agent_may_not_supply_a_precision_either(self) -> None:
        result = outcome(
            self.validator(),
            self.a_commitment(
                arguments={"statement": "Send the revised quote", "due_precision": "exact"}
            ),
        )
        assert result.accepted == ()
        assert "BR-AI-17" in result.refused[0][1]

    def test_no_deadline_means_vague_and_no_date(self) -> None:
        accepted = outcome(self.validator(), self.a_commitment()).accepted[0]
        assert accepted.arguments["due_precision"] == "vague"
        assert "due_date" not in accepted.arguments

    def test_a_deadline_beside_the_span_in_the_same_sentence_is_read(self) -> None:
        """The shape a real model actually returns, and the reason the window is the sentence.

        GPT-4o cites the *action* — "send the revised quote" — and leaves the deadline next to it.
        Requiring the phrase inside the span passed every stubbed test in this file and would have
        dropped almost every deadline in production; this is that defect, pinned.
        """
        body = "Hi Mai. I will send the revised quote by Friday once finance signs off. Thanks."
        accepted = outcome(
            self.validator(body=body),
            self.a_commitment(
                # Exactly the span the real model returned: the verb phrase, nothing around it.
                evidence=EvidenceSpan(char_start=15, char_end=37),
                arguments={"statement": "send the revised quote", "due_phrase": "by Friday"},
            ),
        ).accepted[0]
        assert body[15:37] == "send the revised quote"
        assert accepted.arguments["due_date"] == "2026-09-18"
        assert accepted.arguments["due_precision"] == "week"

    def test_a_deadline_from_another_sentence_is_not_read(self) -> None:
        """BR-E-05's discipline applied to the deadline: a citation is checked, not believed.

        "By Friday" is in this message and belongs to somebody else's promise. A deadline does not
        travel between sentences just because both are in the same message.
        """
        body = "Mai will publish the notes by Friday. I will send the revised quote. Thanks."
        accepted = outcome(
            self.validator(body=body),
            self.a_commitment(
                evidence=EvidenceSpan(char_start=38, char_end=67),
                arguments={"statement": "I will send the revised quote", "due_phrase": "by Friday"},
            ),
        ).accepted[0]
        assert body[38:67] == "I will send the revised quote"
        assert accepted.arguments["due_precision"] == "vague"
        assert "due_date" not in accepted.arguments

    def test_a_phrase_that_is_nowhere_in_the_message_is_not_read(self) -> None:
        accepted = outcome(
            self.validator(),
            self.a_commitment(
                arguments={"statement": "Send the revised quote", "due_phrase": "by Tuesday"}
            ),
        ).accepted[0]
        assert accepted.arguments["due_precision"] == "vague"

    def test_an_unreadable_quote_degrades_rather_than_refusing_the_promise(self) -> None:
        """BR-AI-17. A promise whose deadline could not be read is still a promise."""
        body = "Send the revised quote before the meeting. " + ("x" * 100)
        accepted = outcome(
            self.validator(body=body),
            self.a_commitment(
                evidence=EvidenceSpan(char_start=0, char_end=41),
                arguments={
                    "statement": "Send the revised quote before the meeting",
                    "due_phrase": "before the meeting",
                },
            ),
        ).accepted[0]
        assert accepted.arguments["due_precision"] == "vague"
        assert "due_date" not in accepted.arguments

    def test_a_non_string_quote_is_not_read(self) -> None:
        accepted = outcome(
            self.validator(),
            self.a_commitment(
                arguments={"statement": "Send the revised quote by Friday", "due_phrase": 7}
            ),
        ).accepted[0]
        assert accepted.arguments["due_precision"] == "vague"

    def test_the_anchor_is_the_event_and_not_the_clock(self) -> None:
        """Re-analysing an old message must produce the dates it produced the first time."""
        older = self.validator(occurred_at=dt.datetime(2026, 8, 1, 9, 0, tzinfo=dt.UTC))
        accepted = outcome(
            older,
            self.a_commitment(
                arguments=BY_FRIDAY,
            ),
        ).accepted[0]
        # 1 August 2026 is a Saturday; the coming Friday is the 7th, not September's.
        assert accepted.arguments["due_date"] == "2026-08-07"

    def test_the_organisation_timezone_decides_which_day_it_was(self) -> None:
        """23:00 UTC on Friday is already Saturday in Hanoi, and "by Friday" means a week later."""
        late = dt.datetime(2026, 9, 11, 23, 0, tzinfo=dt.UTC)
        in_utc = outcome(
            self.validator(occurred_at=late, timezone="UTC"),
            self.a_commitment(
                arguments=BY_FRIDAY,
            ),
        ).accepted[0]
        in_hanoi = outcome(
            self.validator(occurred_at=late, timezone="Asia/Ho_Chi_Minh"),
            self.a_commitment(
                arguments=BY_FRIDAY,
            ),
        ).accepted[0]
        assert in_utc.arguments["due_date"] == "2026-09-11"
        assert in_hanoi.arguments["due_date"] == "2026-09-18"

    def test_an_unknown_timezone_falls_back_rather_than_failing(self) -> None:
        """A misconfigured organization is not a reason to refuse somebody's promise."""
        accepted = outcome(
            self.validator(timezone="Mars/Olympus_Mons"),
            self.a_commitment(
                arguments=BY_FRIDAY,
            ),
        ).accepted[0]
        assert accepted.arguments["due_date"] == "2026-09-18"

    def test_work_never_carries_a_deadline_argument(self) -> None:
        """CREATE_WORK lost `due_date` too: nothing populated it and nothing ever read it."""
        dated = {"title": "x", "due_date": "2026-09-18"}
        result = outcome(a_validator(), an_intent(arguments=dated))
        assert result.accepted == ()
        assert "BR-AI-17" in result.refused[0][1]


# --------------------------------------------------------- where a link may come from


WORK_CANDIDATE = uuid.uuid4()
PROJECT_CANDIDATE = uuid.uuid4()


def with_candidates() -> LinkCandidates:
    return LinkCandidates(
        work=(
            WorkCandidate(
                id=WORK_CANDIDATE,
                title="Revise the quote",
                status="in_progress",
                reason="same_thread_is_evidence",
            ),
        ),
        projects=(
            ProjectCandidate(
                id=PROJECT_CANDIDATE,
                title="Q4 renewals",
                reason="owns_candidate_work",
                via_work_id=WORK_CANDIDATE,
            ),
        ),
    )


class TestALinkComesFromTheEvidenceGraph:
    """BR-AI-39, ADR-0073.

    The rule this file exists to protect is that an agent may only name things somebody already
    approved. CP29 widened *which* arguments an agent may supply and narrowed *what values* they may
    hold, and the second half is what makes the first half safe — so it is asserted here, at the
    validator, rather than trusted to the prompt that asks for it.
    """

    def test_a_candidate_may_be_named(self) -> None:
        result = outcome(
            a_validator(candidates=with_candidates()),
            an_intent(
                arguments={
                    "title": "Send the revised quote",
                    "project_id": str(PROJECT_CANDIDATE),
                }
            ),
        )
        assert result.refused == ()
        assert result.accepted[0].arguments["project_id"] == str(PROJECT_CANDIDATE)

    def test_anything_else_is_refused_by_name(self) -> None:
        """Not dropped. An agent naming something outside its set has asked for what it may not
        have, which is the same class of event as naming an unregistered tool — and a silently
        dropped argument would read afterwards as the agent choosing not to link."""
        result = outcome(
            a_validator(candidates=with_candidates()),
            an_intent(
                arguments={"title": "Send the revised quote", "project_id": str(uuid.uuid4())}
            ),
        )
        assert result.accepted == ()
        assert "BR-AI-39" in result.refused[0][1]

    def test_an_empty_candidate_set_refuses_every_link(self) -> None:
        """The default, and the one that must not be the permissive case. A caller that forgets to
        resolve candidates gets every link refused rather than every link allowed."""
        result = outcome(
            a_validator(),
            an_intent(
                arguments={"title": "Send the revised quote", "project_id": str(PROJECT_CANDIDATE)}
            ),
        )
        assert result.accepted == ()
        assert "BR-AI-39" in result.refused[0][1]

    def test_something_that_is_not_an_identifier_is_refused(self) -> None:
        result = outcome(
            a_validator(candidates=with_candidates()),
            an_intent(arguments={"title": "Send the revised quote", "project_id": "Q4 renewals"}),
        )
        assert result.accepted == ()
        assert "BR-AI-39" in result.refused[0][1]

    def test_an_intent_naming_nothing_is_untouched(self) -> None:
        """A Proposal with no link is the ordinary outcome and must stay cheap (BR-AI-17)."""
        result = outcome(a_validator(candidates=with_candidates()), an_intent())
        assert result.refused == ()
        assert "project_id" not in result.accepted[0].arguments
