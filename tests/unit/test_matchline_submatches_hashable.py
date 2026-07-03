"""MatchLine must stay hashable even when submatches is populated (regression from #340,
found by the blast-radius-regression workflow 2026-07-03), and the RipgrepBackend must only
stash submatch offsets when a column-emitting formatter will consume them."""

from tensor_grep.core.result import MatchLine


def test_matchline_hashable_without_submatches() -> None:
    m = MatchLine(line_number=1, text="x", file="a.py")
    assert hash(m) == hash(MatchLine(line_number=1, text="x", file="a.py"))


def test_matchline_hashable_with_submatches_populated() -> None:
    # A tuple of dicts is unhashable; compare=False must keep the frozen dataclass hashable.
    m = MatchLine(
        line_number=1,
        text="ab ab",
        file="a.py",
        submatches=({"start": 0, "end": 2}, {"start": 3, "end": 5}),
    )
    hash(m)  # must not raise TypeError
    # usable in a set (dedup) — the whole point of a frozen dataclass
    assert len({m, m}) == 1


def test_submatches_excluded_from_equality() -> None:
    # The offsets are a pure function of text+line, so two matches equal on the visible fields
    # are equal regardless of the derived offsets.
    base = MatchLine(line_number=1, text="ab", file="a.py")
    with_subs = MatchLine(
        line_number=1, text="ab", file="a.py", submatches=({"start": 0, "end": 2},)
    )
    assert base == with_subs
    assert hash(base) == hash(with_subs)
