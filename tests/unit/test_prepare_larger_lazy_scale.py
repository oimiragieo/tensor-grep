"""Regression tests for DF2-01 LARGER lazy candidate seeding and ego-subgraph scaling."""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import prepare_service, repo_map


def _make_large_repo(root: Path, count: int = 96) -> None:
    src = root / "src"
    tests = root / "tests"
    src.mkdir()
    tests.mkdir()
    for idx in range(count):
        (src / f"mod_{idx:04d}.py").write_text(
            f"def fn_{idx:04d}(x):\n    return x + {idx}\n",
            encoding="utf-8",
        )
    (src / "mod_0010.py").write_text(
        "from mod_0011 import fn_0011\n\n"
        "def compute_invoice_total(items):\n"
        "    return sum(fn_0011(x) for x in items)\n",
        encoding="utf-8",
    )
    (tests / "test_mod_0010.py").write_text(
        "from mod_0010 import compute_invoice_total\n\n"
        "def test_invoice():\n"
        "    assert compute_invoice_total([1]) >= 0\n",
        encoding="utf-8",
    )
    (src / "mod_0077.py").write_text(
        "def reconcile_ledger_entries(rows):\n    return len(rows)\n",
        encoding="utf-8",
    )


def test_larger_lazy_cold_and_warm_avoids_full_repo_parse(
    tmp_path: Path, monkeypatch
) -> None:
    """Cold run seeds + expands 1-2 hop ego-subgraph without full build_repo_map."""
    _make_large_repo(tmp_path, count=96)

    build_calls = 0
    parse_calls: list[str] = []
    orig_build = repo_map.build_repo_map
    orig_parse = repo_map._imports_and_symbols_for_path

    def spy_build(*args, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return orig_build(*args, **kwargs)

    def spy_parse(path: Path, *args, **kwargs):
        parse_calls.append(str(path))
        return orig_parse(path, *args, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", spy_build)
    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", spy_parse)

    cold = prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )
    assert build_calls == 0
    assert 1 <= len(parse_calls) <= prepare_service._LARGER_EGO_MAX_FILES
    assert cold["primary_target"]["symbol"] == "compute_invoice_total"
    assert cold.get("partial") is None
    assert (tmp_path / prepare_service._PREPARE_CACHE_REL_PATH).exists()
    assert (tmp_path / ".tg_cache" / "symbols_v1" / "metadata.sqlite3").exists()

    # Warm run for the same query: 0 builds, 0 parses
    parse_calls.clear()
    warm = prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="compute_invoice_total",
        claim=False,
    )
    assert build_calls == 0
    assert parse_calls == []
    assert warm["primary_target"] == cold["primary_target"]

    # Warm run for a different query lazily hydrates only the new neighborhood
    parse_calls.clear()
    warm_other = prepare_service._build_prepare_payload(
        path=str(tmp_path),
        query="reconcile_ledger_entries",
        claim=False,
    )
    assert build_calls == 0
    assert 1 <= len(parse_calls) <= prepare_service._LARGER_EGO_MAX_FILES
    assert warm_other["primary_target"]["symbol"] == "reconcile_ledger_entries"
