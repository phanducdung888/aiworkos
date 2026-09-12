"""Typed provider failures (ADR-0049).

An SDK or HTTP exception never reaches the runtime. A domain that catches `httpx.HTTPError` is a
domain with opinions about a transport, and the next adapter — a different vendor, a different
client library — would need every `except` clause above it rewritten.

The distinction that matters most is the last one. A provider returning unparseable output is not a
low-confidence answer; it is a broken integration. Treating them the same makes an outage look like
a quiet day, and nobody investigates a quiet day.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Anything that went wrong between asking a model and getting a usable answer."""

    #: Whether asking again, later, could plausibly work. Read by the runtime to decide whether an
    #: interaction is worth recording as `failed` or `timed_out`, and by nothing else — a provider
    #: does not get to decide that the system should retry.
    retryable: bool = False


class ProviderUnavailable(ProviderError):
    """The provider could not be reached at all."""

    retryable = True


class ProviderTimeout(ProviderError):
    """The provider was reached and did not answer in time."""

    retryable = True


class ProviderRateLimited(ProviderError):
    """The provider refused because we are asking too often."""

    retryable = True


class ProviderAuthenticationFailure(ProviderError):
    """The credential was rejected.

    Not retryable: asking again with the same rejected credential is the same request. This is an
    operational problem and should look like one rather than like a flaky model.
    """


class ProviderInvalidResponse(ProviderError):
    """The provider answered, and the answer could not be understood.

    Deliberately *not* a low-confidence result (ADR-0050). The model being unsure and the
    integration being broken are different events with different responses, and collapsing them
    loses the one worth alerting on.
    """


class ProviderContractViolation(ProviderError):
    """The response parsed and broke a rule the contract guarantees.

    A span pointing outside the text, a confidence outside 0-100, a model identifier the provider
    was not asked about. Separate from `ProviderInvalidResponse` because the failure is not that we
    could not read the answer — it is that we read it and it was not allowed to say that.
    """
