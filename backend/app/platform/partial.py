"""Telling "not mentioned" apart from "set to null".

Every context has partial updates, and every one of them needs this distinction: clearing a due date
and not mentioning a due date are different intentions, and a service that cannot tell them apart
erases data on every PATCH.

Lives in platform because it belongs to no context. It was first written inside the Work Core, which
worked until Identity needed it too — and importing it from there would have made Identity depend on
Work Core, reversing the one direction ADR-0035 fixes.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Any


class _Sentinel(enum.Enum):
    UNSET = "unset"


#: "This field was not mentioned." Distinct from `None`, which means "set it to null".
UNSET = _Sentinel.UNSET

type Maybe[T] = T | _Sentinel


def value_or_none[T](value: Maybe[T]) -> T | None:
    """The caller's value, or None when they said nothing.

    For fields where "unspecified" and "null" mean the same thing to the service — a visibility left
    to inheritance, say — collapsing them here keeps `_Sentinel` private to this module.
    """
    return None if isinstance(value, _Sentinel) else value


def changed_fields(command: object) -> dict[str, Any]:
    """The fields a caller actually set. Everything still `UNSET` is left untouched."""
    return {
        field.name: value
        for field in dataclasses.fields(command)  # type: ignore[arg-type]
        if not isinstance(value := getattr(command, field.name), _Sentinel)
    }
