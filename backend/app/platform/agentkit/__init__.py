"""Shared vocabulary between an agent and WorkOS.

Pure data. No I/O, no database, no services — every type here is a value that crosses the boundary
between something that reasons and something that decides.

It lives in `platform` because it belongs to *neither* side. The first attempt put `ToolIntent` in
`app/agent`, which made the Intelligence context import upwards to validate it — inverting the
dependency the whole design rests on, and the layer contract said so immediately. A contract owned
by one party is not a contract.
"""

from app.platform.agentkit.confidence import (
    BAND_FLOOR,
    UNKNOWN_CONFIDENCE,
    ConfidenceAssessment,
    ConfidenceBand,
    ConfidenceNormalizer,
    ConfidencePolicy,
    ConfidenceSource,
)
from app.platform.agentkit.contract import (
    AgentAnalysis,
    AgentContract,
    EvidenceSpan,
    IntentKind,
    PersonReference,
    ToolIntent,
)

__all__ = [
    "BAND_FLOOR",
    "UNKNOWN_CONFIDENCE",
    "AgentAnalysis",
    "AgentContract",
    "ConfidenceAssessment",
    "ConfidenceBand",
    "ConfidenceNormalizer",
    "ConfidencePolicy",
    "ConfidenceSource",
    "EvidenceSpan",
    "IntentKind",
    "PersonReference",
    "ToolIntent",
]
