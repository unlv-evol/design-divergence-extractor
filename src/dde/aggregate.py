"""Stage 8: roll values up method -> class -> file -> patch (spec Section 8).

Each level reports the most divergent value and the mean over computed
contributions, the number of contributions, a state, and the share of
contributions that were ``unavailable``. A class counts once however many of
its methods were edited.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from dde.models import State

# How "most divergent" is defined for each rolled-up indicator.
WORST = {
    "method_match": "any",
    "contract": "any",
    "unresolved_ratio": "max",
    "class_match": "any",
    "method_jaccard": "min",
    "field_jaccard": "min",
    "n_lost": "max",
    "n_relocated": "max",
    "collab_jaccard": "min",
    "supertypes_changed": "any",
    "relocation": "any",
}
METHOD_LEVEL = ("method_match", "contract", "unresolved_ratio")
CLASS_LEVEL = ("class_match", "method_jaccard", "field_jaccard", "n_lost", "n_relocated",
               "collab_jaccard", "supertypes_changed")
FILE_LEVEL = ("relocation",)


@dataclass(frozen=True)
class Summary:
    worst: float | None
    mean: float | None
    n: int
    state: State
    unavailable_share: float | None


def leaf(value: float | int | bool | str | None, state: State, kind: str) -> Summary:
    """A single entity's value as a one-element summary.

    For ``any`` indicators the value is 1 when the state is divergent, else 0,
    so the mean becomes the share of divergent entities.
    """
    if state.computed:
        if kind == "any":
            v: float | None = 1.0 if state is State.DIVERGENT else 0.0
        else:
            v = float(value) if value is not None and not isinstance(value, str) else None
    else:
        v = None
    return Summary(v, v, 1, state, 1.0 if state is State.UNAVAILABLE else 0.0)


def combine(items: Iterable[Summary], kind: str) -> Summary:
    items = list(items)
    if not items:
        return Summary(None, None, 0, State.NOT_APPLICABLE, None)
    states = [s.state for s in items]
    unavailable_share = sum(st is State.UNAVAILABLE for st in states) / len(items)
    computed = [s for s in items if s.state.computed and s.worst is not None]
    if not computed:
        if all(st is State.UNAVAILABLE for st in states):
            state = State.UNAVAILABLE
        elif State.UNRESOLVED in states:
            state = State.UNRESOLVED
        elif State.UNAVAILABLE in states:
            state = State.UNAVAILABLE
        else:
            state = State.NOT_APPLICABLE
        return Summary(None, None, len(items), state, unavailable_share)
    worsts = [s.worst for s in computed if s.worst is not None]
    means = [s.mean for s in computed if s.mean is not None]
    worst = min(worsts) if kind == "min" else max(worsts)
    state = State.DIVERGENT if any(s.state is State.DIVERGENT for s in computed) else State.ZERO
    mean = sum(means) / len(means) if means else None
    return Summary(worst, mean, len(items), state, unavailable_share)
