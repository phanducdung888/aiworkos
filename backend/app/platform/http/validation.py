"""Request-level validation shared by every entry point.

One rule lives here so far, and it is here rather than in a schema module because a request string
arrives by more than one route: a JSON body field, and a header that is stored just as a body field
is. Both reach the same text column, so both need the same answer.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator


def reject_nul(value: str) -> str:
    """Refuse U+0000 in anything destined for a text column.

    PostgreSQL cannot store a NUL byte in `text` or `varchar`, and psycopg refuses to encode the
    parameter before the statement is even sent — so a JSON string containing one is not a value
    this system can accept. Pydantic sees a perfectly ordinary `str`, which is how a request that
    could never have succeeded reached the database and came back as a 500.

    Rejected at the edge for the reason any validation belongs there: the caller is told which field
    was wrong, and nothing is written first. The exception rises as a `RequestValidationError` and
    leaves as the same 422 problem+json every other malformed request gets.

    Deliberately only U+0000. Newlines, tabs, accents, emoji and every other control character are
    text PostgreSQL stores without complaint, and no business rule forbids them; a wider blacklist
    here would be a rule nobody wrote, enforced in the layer least able to explain itself.
    """
    if "\x00" in value:
        raise ValueError("must not contain the NUL character (U+0000)")
    return value


#: Every request string that ends up in a text column, whether it arrives in a body or a header.
CleanText = Annotated[str, AfterValidator(reject_nul)]
