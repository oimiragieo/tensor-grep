import json
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map
from tensor_grep.cli.repo_map_ranking import (
    _filename_phrase_matches,
    _promote_filename_phrase,
)


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("query", "files", "symbols"),
    [
        (
            "create_invoice",
            [("src/core.py", 65), ("src/service.py", 31), ("third_party/widget.py", 16)],
            [("create_invoice", "src/core.py", 3), ("create_invoice", "third_party/widget.py", 3)],
        ),
        (
            "invoice total",
            [("src/service.py", 37), ("src/core.py", 31), ("third_party/widget.py", 8)],
            [
                ("create_invoice", "src/core.py", 4),
                ("build_invoice", "src/service.py", 4),
                ("create_invoice", "third_party/widget.py", 4),
            ],
        ),
        ("rank", [("src/other.py", 19)], [("rerank_value", "src/other.py", 3)]),
    ],
)
def test_existing_unrelated_exact_and_deweighted_ranking_pin(tmp_path, query, files, symbols):
    _write(tmp_path, "src/core.py", "def create_invoice(total):\n    return total + 1\n")
    _write(
        tmp_path,
        "src/service.py",
        "from src.core import create_invoice\n\ndef build_invoice(total):\n"
        "    return create_invoice(total)\n",
    )
    _write(tmp_path, "src/other.py", "def rerank_value(total):\n    return total\n")
    _write(tmp_path, "third_party/widget.py", "def create_invoice(total):\n    return total\n")
    payload = repo_map.build_context_pack(query, tmp_path)
    assert [
        (Path(item["path"]).relative_to(tmp_path).as_posix(), item["score"])
        for item in payload["file_matches"]
    ] == files
    assert [
        (item["name"], Path(item["file"]).relative_to(tmp_path).as_posix(), item["score"])
        for item in payload["symbols"]
    ] == symbols


def _repair_project(root: Path) -> Path:
    target = _write(root, "src/repair_env.py", "def repair_environment():\n    return 'ready'\n")
    _write(
        root,
        "src/cli/main.py",
        "\n".join(
            f"def repair_env_action_{index}():\n    return 'repair env'\n" for index in range(24)
        ),
    )
    return target


def test_filename_preference_is_order_only_and_preserves_other_candidates(tmp_path, monkeypatch):
    target = _repair_project(tmp_path)
    _write(tmp_path, "src/other.py", "def repair_helper():\n    return 'env'\n")
    inventory = repo_map.build_repo_map(tmp_path)
    with monkeypatch.context() as control:
        control.setattr(repo_map, "_promote_filename_phrase", lambda *args, **kwargs: None)
        baseline = repo_map.build_context_pack_from_map(inventory, "repair env")
    assert baseline["files"][0].endswith("main.py")  # Reproduces dispatcher competition.
    result = repo_map.build_context_pack_from_map(inventory, "repair env")
    target_path = str(target.resolve())
    assert result["files"][0] == target_path
    assert result["files"][1:] == [path for path in baseline["files"] if path != target_path]
    assert {item["path"]: item["score"] for item in result["file_matches"]} == {
        item["path"]: item["score"] for item in baseline["file_matches"]
    }

    def key(item):
        return item["file"], item["name"], item["line"], item["score"]

    assert [key(item) for item in result["symbols"] if item["file"] != target_path] == [
        key(item) for item in baseline["symbols"] if item["file"] != target_path
    ]
    assert sorted(map(key, result["symbols"])) == sorted(map(key, baseline["symbols"]))
    assert inventory["symbols"] == repo_map.build_repo_map(tmp_path)["symbols"]


@pytest.mark.parametrize("mode", ["render", "edit"])
def test_context_and_edit_agree_under_minimum_budgets(tmp_path, mode):
    target = _repair_project(tmp_path)
    inventory = repo_map.build_repo_map(tmp_path)
    if mode == "render":
        payload = repo_map.build_context_render_from_map(
            inventory, "repair env", max_files=1, max_sources=1, max_symbols_per_file=1
        )
    else:
        payload = repo_map.build_context_edit_plan_from_map(
            inventory, "repair env", max_files=1, max_sources=1, max_symbols=1
        )
    assert payload["files"] == [str(target.resolve())]
    assert payload["symbols"][0]["name"] == "repair_environment"
    primary = payload["edit_plan_seed"]["primary_symbol"]
    assert (primary["file"], primary["name"], primary["line"]) == (
        str(target.resolve()),
        "repair_environment",
        1,
    )


@pytest.mark.parametrize(
    ("filename", "query", "expected"),
    [
        ("repair_env.py", "repair env", True),
        ("repairEnv.py", "please repair env now", True),
        ("repair_env.py", "env repair", False),
        ("repair_env.py", "repair broken env", False),
        ("repair_env.py", "repair environment", False),
        ("repair_environment.py", "repair env", False),
        ("repair_env_helper.py", "repair env", False),
        ("repair.py", "repair env", False),
        ("repair_repair.py", "repair repair env", True),
        ("repair_repair.py", "repair env", False),
    ],
)
def test_filename_phrase_uses_complete_ordered_contiguous_tokens(filename, query, expected):
    assert _filename_phrase_matches(filename, query) is expected


@pytest.mark.parametrize("query", ["env repair", "repair broken env", "repair environment"])
def test_negative_phrases_preserve_entire_ranking(tmp_path, monkeypatch, query):
    _repair_project(tmp_path)
    inventory = repo_map.build_repo_map(tmp_path)
    with monkeypatch.context() as control:
        control.setattr(repo_map, "_promote_filename_phrase", lambda *args, **kwargs: None)
        baseline = repo_map.build_context_pack_from_map(inventory, query)
    assert repo_map.build_context_pack_from_map(inventory, query) == baseline


@pytest.mark.parametrize("symbol", ["repair_env", "repairEnv"])
def test_exact_and_bridge_symbols_override_filename_phrase(tmp_path, symbol):
    target = _repair_project(tmp_path)
    exact = _write(tmp_path, "src/exact.py", f"def {symbol}():\n    return 1\n")
    payload = repo_map.build_context_edit_plan(
        symbol, tmp_path, max_files=1, max_symbols=1, max_sources=1
    )
    assert payload["edit_plan_seed"]["primary_symbol"]["file"] == str(exact.resolve())
    assert not any(item.get("filename_phrase_match") for item in payload["symbols"])
    assert str(target.resolve()) != payload["symbols"][0]["file"]


def test_bridge_evidence_suppresses_filename_preference_for_natural_phrase(tmp_path):
    _repair_project(tmp_path)
    exact = _write(tmp_path, "src/exact.py", "def repair_env():\n    return 1\n")
    payload = repo_map.build_context_pack("repair env", tmp_path)
    assert payload["symbols"][0]["file"] == str(exact.resolve())
    assert payload["symbols"][0]["bridge_query_match"]
    assert not any(item.get("filename_phrase_match") for item in payload["symbols"])


def test_duplicate_stems_use_existing_score_and_path_order(tmp_path, monkeypatch):
    _repair_project(tmp_path)
    duplicate = _write(
        tmp_path,
        "src/alternate/repair_env.py",
        "def repair_environment_state():\n    return 'env'\n",
    )
    inventory = repo_map.build_repo_map(tmp_path)
    with monkeypatch.context() as control:
        control.setattr(repo_map, "_promote_filename_phrase", lambda *args, **kwargs: None)
        baseline = repo_map.build_context_pack_from_map(inventory, "repair env")
    expected = next(path for path in baseline["files"] if Path(path).stem == "repair_env")
    result = repo_map.build_context_pack_from_map(inventory, "repair env")
    assert str(duplicate.resolve()) in result["files"]
    assert result["files"][0] == expected


def test_classified_vendor_generated_tests_and_language_are_ineligible(tmp_path):
    target = _repair_project(tmp_path)
    for directory in ["third_party", "src/generated", "tests", "src/vendor"]:
        _write(
            tmp_path, f"{directory}/repair_env.py", "def repair_environment_many():\n    return 1\n"
        )
    _write(tmp_path, "src/repair_env.js", "function repairEnvironmentMany() { return 1; }\n")
    _write(tmp_path, "src/generated/pyproject.toml", "[project]\nname = 'generated-fixture'\n")
    _write(tmp_path, "src/generated/support.py", "def generated_support():\n    return 1\n")
    _write(tmp_path, "src/generated/consumer.py", "from .support import generated_support\n")
    payload = repo_map.build_context_pack("repair env python", tmp_path)
    assert str(tmp_path / "src/generated") in {item["path"] for item in payload["deweighted_trees"]}
    assert payload["files"][0] == str(target.resolve())
    flagged = [item for item in payload["symbols"] if item.get("filename_phrase_match")]
    assert flagged and {item["file"] for item in flagged} == {str(target.resolve())}


@pytest.mark.parametrize("directory", ["third_party", "_vendored", ".claude", "vendor"])
def test_known_vendor_paths_remain_ineligible_when_deweighting_disabled(tmp_path, directory):
    _write(
        tmp_path,
        f"{directory}/repair_env.py",
        "def repair_environment():\n    return 'repair env'\n",
    )
    _write(tmp_path, "src/main.py", "def repair_environment():\n    return 1\n")
    inventory = repo_map.build_repo_map(tmp_path)
    payload = repo_map.build_context_pack_from_map(inventory, "repair env", auto_deweight=False)
    assert not any(item.get("filename_phrase_match") for item in payload["symbols"])


def test_marker_promotion_still_runs_before_filename_preference_guard(tmp_path):
    marker = {
        "name": "env_marker",
        "file": "src/repair_env.py",
        "score": 8,
        "line": 1,
        "end_line": 2,
        "filename_phrase_match": True,
    }
    implementation = {
        "name": "repair_environment",
        "file": "src/runtime.py",
        "score": 8,
        "line": 1,
        "end_line": 5,
    }
    promoted = repo_map._promote_substantive_symbol_for_edit_seed(
        marker,
        [marker, implementation],
        query="repair env",
        selected_files={marker["file"], implementation["file"]},
        query_language_hints=[],
        primary_file_reasons={"filename-phrase"},
    )
    assert promoted is implementation


def test_later_symbol_sort_preserves_order_within_promoted_file(tmp_path):
    target = str(_write(tmp_path, "repair_env.py", "").resolve())
    covered = {"file": target, "name": "repair", "line": 1, "score": 3, "covered_query_match": True}
    high_score = {"file": target, "name": "environment", "line": 2, "score": 50}
    other = {
        "file": str(tmp_path / "main.py"),
        "name": "repair_env_handler",
        "line": 1,
        "score": 100,
    }
    symbols = repo_map._sorted_ranked_symbols([high_score, other, covered])
    files = [(1000, other["file"]), (10, target)]
    _promote_filename_phrase(
        files,
        symbols,
        payload={"path": str(tmp_path)},
        query="repair env",
        query_language_hints=[],
        deweighted_trees={},
        file_reasons={},
    )
    assert symbols == [covered, high_score, other]
    assert repo_map._sorted_ranked_symbols(symbols) == symbols


def test_thin_cli_dispatcher_still_promotes_its_implementation(tmp_path):
    from tensor_grep.cli.agent_capsule_targets import (
        _prefer_implementation_over_cli_dispatcher_helper,
    )

    wrapper = _write(
        tmp_path,
        "src/cli/repair_env.py",
        "@app.command()\ndef repair_environment_command():\n    return install_runtime()\n",
    )
    primary = {
        "file": str(wrapper),
        "symbol": "repair_environment_command",
        "kind": "function",
        "line": 2,
        "confidence": 0.8,
        "filename_phrase_match": True,
    }
    implementation = {
        "file": str(tmp_path / "src/runtime.py"),
        "symbol": "install_runtime",
        "kind": "function",
        "line": 1,
        "confidence": 0.7,
    }
    target, alternatives = _prefer_implementation_over_cli_dispatcher_helper(
        primary, [implementation]
    )
    assert target is implementation
    assert alternatives == [primary]


def test_cli_mcp_and_cached_session_share_filename_preference(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from tensor_grep.cli import mcp_server, session_store
    from tensor_grep.cli.main import app

    monkeypatch.chdir(tmp_path)
    project = tmp_path / "project"
    target = _repair_project(project)
    result = CliRunner().invoke(app, ["context", "repair env", str(project), "--json"])
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["files"][0] == str(target.resolve())
    assert json.loads(mcp_server.tg_context("pack", "repair env", str(project)))["files"][0] == str(
        target.resolve()
    )
    planned = json.loads(
        mcp_server.tg_context(
            "edit_plan", "repair env", str(project), max_files=1, max_symbols=1, max_sources=1
        )
    )
    assert planned["edit_plan_seed"]["primary_symbol"]["file"] == str(target.resolve())
    opened = session_store.open_session(str(project))
    for _ in range(2):
        payload = json.loads(
            mcp_server.tg_session_context(opened.session_id, "repair env", str(project))
        )
        assert payload["files"][0] == str(target.resolve())
