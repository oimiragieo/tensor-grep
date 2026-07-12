"""DAR (Dependency-Aware Retrieval, arxiv steal #4): the `tg agent` capsule surfaces the primary
target's OUTBOUND dependencies (imports + callees) as budget-isolated related-context, so an
agent can edit without extra file reads.

THE TRAP these tests guard against: `repo_map.build_context_render`'s compact render profile
(what `build_agent_capsule` always requests -- `render_profile="full"` + `optimize_context=True`
normalizes to "compact") POPS `payload["symbols"]`/`payload["imports"]`. A naive DAR reading
those keys directly would be silently empty forever. Most tests here build REAL on-disk files (so
`repo_map._imports_and_symbols_for_path` parses a real import statement) while monkeypatching
`repo_map.build_context_render` to deterministically control which snippet source,
`file_summaries`, and `candidate_edit_targets` land in the capsule -- the same pattern used by
`test_agent_capsule_token_budget_confidence.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import agent_capsule, repo_map
from tensor_grep.cli.main import app

_PRIMARY_SYMBOL = "handle_widget_request"
_DEPENDENCY_SYMBOL = "compute_widget_total"
_PRIMARY_SOURCE = f"def {_PRIMARY_SYMBOL}(payload):\n    return {_DEPENDENCY_SYMBOL}(payload)\n"
_MANY_DEP_NAMES = [f"dep_{index}" for index in range(9)]


@pytest.fixture(autouse=True)
def _dar_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # DAR ships default-OFF (opt-in, matching the other retrieval-quality features). These tests
    # exercise the ENABLED behavior, so opt in here; the kill-switch/off tests below explicitly
    # re-set "0"/off-values (a later setenv wins), and the default-off test below delenv's it.
    monkeypatch.setenv("TG_CAPSULE_OUTBOUND_DEPS", "1")


def test_dar_defaults_off_when_env_unset() -> None:
    import os as _os

    saved = _os.environ.pop("TG_CAPSULE_OUTBOUND_DEPS", None)
    try:
        assert agent_capsule._capsule_outbound_dependencies_enabled() is False
        _os.environ["TG_CAPSULE_OUTBOUND_DEPS"] = "1"
        assert agent_capsule._capsule_outbound_dependencies_enabled() is True
        _os.environ["TG_CAPSULE_OUTBOUND_DEPS"] = "0"
        assert agent_capsule._capsule_outbound_dependencies_enabled() is False
    finally:
        if saved is None:
            _os.environ.pop("TG_CAPSULE_OUTBOUND_DEPS", None)
        else:
            _os.environ["TG_CAPSULE_OUTBOUND_DEPS"] = saved


def _write_dar_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    primary_file = project / "handler.py"
    primary_file.write_text(
        f"from caller_helpers import {_DEPENDENCY_SYMBOL}\n\n\n" + _PRIMARY_SOURCE,
        encoding="utf-8",
    )
    dependency_file = project / "caller_helpers.py"
    dependency_file.write_text(
        f"def {_DEPENDENCY_SYMBOL}(payload):\n    return payload\n",
        encoding="utf-8",
    )
    return {"project": project, "primary": primary_file, "dependency": dependency_file}


def _write_no_deps_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    primary_file = project / "handler.py"
    primary_file.write_text(
        f"def {_PRIMARY_SYMBOL}(payload):\n    return payload\n",
        encoding="utf-8",
    )
    return {"project": project, "primary": primary_file}


def _write_confidence_isolation_project(tmp_path: Path) -> dict[str, Path]:
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    primary_file = project / "handler.py"
    primary_file.write_text(
        f"from caller_helpers import {_DEPENDENCY_SYMBOL}\n\n\n" + _PRIMARY_SOURCE,
        encoding="utf-8",
    )
    dependency_file = project / "caller_helpers.py"
    dependency_file.write_text(
        f"def {_DEPENDENCY_SYMBOL}(payload):\n    return payload\n",
        encoding="utf-8",
    )
    caller_file = project / "caller.py"
    caller_file.write_text(
        f"from handler import {_PRIMARY_SYMBOL}\n\n\n"
        f"def process_incoming_request(payload):\n    return {_PRIMARY_SYMBOL}(payload)\n",
        encoding="utf-8",
    )
    return {
        "project": project,
        "primary": primary_file,
        "dependency": dependency_file,
        "caller": caller_file,
    }


def _write_many_deps_project(tmp_path: Path) -> dict[str, Any]:
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    primary_file = project / "handler.py"
    import_line = f"from many_helpers import {', '.join(_MANY_DEP_NAMES)}\n"
    call_lines = "".join(f"    {name}(payload)\n" for name in _MANY_DEP_NAMES)
    primary_source = f"def {_PRIMARY_SYMBOL}(payload):\n{call_lines}    return payload\n"
    primary_file.write_text(import_line + "\n\n" + primary_source, encoding="utf-8")
    dependency_file = project / "many_helpers.py"
    dependency_file.write_text(
        "".join(f"def {name}(payload):\n    return payload\n\n\n" for name in _MANY_DEP_NAMES),
        encoding="utf-8",
    )
    return {
        "project": project,
        "primary": primary_file,
        "dependency": dependency_file,
        "primary_source": primary_source,
    }


def _dar_context_payload(
    *,
    primary_file: Path,
    primary_symbol: str = _PRIMARY_SYMBOL,
    primary_source: str = _PRIMARY_SOURCE,
    file_summaries: list[dict[str, Any]] | None = None,
    candidate_symbols: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    end_line = max(1, len(primary_source.splitlines()))
    return {
        "routing_backend": "RepoMap",
        "routing_reason": "context-render",
        "semantic_provider": "native",
        "files": [str(primary_file)],
        "sources": [
            {
                "file": str(primary_file),
                "symbol": primary_symbol,
                "name": primary_symbol,
                "start_line": 1,
                "end_line": end_line,
                "source": primary_source,
            },
        ],
        "validation_commands": ["uv run pytest -q"],
        "edit_plan_seed": {
            "primary_file": str(primary_file),
            "primary_symbol": {"name": primary_symbol, "kind": "function"},
            "primary_span": {"start_line": 1, "end_line": end_line},
            "confidence": {"overall": 0.9},
            "validation_plan": [
                {
                    "runner": "pytest",
                    "scope": "repo",
                    "target": "",
                    "command": "uv run pytest -q",
                    "confidence": 0.55,
                    "detection": "detected",
                }
            ],
            "validation_commands": ["uv run pytest -q"],
            "validation_alignment": {"status": "aligned", "kept_count": 1, "filtered_count": 0},
            "edit_ordering": [str(primary_file)],
        },
        "navigation_pack": {
            "primary_target": {
                "file": str(primary_file),
                "symbol": primary_symbol,
                "kind": "function",
                "start_line": 1,
                "end_line": end_line,
                "confidence": {"overall": 0.9},
            },
            "follow_up_reads": [],
        },
        "candidate_edit_targets": {
            "files": [str(primary_file)],
            "symbols": candidate_symbols or [],
            "tests": [],
        },
        "file_summaries": file_summaries or [],
        "context_consistency": {
            "primary_file_included": True,
            "rendered_context_includes_primary": True,
        },
    }


# ---------------------------------------------------------------------------------------------
# (1) Known deps included, tagged, resolved, refetch-able.
# ---------------------------------------------------------------------------------------------


def test_dar_surfaces_known_outbound_dependency_with_resolved_location(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_dar_project(tmp_path)
    monkeypatch.setattr(
        repo_map,
        "build_context_render_from_map",
        lambda *args, **kwargs: _dar_context_payload(
            primary_file=paths["primary"].resolve(),
            file_summaries=[
                {
                    "path": str(paths["dependency"].resolve()),
                    "symbols": [{"name": _DEPENDENCY_SYMBOL, "kind": "function", "line": 1}],
                },
            ],
        ),
    )

    payload = agent_capsule.build_agent_capsule(_PRIMARY_SYMBOL, paths["project"])

    deps = payload["outbound_dependencies"]
    assert len(deps) == 1
    record = deps[0]
    assert record["symbol"] == _DEPENDENCY_SYMBOL
    assert record["relation"] == "outbound-dependency"
    assert record["dependency_kind"] == "call+import"
    assert record["file"] == str(paths["dependency"].resolve())
    assert record["line"] == 1
    assert record["kind"] == "function"
    assert record["reason"] == "primary target calls this symbol"
    assert record["refetch"]["argv"] == [
        "tg",
        "source",
        str(paths["dependency"].resolve()),
        _DEPENDENCY_SYMBOL,
        "--json",
    ]

    evidence = payload["outbound_dependency_evidence"]
    assert evidence["status"] == "collected"
    assert evidence["symbol"] == _PRIMARY_SYMBOL
    assert evidence["returned_dependencies"] == 1
    assert evidence["omitted_dependencies"] == 0
    assert evidence["provenance"] == ["parser-backed"]


def test_dar_cli_end_to_end_surfaces_known_outbound_dependency(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    tax_path = src_dir / "tax.py"
    tax_path.write_text(
        "def compute_tax(total, rate):\n    return total * rate\n",
        encoding="utf-8",
    )
    payments_path = src_dir / "payments.py"
    payments_path.write_text(
        "from src.tax import compute_tax\n\n\n"
        "def create_invoice(total, rate):\n"
        "    return total + compute_tax(total, rate)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(100, 0.1) == 110\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["agent", "--query", "change invoice tax calculation", "--json", str(project)],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["primary_target"]["symbol"] == "create_invoice"

    deps = payload.get("outbound_dependencies", [])
    resolved = next((record for record in deps if record["symbol"] == "compute_tax"), None)
    assert resolved is not None, deps
    assert resolved["file"] == str(tax_path.resolve())
    assert resolved["relation"] == "outbound-dependency"
    assert resolved["refetch"]["argv"][:2] == ["tg", "source"]


# ---------------------------------------------------------------------------------------------
# (2) No deps -> NEITHER key, byte-identical to a TG_CAPSULE_OUTBOUND_DEPS=0 run.
# ---------------------------------------------------------------------------------------------


def test_dar_no_deps_emits_neither_key_and_matches_kill_switch_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_no_deps_project(tmp_path)
    primary_source = f"def {_PRIMARY_SYMBOL}(payload):\n    return payload\n"
    monkeypatch.setattr(
        repo_map,
        "build_context_render_from_map",
        lambda *args, **kwargs: _dar_context_payload(
            primary_file=paths["primary"].resolve(),
            primary_source=primary_source,
        ),
    )

    payload_env_on = agent_capsule.build_agent_capsule(_PRIMARY_SYMBOL, paths["project"])
    assert "outbound_dependencies" not in payload_env_on
    assert "outbound_dependency_evidence" not in payload_env_on

    monkeypatch.setenv("TG_CAPSULE_OUTBOUND_DEPS", "0")
    payload_env_off = agent_capsule.build_agent_capsule(_PRIMARY_SYMBOL, paths["project"])

    assert json.dumps(payload_env_on, sort_keys=True) == json.dumps(
        payload_env_off,
        sort_keys=True,
    )


# ---------------------------------------------------------------------------------------------
# (3) Upstream not crowded: budget isolation + K-cap + preview-budget-zero skeletons.
# ---------------------------------------------------------------------------------------------


def test_dar_caps_at_k_and_never_crowds_upstream_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_many_deps_project(tmp_path)
    primary_source = paths["primary_source"]
    file_summaries = [
        {
            "path": str(paths["dependency"].resolve()),
            "symbols": [
                {"name": name, "kind": "function", "line": index * 4 + 1}
                for index, name in enumerate(_MANY_DEP_NAMES)
            ],
        },
    ]

    def _payload(*args: object, **kwargs: object) -> dict[str, Any]:
        return _dar_context_payload(
            primary_file=paths["primary"].resolve(),
            primary_source=primary_source,
            file_summaries=file_summaries,
        )

    monkeypatch.setattr(repo_map, "build_context_render_from_map", _payload)

    payload_on = agent_capsule.build_agent_capsule(
        _PRIMARY_SYMBOL,
        paths["project"],
        max_tokens=160,
    )
    monkeypatch.setenv("TG_CAPSULE_OUTBOUND_DEPS", "0")
    payload_off = agent_capsule.build_agent_capsule(
        _PRIMARY_SYMBOL,
        paths["project"],
        max_tokens=160,
    )

    # Upstream fields are element-EQUAL regardless of DAR -- outbound deps are metadata OUTSIDE
    # the snippet/caller token budget (load-bearing budget-isolation contract).
    assert payload_on["snippets"] == payload_off["snippets"]
    assert payload_on["related_call_sites"] == payload_off["related_call_sites"]
    assert payload_on["omissions"] == payload_off["omissions"]

    evidence = payload_on["outbound_dependency_evidence"]
    assert evidence["max_dependencies"] == 6
    assert evidence["returned_dependencies"] == 6
    assert evidence["omitted_dependencies"] == 3
    deps = payload_on["outbound_dependencies"]
    assert len(deps) == 6
    returned_names = {record["symbol"] for record in deps}
    assert returned_names == set(_MANY_DEP_NAMES[:6])


def test_dar_records_ship_without_text_when_preview_budget_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_dar_project(tmp_path)
    monkeypatch.setattr(
        repo_map,
        "build_context_render_from_map",
        lambda *args, **kwargs: _dar_context_payload(
            primary_file=paths["primary"].resolve(),
            file_summaries=[
                {
                    "path": str(paths["dependency"].resolve()),
                    "symbols": [{"name": _DEPENDENCY_SYMBOL, "kind": "function", "line": 1}],
                },
            ],
        ),
    )
    used_tokens = repo_map._estimate_tokens(_PRIMARY_SOURCE)

    payload = agent_capsule.build_agent_capsule(
        _PRIMARY_SYMBOL,
        paths["project"],
        max_tokens=used_tokens,
    )

    # The primary snippet itself still fits exactly -- DAR still runs (skeletons present).
    assert payload["snippets"]
    deps = payload["outbound_dependencies"]
    assert deps
    assert all("text" not in record for record in deps)
    assert payload["outbound_dependency_evidence"]["preview_token_budget_remaining"] == 0


# ---------------------------------------------------------------------------------------------
# (4) Confidence isolation: DAR must never touch confidence/consistency/ask-user state.
# ---------------------------------------------------------------------------------------------


def test_dar_never_mutates_confidence_or_consistency_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_confidence_isolation_project(tmp_path)
    monkeypatch.setattr(
        repo_map,
        "build_context_render_from_map",
        lambda *args, **kwargs: _dar_context_payload(
            primary_file=paths["primary"].resolve(),
            file_summaries=[
                {
                    "path": str(paths["dependency"].resolve()),
                    "symbols": [{"name": _DEPENDENCY_SYMBOL, "kind": "function", "line": 1}],
                },
            ],
        ),
    )

    payload_on = agent_capsule.build_agent_capsule(_PRIMARY_SYMBOL, paths["project"])
    assert payload_on["outbound_dependencies"]  # sanity: DAR actually ran this time
    assert payload_on["call_site_evidence"]["status"] == "collected"

    monkeypatch.setenv("TG_CAPSULE_OUTBOUND_DEPS", "0")
    payload_off = agent_capsule.build_agent_capsule(_PRIMARY_SYMBOL, paths["project"])
    assert "outbound_dependencies" not in payload_off

    assert payload_on["confidence"] == payload_off["confidence"]
    assert payload_on["ask_user_before_editing"] == payload_off["ask_user_before_editing"]
    assert payload_on["context_consistency"] == payload_off["context_consistency"]
    assert payload_on["primary_target"]["confidence"] == payload_off["primary_target"]["confidence"]


# ---------------------------------------------------------------------------------------------
# (5) Fail-safe: parse error / missing snippet / no primary symbol -> no keys, no exception.
# ---------------------------------------------------------------------------------------------


def test_dar_fail_safe_on_parse_error_emits_no_keys_and_does_not_raise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_dar_project(tmp_path)
    # task #108: build_agent_capsule now runs a REAL build_repo_map before delegating to
    # build_agent_capsule_from_map (the map is shared with the daemon-moat call-site-evidence
    # step instead of a second independent scan) -- that initial scan would ALSO hit the
    # _imports_and_symbols_for_path mock below and blow up before DAR's own fail-safe is ever
    # exercised. Mock it out with a minimal map, same isolation boundary the full
    # build_context_render mock gave this test before the refactor (it swallowed the internal
    # scan entirely); only DAR's OWN targeted parse of the primary file should remain real+raising.
    monkeypatch.setattr(
        repo_map,
        "build_repo_map",
        lambda *args, **kwargs: {
            "path": str(paths["project"].resolve()),
            "files": [],
            "symbols": [],
            "imports": [],
        },
    )
    monkeypatch.setattr(
        repo_map,
        "build_context_render_from_map",
        lambda *args, **kwargs: _dar_context_payload(
            primary_file=paths["primary"].resolve(),
            file_summaries=[
                {
                    "path": str(paths["dependency"].resolve()),
                    "symbols": [{"name": _DEPENDENCY_SYMBOL, "kind": "function", "line": 1}],
                },
            ],
        ),
    )

    def _raise(*args: object, **kwargs: object) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", _raise)

    payload = agent_capsule.build_agent_capsule(_PRIMARY_SYMBOL, paths["project"])

    assert "outbound_dependencies" not in payload
    assert "outbound_dependency_evidence" not in payload


def test_dar_fail_safe_when_primary_snippet_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_dar_project(tmp_path)

    def _payload(*args: object, **kwargs: object) -> dict[str, Any]:
        payload = _dar_context_payload(primary_file=paths["primary"].resolve())
        payload["sources"] = []  # primary never lands in `snippets` at all
        return payload

    monkeypatch.setattr(repo_map, "build_context_render_from_map", _payload)

    payload = agent_capsule.build_agent_capsule(_PRIMARY_SYMBOL, paths["project"])

    assert "outbound_dependencies" not in payload
    assert "outbound_dependency_evidence" not in payload


# ---------------------------------------------------------------------------------------------
# (6) Kill-switch + dedupe vs. related_call_sites.
# ---------------------------------------------------------------------------------------------


def test_dar_kill_switch_suppresses_real_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _write_dar_project(tmp_path)
    monkeypatch.setattr(
        repo_map,
        "build_context_render_from_map",
        lambda *args, **kwargs: _dar_context_payload(
            primary_file=paths["primary"].resolve(),
            file_summaries=[
                {
                    "path": str(paths["dependency"].resolve()),
                    "symbols": [{"name": _DEPENDENCY_SYMBOL, "kind": "function", "line": 1}],
                },
            ],
        ),
    )

    for off_value in ("0", "false", "False", "no", "off", ""):
        monkeypatch.setenv("TG_CAPSULE_OUTBOUND_DEPS", off_value)
        payload = agent_capsule.build_agent_capsule(_PRIMARY_SYMBOL, paths["project"])
        assert "outbound_dependencies" not in payload, off_value
        assert "outbound_dependency_evidence" not in payload, off_value

    monkeypatch.setenv("TG_CAPSULE_OUTBOUND_DEPS", "1")
    payload = agent_capsule.build_agent_capsule(_PRIMARY_SYMBOL, paths["project"])
    assert payload["outbound_dependencies"]


def test_dar_dedupes_candidates_already_present_in_related_call_sites() -> None:
    payload: dict[str, Any] = {
        "file_summaries": [
            {
                "path": "/repo/dep.py",
                "symbols": [{"name": "helper", "kind": "function", "line": 3}],
            },
        ],
        "candidate_edit_targets": {"symbols": []},
    }
    target = {"file": "/repo/primary.py", "symbol": "primary_fn"}
    snippets = [
        {
            "file": "/repo/primary.py",
            "start_line": 1,
            "source": "def primary_fn():\n    return helper()\n",
        },
    ]
    related_call_sites = [{"file": "/repo/dep.py", "symbol": "helper"}]

    records, evidence = agent_capsule._collect_outbound_dependencies(
        "primary_fn",
        "/repo",
        target,
        payload,
        snippets,
        related_call_sites,
        max_files=3,
        preview_token_budget=None,
    )

    assert records == []
    assert evidence == {}
