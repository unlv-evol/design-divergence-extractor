from dde.aggregate import combine, leaf
from dde.models import State


def test_min_and_mean_over_computed_only() -> None:
    s = combine([leaf(0.5, State.DIVERGENT, "min"), leaf(1.0, State.ZERO, "min"),
                 leaf(None, State.UNAVAILABLE, "min")], "min")
    assert (s.worst, s.mean, s.n, s.state) == (0.5, 0.75, 3, State.DIVERGENT)
    assert s.unavailable_share == 1 / 3


def test_any_becomes_share_of_divergent() -> None:
    s = combine([leaf("absent", State.DIVERGENT, "any"), leaf("exact", State.ZERO, "any")], "any")
    assert (s.worst, s.mean, s.state) == (1.0, 0.5, State.DIVERGENT)


def test_states_aggregate_conservatively() -> None:
    assert combine([leaf(None, State.UNAVAILABLE, "max")] * 2, "max").state is State.UNAVAILABLE
    mixed = combine([leaf(None, State.UNAVAILABLE, "max"), leaf(None, State.UNRESOLVED, "max")], "max")
    assert mixed.state is State.UNRESOLVED
    assert combine([leaf(None, State.NOT_APPLICABLE, "max")], "max").state is State.NOT_APPLICABLE
    assert combine([], "max").state is State.NOT_APPLICABLE
    assert combine([leaf(1.0, State.ZERO, "min")], "min").state is State.ZERO


def test_rollups_count_each_class_once() -> None:
    class_a = combine([leaf(0.0, State.DIVERGENT, "min")] * 9, "min")
    class_b = combine([leaf(1.0, State.ZERO, "min")], "min")
    file_ = combine([class_a, class_b], "min")
    assert file_.mean == 0.5  # not 0.1
