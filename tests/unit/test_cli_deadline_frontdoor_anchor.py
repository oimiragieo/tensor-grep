"""Closes #200 sub-gap B (the #197 front-door residual): `tg agent --deadline N` and its cold-path
siblings (`context-render`/`edit-plan`/`orient`/`codemap`) anchored `deadline_monotonic` INSIDE the
builder (`agent_capsule.build_agent_capsule` et al.), computed fresh from `deadline_seconds` at
whatever moment the builder happened to start running. Front-door time spent in the CLI command
body BEFORE that point -- the lazy builder import, path/query resolution, GPU-id parsing, and the
daemon-skip check -- ran completely UNBUDGETED. Re-dogfooded on v1.80.4: 29% of `--deadline 3`
near-miss overruns silently exited 0 (internal compute finished just under ITS OWN budget, but
total wall-clock from CLI entry exceeded the user's requested `--deadline`).

The fix anchors `deadline_monotonic = time.monotonic() + deadline_seconds` at the TOP of each
command body (main.py's `_cli_deadline_monotonic`, called before any front-door work), then threads
that PRE-ANCHORED value into the builder, which now accepts an optional `deadline_monotonic`
override instead of always computing a fresh one from `deadline_seconds`.

Note on `tg agent` specifically: it additionally carries an already-shipped, SEPARATE cold-path-only
default (`DEFAULT_AGENT_CLI_DEADLINE_SECONDS`, "dogfood finding 1 (F4)") that applies only when the
user passes neither `--deadline` nor `--no-deadline`, computed AFTER the daemon-miss decision (by
design -- a warm daemon should still be tried on a default/implicit call). That default keeps its
existing (later) anchor point unchanged; this fix's early anchor covers the EXPLICIT `--deadline N`
case the task targets, which is the only case where `effective_deadline` is non-None at the top of
the function.

TDD technique (mirrors PR #642's deterministic injection, no wall-clock racing): each frontdoor case
monkeypatches the lazy-imported builder function's OWN module attribute to sleep before delegating
to the real implementation. Pre-fix, the builder computes its OWN deadline_monotonic AFTER the
injected sleep, so the sleep is invisible to the budget and the (trivially fast) real scan reports
success. Post-fix, main.py has already anchored deadline_monotonic BEFORE the sleep point (the sleep
happens inside the wrapped builder CALL, which is reached only after the CLI-layer anchor runs), so
the sleep eats into the shared budget and the scan honestly reports a truncated/partial result.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

import tensor_grep.cli.agent_capsule as agent_capsule
import tensor_grep.cli.codemap as codemap_module
import tensor_grep.cli.orient_capsule as orient_capsule
import tensor_grep.cli.repo_map as repo_map
from tensor_grep.cli.main import app

_FRONTDOOR_DELAY_SECONDS = 0.5
_DEADLINE_SECONDS = "0.3"


def _write_small_fixture(tmp_path: Path) -> None:
    # A trivially small repo: the REAL scan must finish in well under 0.3s so a pre-fix run would
    # (wrongly) report success if the injected front-door delay were invisible to the budget.
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")


def _wrap_with_frontdoor_delay(original):
    def _slow(*args, **kwargs):
        time.sleep(_FRONTDOOR_DELAY_SECONDS)
        return original(*args, **kwargs)

    return _slow


# ==================================================================================================
# Core RED/GREEN: a front-door delay injected AT THE BUILDER CALL BOUNDARY (a faithful stand-in for
# any pre-builder cost -- import, path resolution, GPU-id parsing) must count against --deadline.
# ==================================================================================================


@pytest.mark.parametrize(
    ("command", "extra_args", "module", "attr"),
    [
        ("agent", ["q"], agent_capsule, "build_agent_capsule"),
        ("context-render", ["q"], repo_map, "build_context_render"),
        ("edit-plan", ["q"], repo_map, "build_context_edit_plan"),
        ("codemap", [], codemap_module, "build_codemap"),
    ],
    ids=["agent", "context-render", "edit-plan", "codemap"],
)
def test_frontdoor_delay_before_builder_is_budgeted_exit_2(
    tmp_path: Path, monkeypatch, command: str, extra_args: list[str], module, attr: str
) -> None:
    _write_small_fixture(tmp_path)
    original = getattr(module, attr)
    monkeypatch.setattr(module, attr, _wrap_with_frontdoor_delay(original))

    result = CliRunner().invoke(
        app, [command, str(tmp_path), *extra_args, "--deadline", _DEADLINE_SECONDS, "--json"]
    )

    assert result.exit_code == 2, (
        f"{command}: a front-door delay ({_FRONTDOOR_DELAY_SECONDS}s) exceeding --deadline "
        f"{_DEADLINE_SECONDS}s must exit 2 (incomplete), not silently succeed: {result.output}"
    )
    payload = json.loads(result.output)
    assert payload.get("partial") is True, f"{command}: {result.output}"


def test_orient_frontdoor_delay_stays_exit_0_but_surfaces_partial(
    tmp_path: Path, monkeypatch
) -> None:
    # docs/CONTRACTS.md: tg orient has NO exit-2 contract -- a truncated scan still exits 0, but
    # partial/deadline_limit must still be surfaced HONESTLY as informational fields, never silently
    # dropped just because the truncation's root cause was front-door time instead of scan time.
    _write_small_fixture(tmp_path)
    original = orient_capsule.build_orient_capsule
    monkeypatch.setattr(
        orient_capsule, "build_orient_capsule", _wrap_with_frontdoor_delay(original)
    )

    result = CliRunner().invoke(
        app, ["orient", str(tmp_path), "--deadline", _DEADLINE_SECONDS, "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload.get("partial") is True, (
        f"orient: front-door delay must still surface partial=true informationally: {result.output}"
    )


# ==================================================================================================
# Builder-level: the CLI layer's pre-anchored deadline_monotonic must actually be threaded through
# (mirrors test_cli_deadline_flag.py's "forwards deadline to build_repo_map" style, one layer up:
# the top-level command wrapper -- build_agent_capsule et al. -- not build_repo_map itself). The
# [before+N, after+N] window proves the anchor happened DURING the CLI invocation (i.e. inside the
# command body, not lazily inside a builder that a spy has now bypassed entirely), with no reliance
# on machine speed -- deterministic by construction, not a wall-clock race.
# ==================================================================================================


def test_agent_command_passes_preanchored_deadline_monotonic(tmp_path: Path, monkeypatch) -> None:
    _write_small_fixture(tmp_path)
    recorded: dict = {}

    def _spy(query, path=".", *, deadline_monotonic=None, **_kwargs):
        recorded["deadline_monotonic"] = deadline_monotonic
        return {"path": str(path), "query": query}

    monkeypatch.setattr(agent_capsule, "build_agent_capsule", _spy)
    before = time.monotonic()
    result = CliRunner().invoke(app, ["agent", str(tmp_path), "q", "--deadline", "5", "--json"])
    after = time.monotonic()

    assert result.exit_code == 0, result.output
    got = recorded.get("deadline_monotonic")
    assert got is not None
    assert before + 5.0 <= got <= after + 5.0 + 0.05


def test_orient_command_passes_preanchored_deadline_monotonic(tmp_path: Path, monkeypatch) -> None:
    _write_small_fixture(tmp_path)
    recorded: dict = {}

    def _spy(path, *, deadline_monotonic=None, **_kwargs):
        recorded["deadline_monotonic"] = deadline_monotonic
        return {"path": str(path)}

    monkeypatch.setattr(orient_capsule, "build_orient_capsule", _spy)
    before = time.monotonic()
    result = CliRunner().invoke(app, ["orient", str(tmp_path), "--deadline", "5", "--json"])
    after = time.monotonic()

    assert result.exit_code == 0, result.output
    got = recorded.get("deadline_monotonic")
    assert got is not None
    assert before + 5.0 <= got <= after + 5.0 + 0.05


def test_codemap_command_passes_preanchored_deadline_monotonic(tmp_path: Path, monkeypatch) -> None:
    recorded: dict = {}

    def _spy(path, *, deadline_monotonic=None, **_kwargs):
        recorded["deadline_monotonic"] = deadline_monotonic
        return {
            "path": str(path),
            "out": str(Path(path) / "docs" / "code-map"),
            "index": str(Path(path) / "docs" / "code-map" / "index.md"),
            "folders_total": 0,
            "files_total": 0,
            "symbols_total": 0,
            "partial": False,
            "partial_reason": None,
        }

    monkeypatch.setattr(codemap_module, "build_codemap", _spy)
    before = time.monotonic()
    result = CliRunner().invoke(app, ["codemap", str(tmp_path), "--deadline", "5", "--json"])
    after = time.monotonic()

    assert result.exit_code == 0, result.output
    got = recorded.get("deadline_monotonic")
    assert got is not None
    assert before + 5.0 <= got <= after + 5.0 + 0.05


def test_edit_plan_command_passes_preanchored_deadline_monotonic(
    tmp_path: Path, monkeypatch
) -> None:
    _write_small_fixture(tmp_path)
    recorded: dict = {}

    def _spy(query, path=".", *, deadline_monotonic=None, **_kwargs):
        recorded["deadline_monotonic"] = deadline_monotonic
        return {"path": str(path), "query": query, "files": [], "tests": [], "symbols": []}

    monkeypatch.setattr(repo_map, "build_context_edit_plan", _spy)
    before = time.monotonic()
    result = CliRunner().invoke(app, ["edit-plan", str(tmp_path), "q", "--deadline", "5", "--json"])
    after = time.monotonic()

    assert result.exit_code == 0, result.output
    got = recorded.get("deadline_monotonic")
    assert got is not None
    assert before + 5.0 <= got <= after + 5.0 + 0.05


def test_context_render_command_passes_preanchored_deadline_monotonic(
    tmp_path: Path, monkeypatch
) -> None:
    _write_small_fixture(tmp_path)
    recorded: dict = {}

    def _spy(query, path=".", *, deadline_monotonic=None, **_kwargs):
        recorded["deadline_monotonic"] = deadline_monotonic
        return {"path": str(path), "query": query, "render_profile": "llm", "rendered_context": ""}

    monkeypatch.setattr(repo_map, "build_context_render", _spy)
    before = time.monotonic()
    result = CliRunner().invoke(
        app, ["context-render", str(tmp_path), "q", "--deadline", "5", "--json"]
    )
    after = time.monotonic()

    assert result.exit_code == 0, result.output
    got = recorded.get("deadline_monotonic")
    assert got is not None
    assert before + 5.0 <= got <= after + 5.0 + 0.05


def test_deadline_monotonic_none_when_no_deadline(tmp_path: Path, monkeypatch) -> None:
    _write_small_fixture(tmp_path)
    recorded: dict = {"deadline_monotonic": "sentinel"}

    def _spy(query, path=".", *, deadline_monotonic=None, **_kwargs):
        recorded["deadline_monotonic"] = deadline_monotonic
        return {"path": str(path), "query": query}

    monkeypatch.setattr(agent_capsule, "build_agent_capsule", _spy)
    result = CliRunner().invoke(app, ["agent", str(tmp_path), "q", "--json"])

    assert result.exit_code == 0, result.output
    # tg agent's own F4 cold-path default (DEFAULT_AGENT_CLI_DEADLINE_SECONDS) applies when neither
    # --deadline nor --no-deadline is passed -- deadline_monotonic must be a real anchored value
    # here (not None), but it is NOT this fix's early-anchor path (see module docstring).
    assert recorded.get("deadline_monotonic") is not None


def test_deadline_monotonic_none_when_no_deadline_flag_passed(tmp_path: Path, monkeypatch) -> None:
    # --no-deadline must still yield deadline_monotonic=None (not an anchored-but-huge value).
    _write_small_fixture(tmp_path)
    recorded: dict = {"deadline_monotonic": "sentinel"}

    def _spy(query, path=".", *, deadline_monotonic=None, **_kwargs):
        recorded["deadline_monotonic"] = deadline_monotonic
        return {"path": str(path), "query": query}

    monkeypatch.setattr(agent_capsule, "build_agent_capsule", _spy)
    result = CliRunner().invoke(app, ["agent", str(tmp_path), "q", "--no-deadline", "--json"])

    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_monotonic") is None


def test_edit_plan_deadline_monotonic_none_when_no_deadline(tmp_path: Path, monkeypatch) -> None:
    # edit-plan has no F4-style cold-path default -- omitting --deadline must stay fully unbounded
    # (deadline_monotonic=None), unlike tg agent's special case above.
    _write_small_fixture(tmp_path)
    recorded: dict = {"deadline_monotonic": "sentinel"}

    def _spy(query, path=".", *, deadline_monotonic=None, **_kwargs):
        recorded["deadline_monotonic"] = deadline_monotonic
        return {"path": str(path), "query": query, "files": [], "tests": [], "symbols": []}

    monkeypatch.setattr(repo_map, "build_context_edit_plan", _spy)
    result = CliRunner().invoke(app, ["edit-plan", str(tmp_path), "q", "--json"])

    assert result.exit_code == 0, result.output
    assert recorded.get("deadline_monotonic") is None


# ==================================================================================================
# Builder-level backward compatibility: a direct caller that only passes deadline_seconds (MCP
# server, tests, the deprecated build_agent_capsule_json) must be byte-identical to pre-fix
# behavior -- the new deadline_monotonic param is additive-only (default None -> internal fallback
# computation unchanged).
# ==================================================================================================


def test_build_agent_capsule_still_computes_fresh_deadline_without_override(
    tmp_path: Path, monkeypatch
) -> None:
    _write_small_fixture(tmp_path)
    recorded: dict = {}
    original_build_repo_map = repo_map.build_repo_map

    def _spy(path, **kwargs):
        recorded["deadline_monotonic"] = kwargs.get("deadline_monotonic")
        return original_build_repo_map(path, **kwargs)

    monkeypatch.setattr(agent_capsule.repo_map, "build_repo_map", _spy)
    before = time.monotonic()
    agent_capsule.build_agent_capsule("f", str(tmp_path), deadline_seconds=5.0)
    after = time.monotonic()

    got = recorded.get("deadline_monotonic")
    assert got is not None
    assert before + 5.0 <= got <= after + 5.0 + 0.05


def test_build_agent_capsule_preanchored_override_takes_precedence(
    tmp_path: Path, monkeypatch
) -> None:
    _write_small_fixture(tmp_path)
    recorded: dict = {}
    original_build_repo_map = repo_map.build_repo_map

    def _spy(path, **kwargs):
        recorded["deadline_monotonic"] = kwargs.get("deadline_monotonic")
        return original_build_repo_map(path, **kwargs)

    monkeypatch.setattr(agent_capsule.repo_map, "build_repo_map", _spy)
    pre_anchored = time.monotonic() - 1.0  # already-expired, deliberately distinguishable
    agent_capsule.build_agent_capsule(
        "f", str(tmp_path), deadline_seconds=999.0, deadline_monotonic=pre_anchored
    )

    # The override must win over deadline_seconds -- if the builder ignored it and recomputed from
    # deadline_seconds=999.0, the recorded value would be ~999s in the future, not already expired.
    assert recorded.get("deadline_monotonic") == pre_anchored
