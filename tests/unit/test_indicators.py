from dde.indicators import CLASS_MATCH_STATE, METHOD_MATCH_STATE, jaccard, module_of
from dde.models import State


def test_jaccard_conventions() -> None:
    assert jaccard(set(), set()) == 1.0
    assert jaccard({1, 2}, {2, 3}) == 1 / 3


def test_renames_are_not_divergence() -> None:
    assert METHOD_MATCH_STATE["renamed"] is State.ZERO
    assert CLASS_MATCH_STATE["renamed"] is State.ZERO
    assert METHOD_MATCH_STATE["ambiguous"] is State.UNRESOLVED
    assert METHOD_MATCH_STATE["absent"] is State.DIVERGENT


def test_module_of() -> None:
    files = frozenset({"core/build.gradle", "core/src/A.java", "lib/src/B.java", "build.gradle"})
    assert module_of("core/src/A.java", files) == "core"
    assert module_of("lib/src/B.java", files) == ""  # no build file in lib/
    assert module_of("A.java", files) == ""
