"""Exact metadata exclusions preserve repository scan budgets and diagnostics."""

import os
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map


def _write(root: Path, name: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("value = 1\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("limit", [None, 100])
def test_exact_exclusion_keeps_same_named_sibling_and_explicit_file(
    tmp_path: Path, limit: int | None
) -> None:
    omitted = _write(tmp_path, "a/metadata.lock")
    sibling = _write(tmp_path, "b/metadata.lock")
    source = _write(tmp_path, "keep.py")
    exclusions = frozenset({omitted})

    assert set(repo_map._iter_repo_files(tmp_path, max_files=limit, exclude_files=exclusions)) == {
        sibling,
        source,
    }
    assert repo_map._iter_repo_files(omitted, exclude_files=exclusions) == []
    assert repo_map._iter_repo_files(sibling, exclude_files=exclusions) == [sibling]


def test_capped_walk_consumes_excluded_prefix_in_the_same_bucket(tmp_path: Path) -> None:
    omitted = _write(tmp_path, "a/00.lock")
    first = _write(tmp_path, "a/10.txt")
    second = _write(tmp_path, "a/20.txt")
    other = _write(tmp_path, "b/10.txt")
    empty_bucket = _write(tmp_path, "c/00.lock")

    result = repo_map._iter_repo_files(
        tmp_path, max_files=3, exclude_files=frozenset({omitted, empty_bucket})
    )

    assert result == [first, other, second]


def test_metadata_does_not_make_a_complete_two_file_budget_look_truncated(tmp_path: Path) -> None:
    source = _write(tmp_path, "keep.py")
    metadata = _write(tmp_path, ".publication.lock")

    payload = repo_map.build_repo_map(
        tmp_path, max_repo_files=2, exclude_files=frozenset({metadata})
    )

    assert payload["files"] == [str(source)]
    assert payload["scan_limit"]["scanned_files"] == 1
    assert payload["scan_limit"]["possibly_truncated"] is False
    assert payload["scan_limit"]["truncation_cause"] is None
    assert not payload.get("partial", False)


@pytest.mark.parametrize("limit", [None, 2])
def test_deadline_stops_a_long_excluded_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int | None
) -> None:
    excluded = frozenset(_write(tmp_path, f"a/{number:02}.lock") for number in range(8))
    _write(tmp_path, "a/last.txt")
    calls = 0

    def clock() -> float:
        nonlocal calls
        calls += 1
        return 99.0 + calls

    monkeypatch.setattr(repo_map.time, "monotonic", clock)
    flag = repo_map._DeadlineBreakFlag()
    result = repo_map._iter_repo_files(
        tmp_path,
        max_files=limit,
        deadline_monotonic=103.0,
        deadline_hit=flag,
        exclude_files=excluded,
    )

    assert result == []
    assert flag.hit is True
    assert calls == 4


@pytest.mark.parametrize("limit", [None, 2])
def test_empty_exclusions_preserve_order_and_clock_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int | None
) -> None:
    first = _write(tmp_path, "a/first.txt")
    second = _write(tmp_path, "a/second.txt")
    other = _write(tmp_path, "b/first.txt")
    observations = []

    for options in ({}, {"exclude_files": frozenset()}):
        calls = 0

        def clock() -> float:
            nonlocal calls
            calls += 1
            return 99.0 + calls

        monkeypatch.setattr(repo_map.time, "monotonic", clock)
        flag = repo_map._DeadlineBreakFlag()
        result = repo_map._iter_repo_files(
            tmp_path, max_files=limit, deadline_monotonic=1000.0, deadline_hit=flag, **options
        )
        observations.append((result, calls, flag.hit))

    expected = [first, other] if limit else [first, second, other]
    assert observations == [(expected, len(expected), False)] * 2


@pytest.mark.parametrize("profile", [False, True])
def test_extra_files_cannot_reintroduce_excluded_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: bool
) -> None:
    omitted = _write(tmp_path, "omitted.py")
    included = _write(tmp_path, "included.py")
    exclusions = frozenset({omitted})
    monkeypatch.chdir(tmp_path)
    seen = []

    def walk(root, **kwargs):
        seen.append(kwargs["exclude_files"])
        return []

    monkeypatch.setattr(repo_map, "_iter_repo_files", walk)
    collector = repo_map._ProfileCollector() if profile else None
    payload = repo_map.build_repo_map(
        tmp_path,
        extra_files=[Path("omitted.py"), included],
        exclude_files=frozenset({Path("omitted.py")}),
        _profiling_collector=collector,
    )

    assert seen == [exclusions]
    assert payload["files"] == [str(included)]
    assert ("_profiling" in payload) is profile
    if profile:
        phases = {phase["name"]: phase for phase in payload["_profiling"]["phases"]}
        assert phases["file_parse"]["calls"] == 1


@pytest.mark.parametrize("profile", [False, True])
def test_default_builder_keeps_fixed_signature_walker_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: bool
) -> None:
    calls = []

    def walk(
        root,
        *,
        max_files=None,
        deadline_monotonic=None,
        deadline_hit=None,
        unreadable_hit=None,
        _profiling_collector=None,
    ):
        calls.append(root)
        return []

    monkeypatch.setattr(repo_map, "_iter_repo_files", walk)
    collector = repo_map._ProfileCollector() if profile else None
    payload = repo_map.build_repo_map(tmp_path, _profiling_collector=collector)

    assert calls == [tmp_path]
    assert payload["files"] == []


@pytest.mark.parametrize("limit", [None, 10])
def test_exclusions_preserve_unreadable_subtree_reporting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int | None
) -> None:
    source = _write(tmp_path, "keep.py")
    omitted = _write(tmp_path, ".publication.lock")
    denied = _write(tmp_path, "denied/unavailable.py").parent
    real_scandir = os.scandir

    def scan(path="."):
        if os.fspath(path) == os.fspath(denied):
            raise PermissionError(13, "injected unreadable directory", str(denied))
        return real_scandir(path)

    monkeypatch.setattr(repo_map.os, "scandir", scan)
    payload = repo_map.build_repo_map(
        tmp_path, max_repo_files=limit, exclude_files=frozenset({omitted})
    )

    assert payload["files"] == [str(source)]
    assert payload["unreadable_paths"]["count"] >= 1
    assert str(denied) in payload["unreadable_paths"]["sample"]
