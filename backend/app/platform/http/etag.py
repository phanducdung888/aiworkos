"""Entity tags over the `version` column.

Optimistic concurrency already exists at the repository boundary (BR-G-06, `platform/concurrency`).
This is only its honest expression on the wire: the version a client last read goes out as an ETag
and comes back as `If-Match`, and the service refuses the write if the row has moved on.

Weak tags (`W/"3"`) because two responses with the same version are semantically the same entity
without being byte-identical — the same row serialised twice can differ in field order, and a
strong tag would be a promise this API does not keep.

Missing and wrong are different answers. A request with no `If-Match` never expressed an
expectation, so it is refused with 428 and told to; a request naming a version that is no longer
current expressed one that turned out to be false, and 412 is that answer (RFC 9110 §13.1.1).
"""

from __future__ import annotations

from app.platform.http.errors import PreconditionRequired


def etag_for(version: int) -> str:
    return f'W/"{version}"'


def require_if_match(header: str | None) -> int:
    """The version the caller believes it is editing.

    An unparseable tag is treated as a version that cannot match, not as a malformed request.
    RFC 9110 says a failed `If-Match` is 412 whatever the reason, and the caller's next move is
    the same either way — re-read and retry.
    """
    if header is None or not header.strip():
        raise PreconditionRequired("If-Match is required for this request")
    candidate = header.strip()
    if candidate.startswith("W/"):
        candidate = candidate[2:]
    candidate = candidate.strip('"')
    try:
        return int(candidate)
    except ValueError:
        # Not a tag this API issued. `-1` matches no row, so the version guard refuses it and the
        # caller gets the 412 that a non-matching tag earns.
        return -1
