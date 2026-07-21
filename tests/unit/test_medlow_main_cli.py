"""Regression tests for confirmed MED/LOW dogfood bugs in cli/main.py.

Covered fixes:
- M7  session open no longer emits the "capped" warning when the repo map is not
      truncated (possibly_truncated == False).
- M8  session show --json exposes file_count / symbol_count (parity with
      session open --json and session list --json).
- M9  session show auto-corrects reversed `<PATH> <SESSION_ID>` args with a hint,
      matching session context.
- M10 doctor surfaces an honest gpu.search_ready boolean, and downgrades an
      over-claimed lsp_proof when the provider stderr names a workspace/fetch
      error (adding a workspace_warning and un-suppressing the stderr tail).
- M15 an unbounded scan of a folder-of-projects workspace root is refused.
- L10 calibrate exits 1 (runtime/unsupported), not 2, when it cannot run.

The tests use CliRunner / direct function calls so they stay import-light and do
not require the Rust core, a GPU, or a live language server.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import (
    _doctor_apply_lsp_missing_component_remediation,
    _doctor_apply_lsp_rust_analyzer_remediation,
    _doctor_apply_lsp_workspace_warnings,
    _doctor_downgrade_lsp_workspace_proof,
    _doctor_rust_analyzer_missing_component_remediation,
    _should_refuse_unbounded_workspace_root_scan,
    app,
)


def _git_init(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)


def _open_session(runner: CliRunner, project: Path) -> str:
    result = runner.invoke(app, ["session", "open", str(project), "--json"])
    assert result.exit_code == 0, result.stdout
    return str(json.loads(result.stdout)["session_id"])


# ---------------------------------------------------------------------------
# M7 - no spurious "capped" warning on a tiny, non-truncated session repo map
# ---------------------------------------------------------------------------


def test_m7_session_open_no_capped_warning_when_not_truncated(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def a() -> None:\n    pass\n", encoding="utf-8")
    (project / "b.py").write_text("def b() -> None:\n    pass\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["session", "open", str(project)])
    assert result.exit_code == 0, result.stdout
    assert "capped" not in result.stdout.lower()

    # The scan_limit is still recorded, but possibly_truncated must be False here.
    json_result = runner.invoke(app, ["session", "open", str(project), "--json"])
    payload = json.loads(json_result.stdout)
    assert payload["scan_limit"]["possibly_truncated"] is False


def test_m7_session_open_warns_only_when_truncated(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    for index in range(6):
        (project / f"mod{index}.py").write_text(f"def f{index}() -> None:\n    pass\n", "utf-8")

    runner = CliRunner()
    # max_repo_files=1 forces possibly_truncated=True.
    result = runner.invoke(app, ["session", "open", str(project), "--max-repo-files", "1"])
    assert result.exit_code == 0, result.stdout
    assert "capped" in result.stdout.lower()
    # The remediation must point at a flag the command actually accepts.
    assert "--max-repo-files" in result.stdout
    # session refresh does not accept --max-repo-files, so the old "refresh
    # without --max-repo-files" wording must be gone.
    assert "refresh without --max-repo-files" not in result.stdout


# ---------------------------------------------------------------------------
# M8 - session show --json parity for file_count / symbol_count
# ---------------------------------------------------------------------------


def test_m8_session_show_json_exposes_counts(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (src / "payments.py").write_text("def invoice() -> int:\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    open_result = runner.invoke(app, ["session", "open", str(project), "--json"])
    opened = json.loads(open_result.stdout)
    session_id = opened["session_id"]

    show_result = runner.invoke(app, ["session", "show", session_id, str(project), "--json"])
    assert show_result.exit_code == 0, show_result.stdout
    shown = json.loads(show_result.stdout)

    assert "file_count" in shown
    assert "symbol_count" in shown
    assert shown["file_count"] == opened["file_count"]
    assert shown["symbol_count"] == opened["symbol_count"]


# ---------------------------------------------------------------------------
# M9 - session show reversed-arg auto-correction + hint
# ---------------------------------------------------------------------------


def test_m9_session_show_autocorrects_reversed_args(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("def a() -> None:\n    pass\n", encoding="utf-8")

    runner = CliRunner()
    session_id = _open_session(runner, project)

    # Reversed order: <PATH> <SESSION_ID>. Must auto-correct (exit 0) and emit a hint.
    reversed_result = runner.invoke(app, ["session", "show", str(project), session_id])
    assert reversed_result.exit_code == 0, reversed_result.stdout
    assert "Session not found" not in (reversed_result.stderr + reversed_result.stdout)
    assert "interpreting as" in reversed_result.stderr
    assert session_id in reversed_result.stdout


# ---------------------------------------------------------------------------
# M10 - doctor honesty (gpu.search_ready + workspace-blind lsp_proof)
# ---------------------------------------------------------------------------


def test_m10_doctor_gpu_search_ready_present(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    gpu = payload["gpu"]
    assert "search_ready" in gpu
    assert isinstance(gpu["search_ready"], bool)
    # search_ready must agree with the runtime probe, never just gpu.available.
    probe_status = gpu.get("search_runtime_probe", {}).get("status")
    assert gpu["search_ready"] == (probe_status == "supported")


def test_m10_lsp_proof_downgraded_on_workspace_error() -> None:
    over_claiming = {
        "language": "rust",
        "available": True,
        "health_status": "ready",
        "lsp_proof": True,
        "provider_recent_stderr": [
            "[ERROR rust_analyzer] FetchWorkspaceError: failed to load Cargo.toml",
        ],
        "stderr_tail": [],
        "stderr_tail_suppressed": True,
    }
    out = _doctor_downgrade_lsp_workspace_proof(over_claiming)
    assert out["lsp_proof"] is False
    assert out["workspace_warning"]
    assert out["not_lsp_proof_reason"]
    # The suppressed evidence is restored and no longer hidden.
    assert out["stderr_tail_suppressed"] is False
    assert any("FetchWorkspaceError" in line for line in out["stderr_tail"])
    # Original dict is not mutated.
    assert over_claiming["lsp_proof"] is True


def test_m10_lsp_proof_preserved_without_workspace_error() -> None:
    clean = {
        "language": "python",
        "available": True,
        "health_status": "ready",
        "lsp_proof": True,
        "provider_recent_stderr": None,
        "stderr_tail": [],
    }
    proof_false = {
        "language": "go",
        "lsp_proof": False,
        "provider_recent_stderr": ["FetchWorkspaceError: nope"],
    }
    result = _doctor_apply_lsp_workspace_warnings([clean, proof_false])
    assert result[0]["lsp_proof"] is True
    assert "workspace_warning" not in result[0]
    # A provider that was never lsp_proof stays untouched.
    assert result[1]["lsp_proof"] is False
    assert "workspace_warning" not in result[1]


# ---------------------------------------------------------------------------
# Actionable rust-analyzer error - name the exact rustup component-add
# remediation when a rust provider's stderr matches rustup's "missing
# component" proxy fingerprint (real repro: rustup's rust-analyzer proxy
# binary spawns, then immediately exits with "unknown binary 'rust-analyzer'
# in toolchain '<toolchain>'" when the component was never installed for the
# active toolchain). Narrow by design: only language == rust and only this
# ONE fingerprint are touched -- every other language and every other error
# shape must pass through unchanged.
# ---------------------------------------------------------------------------


def test_rust_analyzer_missing_component_remediation_names_the_toolchain() -> None:
    stderr_lines = [
        "stdout: some other provider noise",
        "error: unknown binary 'rust-analyzer' in toolchain '1.96.0-x86_64-pc-windows-msvc'",
    ]
    remediation = _doctor_rust_analyzer_missing_component_remediation(stderr_lines)
    assert remediation is not None
    assert (
        "rustup component add rust-analyzer --toolchain 1.96.0-x86_64-pc-windows-msvc"
        in remediation
    )
    assert "tg lsp-setup --include-toolchain-providers" in remediation


def test_rust_analyzer_missing_component_remediation_falls_back_without_toolchain() -> None:
    # The fingerprint markers are present but no quoted toolchain string follows -- must fall
    # back to the plain command instead of emitting a broken `--toolchain` with no value.
    stderr_lines = ["error: unknown binary 'rust-analyzer' -- no toolchain reported"]
    remediation = _doctor_rust_analyzer_missing_component_remediation(stderr_lines)
    assert remediation is not None
    assert "rustup component add rust-analyzer" in remediation
    assert "--toolchain" not in remediation


def test_rust_analyzer_missing_component_remediation_absent_when_fingerprint_missing() -> None:
    stderr_lines = ["some unrelated crash", "thread 'main' panicked at src/main.rs:10"]
    assert _doctor_rust_analyzer_missing_component_remediation(stderr_lines) is None
    assert _doctor_rust_analyzer_missing_component_remediation([]) is None


def test_doctor_appends_rust_analyzer_remediation_to_not_lsp_proof_reason() -> None:
    unhealthy = {
        "language": "rust",
        "available": True,
        "health_status": "unhealthy",
        "lsp_proof": False,
        "not_lsp_proof_reason": "Provider semantic health probe failed or timed out.",
        "stderr_tail": [
            "error: unknown binary 'rust-analyzer' in toolchain '1.96.0-x86_64-pc-windows-msvc'",
        ],
    }
    out = _doctor_apply_lsp_rust_analyzer_remediation(unhealthy)
    assert (
        "rustup component add rust-analyzer --toolchain 1.96.0-x86_64-pc-windows-msvc"
        in out["not_lsp_proof_reason"]
    )
    # The original generic reason text is preserved, not replaced.
    assert "Provider semantic health probe failed or timed out." in out["not_lsp_proof_reason"]
    # Original dict is not mutated.
    assert (
        unhealthy["not_lsp_proof_reason"] == "Provider semantic health probe failed or timed out."
    )


def test_doctor_rust_analyzer_remediation_narrow_to_rust_language() -> None:
    non_rust = {
        "language": "python",
        "health_status": "unhealthy",
        "not_lsp_proof_reason": "Provider semantic health probe failed or timed out.",
        "stderr_tail": [
            "error: unknown binary 'rust-analyzer' in toolchain '1.96.0-x86_64-pc-windows-msvc'",
        ],
    }
    out = _doctor_apply_lsp_rust_analyzer_remediation(non_rust)
    assert out is non_rust
    assert out["not_lsp_proof_reason"] == "Provider semantic health probe failed or timed out."


def test_doctor_rust_analyzer_remediation_narrow_to_the_exact_fingerprint() -> None:
    generic_rust_error = {
        "language": "rust",
        "health_status": "unhealthy",
        "not_lsp_proof_reason": "Provider semantic health probe failed or timed out.",
        "stderr_tail": ["thread 'main' panicked at src/main.rs:10: index out of bounds"],
    }
    out = _doctor_apply_lsp_rust_analyzer_remediation(generic_rust_error)
    assert out["not_lsp_proof_reason"] == "Provider semantic health probe failed or timed out."


def test_doctor_apply_lsp_missing_component_remediation_over_provider_list() -> None:
    providers = [
        {
            "language": "rust",
            "health_status": "unhealthy",
            "not_lsp_proof_reason": "Provider semantic health probe failed or timed out.",
            "stderr_tail": [
                "error: unknown binary 'rust-analyzer' in toolchain '1.96.0-x86_64-pc-windows-msvc'",
            ],
        },
        {
            "language": "python",
            "health_status": "unhealthy",
            "not_lsp_proof_reason": "Provider semantic health probe failed or timed out.",
            "stderr_tail": [],
        },
    ]
    result = _doctor_apply_lsp_missing_component_remediation(providers)
    assert "rustup component add rust-analyzer" in result[0]["not_lsp_proof_reason"]
    assert (
        result[1]["not_lsp_proof_reason"] == "Provider semantic health probe failed or timed out."
    )


# ---------------------------------------------------------------------------
# M15 - refuse an unbounded scan of a folder-of-projects workspace root
# ---------------------------------------------------------------------------


def test_m15_files_scan_refuses_folder_of_projects(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("proj1", "proj2", "proj3"):
        child = workspace / name
        child.mkdir()
        _git_init(child)
        (child / "file.txt").write_text("x\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--files", str(workspace)])
    assert result.exit_code == 2, result.stdout
    assert "multi-project" in result.stderr
    assert "proj1" in result.stderr


def test_m15_single_project_still_scans(tmp_path: Path) -> None:
    project = tmp_path / "solo"
    project.mkdir()
    _git_init(project)
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["search", "--files", str(project)])
    assert result.exit_code == 0, result.stderr
    assert "multi-project" not in result.stderr


def test_m15_detector_keys_on_child_project_markers(tmp_path: Path) -> None:
    from tensor_grep.core.config import SearchConfig

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a").mkdir()
    (workspace / "a" / "pyproject.toml").write_text("[tool]\n", encoding="utf-8")
    (workspace / "b").mkdir()
    (workspace / "b" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (workspace / "c").mkdir()
    (workspace / "c" / "go.mod").write_text("module x\n", encoding="utf-8")

    refuse, dirs = _should_refuse_unbounded_workspace_root_scan(
        [str(workspace)],
        SearchConfig(),
        allow_broad_generated_scan=False,
        paths_defaulted=False,
    )
    assert refuse is True
    assert dirs == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# L10 - calibrate exits 1, not 2, when it cannot run
# ---------------------------------------------------------------------------


def test_l10_calibrate_exits_one_when_unsupported(monkeypatch) -> None:
    # Hermetic: force the no-native-binary branch so this asserts the exit-1 "unsupported"
    # convention DETERMINISTICALLY, regardless of whether a native tg binary happens to be
    # present in the runner env. A present binary runs the native `calibrate`, which exits 2
    # on a no-CUDA box -- an env-dependent FALSE failure of this test's intent (this test
    # false-failed on all CI platforms when a native binary was present; banked lesson: a test
    # must not depend on operator-real / env-resolved binary presence).
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    runner = CliRunner()
    result = runner.invoke(app, ["calibrate"])
    # On a box without the native binary / CUDA, calibrate is a runtime/unsupported
    # error: tg's convention is exit 1, not the usage-error exit 2.
    assert result.exit_code == 1, result.stdout
    # P0-4 (GPU Phase-0 honesty): the missing-binary message must carry an actionable
    # remediation pointer -- not just "not found" with no next step.
    assert "tg upgrade" in result.output
    assert "tg doctor" in result.output
    # CEO dogfood follow-up (v1.76.6): the message must state the honest, evergreen fact
    # (GPU experimental / needs a CUDA-enabled build) instead of inviting
    # TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR=nvidia + `tg upgrade` as an obtainable GPU path --
    # no NVIDIA-enabled asset has ever shipped, so that framing was a permanent dead end.
    assert "experimental" in result.output
    assert "if one is published for this platform on the release page" not in result.output
    assert "NVIDIA-enabled native binary" not in result.output
    # #182 NIT-1: the residual FLAVOR name-drop is dropped so this Python wrapper matches the
    # Rust side (crossover.rs detect_device_name test), which forbids the override entirely.
    # A "confirm before relying on TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR=nvidia" aside dangled
    # an override that no shipped asset honors -- the same permanent dead end, asymmetric.
    assert "TENSOR_GREP_NATIVE_FRONTDOOR_FLAVOR" not in result.output


# ---------------------------------------------------------------------------
# v20 dogfood follow-up - calibrate --json emits an additive structured skip signal
# ---------------------------------------------------------------------------


def test_calibrate_json_flag_missing_binary_emits_skip_signal(monkeypatch) -> None:
    # Hermetic for the same reason as L10 above: force the no-native-binary branch instead of
    # depending on whether a native tg binary happens to be on this runner.
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    runner = CliRunner()
    result = runner.invoke(app, ["calibrate", "--json"])

    # --json is additive: the exit-1 "unsupported" convention (L10) is unchanged.
    assert result.exit_code == 1, result.stdout
    # A structured stdout line lets a harness classify this deterministically instead of
    # parsing the human-readable remediation text.
    assert '"calibration_status": "native_binary_unavailable"' in result.output
    # The human-readable remediation stays present too (additive, not replaced).
    assert "tg upgrade" in result.output

    # #678 gate nit: a caller doing `json.loads(subprocess_stdout)` on the real, separate stdout
    # stream must see ONLY the structured line -- the human remediation text must land on
    # stderr, never stdout, or a whole-stdout json.loads() chokes. `result.output` above is a
    # THIRD, merged view (Click 8.2+, pinned 8.4.2 in uv.lock, keeps `.stdout`/`.stderr` as
    # genuinely separate captured streams) that cannot distinguish which real stream the text
    # landed on -- pin the stream-separation contract directly instead of only the merged one.
    # (Verified against the real code before writing this: `typer.echo(..., err=True)` in
    # calibrate()'s missing-binary branch, main.py, has routed this text to stderr since commit
    # a4b3c05c (2026-07-14), predating #678 by six days -- this assertion makes that contract
    # regression-proof instead of merely incidentally true.)
    assert json.loads(result.stdout) == {"calibration_status": "native_binary_unavailable"}
    assert "tg upgrade" not in result.stdout
    assert "tg upgrade" in result.stderr
