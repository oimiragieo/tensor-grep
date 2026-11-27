from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tensor_grep.core.reranker import HYBRID_INDEX_REL_PATH, hybrid_rerank
from tensor_grep.core.retrieval_chunker import Chunk


def _run_find_hybrid(repo_root: Path, query: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tensor_grep.cli.main",
            "find",
            "--hybrid",
            "--json",
            query,
            str(repo_root),
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _seed_repo(repo_root: Path) -> None:
    (repo_root / "alpha.py").write_text(
        "def unique_symbol_alpha() -> int:\n    return 42\n",
        encoding="utf-8",
    )
    (repo_root / "beta.py").write_text(
        "def unique_symbol_beta() -> int:\n    return 7\n",
        encoding="utf-8",
    )
    (repo_root / "gamma.py").write_text(
        "def unrelated_helper() -> str:\n    return 'ok'\n",
        encoding="utf-8",
    )


def test_find_hybrid_4_parallel_processes_cold_and_warm(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_repo(repo_root)

    queries = [
        ("unique_symbol_alpha", "alpha.py"),
        ("unique_symbol_beta", "beta.py"),
        ("unique_symbol_alpha", "alpha.py"),
        ("unique_symbol_beta", "beta.py"),
    ]

    # Cold start: 4 parallel subprocesses race to create and publish hybrid_v1/index.sqlite3.
    with ThreadPoolExecutor(max_workers=4) as pool:
        cold_results = list(pool.map(lambda item: _run_find_hybrid(repo_root, item[0]), queries))

    for proc, (_query, expected_file) in zip(cold_results, queries, strict=True):
        assert proc.returncode == 0, f"stderr={proc.stderr!r} stdout={proc.stdout!r}"
        payload = json.loads(proc.stdout)
        assert payload["matches"]
        assert payload["matches"][0]["file"].endswith(expected_file)
        assert payload["routing_reason"] == "hybrid-fts5-rrf"

    assert (repo_root / HYBRID_INDEX_REL_PATH).exists()

    # Warm repo: 4 parallel subprocesses read and re-publish the existing hybrid_v1/index.sqlite3.
    with ThreadPoolExecutor(max_workers=4) as pool:
        warm_results = list(pool.map(lambda item: _run_find_hybrid(repo_root, item[0]), queries))

    for proc, (_query, expected_file) in zip(warm_results, queries, strict=True):
        assert proc.returncode == 0, f"stderr={proc.stderr!r} stdout={proc.stdout!r}"
        payload = json.loads(proc.stdout)
        assert payload["matches"]
        assert payload["matches"][0]["file"].endswith(expected_file)
        assert payload["routing_reason"] == "hybrid-fts5-rrf"


def test_hybrid_rerank_retries_transient_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.io import confined

    real_publish = confined.publish_confined
    attempts = 0

    def _flaky_publish(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts <= 2 and str(args[1]).endswith("index.sqlite3"):
            raise PermissionError(13, "sharing violation")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(confined, "publish_confined", _flaky_publish)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    chunks = [
        Chunk(file_path="a.py", start_line=1, end_line=1, text="def first_fn(): pass"),
        Chunk(file_path="b.py", start_line=1, end_line=1, text="def target_fn(): pass"),
    ]
    order = hybrid_rerank("target_fn", chunks, cache_root=tmp_path)
    assert order == [1]
    assert attempts == 3
