"""A scan-policy REFUSAL must be machine-readable on the `--json` surface.

External dogfood (gotcontext-saddle, v1.101.7 and again on v1.101.9) asked for "the same exit-2
refuse for bare `--json` unscoped as the multi-project parent". The exit code was already 2 -- what
was missing is that `--json` printed **zero bytes** to stdout, so a consumer got a
``JSONDecodeError`` and had to parse English off stderr to learn why. Measured on the shipped
v1.101.9::

    tg search PAT --json          # large implicit root
    -> exit 2, stdout 0 bytes, refusal prose on stderr only

A refusal is the one answer a `--json` caller most needs in-band: it is exactly the case where an
empty result must NOT be read as "no matches found". Emitting nothing forces the consumer into the
inference this entire surface exists to prevent.

The envelope MIRRORS MCP's `tg_search` refusal payload field for field rather than inventing a
second shape -- MCP already settled that a scan-policy ceiling classifies as ``scan_limit`` and that
``error.code`` is the sibling signal for WHICH policy refused. Two surfaces refusing the same thing
in two different shapes is how CLI and MCP drift into contradicting each other (#293's lesson).
"""

from __future__ import annotations

import json

import pytest

# NOT a module-level `from tensor_grep.cli.main import ...`. Importing `main` at collection time
# perturbs the lazy-import state four `--help` tests in `test_cli_modes.py` depend on: they passed
# with that file alone and failed whenever this one was collected first, on snippets that simply
# were not in the rendered help. The import is deferred into the helper so collection order stops
# being load-bearing. (A first attempt blamed `redirect_stdout` width-fragility and switching to
# `capsys` did NOT fix it -- the capsys change is kept anyway, since capturing without swapping
# the stream is right regardless, but the module-level import was the actual cause.)

_MESSAGE = (
    "Error: broad root scan refused as a safety guard, not a zero-match result: "
    "path is a large single-project root (over 1500 files)"
)


def _emit(capsys: pytest.CaptureFixture[str], *, json_output: bool) -> tuple[str, str]:
    """Capture via pytest's `capsys`, NOT `contextlib.redirect_stdout`.

    The first cut used `redirect_stdout(io.StringIO())` and POISONED four unrelated `--help`
    tests in `test_cli_modes.py` -- they passed when that file ran alone and failed when this one
    ran first. Click sizes its help wrapping from the stream it finds at render time, so swapping
    stdout for a StringIO changes the detected width and re-wraps help text for the rest of the
    session. Same width-fragility class as the clap help-parse trap, one framework over.

    `capsys` is the fixture pytest provides precisely to capture without disturbing that.
    """
    from tensor_grep.cli.main import _emit_broad_scan_refusal

    _emit_broad_scan_refusal(_MESSAGE, json_output=json_output, path=".")
    captured = capsys.readouterr()
    return captured.out, captured.err


def test_json_refusal_is_a_parseable_document(capsys: pytest.CaptureFixture[str]) -> None:
    # THE DEFECT: stdout was empty, so this line raised JSONDecodeError.
    out, _ = _emit(capsys, json_output=True)
    assert out.strip(), "stdout is empty; a --json consumer gets JSONDecodeError, not a refusal"
    json.loads(out)


def test_the_zero_travels_with_its_qualifiers(capsys: pytest.CaptureFixture[str]) -> None:
    # `total_matches: 0` is only safe because the incompleteness flags appear beside it. A bare
    # zero would be an ABSENCE CLAIM over a scan that never ran -- the exact defect this campaign
    # exists to close. Assert they cannot be separated.
    payload = json.loads(_emit(capsys, json_output=True)[0])
    assert payload["total_matches"] == 0
    assert payload["result_incomplete"] is True
    assert payload["truncated"] is True
    assert payload["incomplete_reason_class"] == "scan_limit"


def test_the_envelope_names_which_policy_refused_and_that_a_retry_is_futile(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = json.loads(_emit(capsys, json_output=True)[0])
    assert payload["error"]["code"] == "broad_scan_refused"
    assert payload["error"]["retryable"] is False
    assert "broad root scan refused" in payload["incomplete_reason"]


def test_the_vocabulary_matches_the_mcp_refusal_payload(capsys: pytest.CaptureFixture[str]) -> None:
    # Cross-surface consistency, pinned. MCP's tg_search refusal carries exactly these keys; if
    # either side grows a field the other lacks, an agent that learned one surface mis-reads the
    # other. Kept as a subset check on the CLI side because MCP's payload additionally carries
    # per-tool keys (lang, rendered_* counts) that have no CLI meaning.
    payload = json.loads(_emit(capsys, json_output=True)[0])
    for shared in (
        "total_matches",
        "total_files",
        "matches",
        "truncated",
        "result_incomplete",
        "incomplete_reason",
        "incomplete_reason_class",
        "error",
    ):
        assert shared in payload, f"CLI refusal envelope is missing MCP's {shared!r}"


def test_human_prose_still_goes_to_stderr_in_both_modes(capsys: pytest.CaptureFixture[str]) -> None:
    # The banner is ADDITIVE. A human running without --json must see exactly what they saw before.
    _, err_json = _emit(capsys, json_output=True)
    _, err_text = _emit(capsys, json_output=False)
    assert "broad root scan refused" in err_json
    assert "broad root scan refused" in err_text
    assert err_json == err_text, "stderr must not differ between the two modes"


def test_text_mode_stdout_is_byte_identical_to_before(capsys: pytest.CaptureFixture[str]) -> None:
    # CONTROL ARM: without it, an emitter that always printed the envelope would satisfy every
    # assertion above while polluting the plain-text surface.
    out_text, _ = _emit(capsys, json_output=False)
    assert out_text == "", "text mode must print nothing to stdout"


def test_emitter_accepts_override_class_and_error_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A workspace-root refusal must NOT share ``scan_limit`` with a file-cap truncation.

    External dogfood (v1.108.2) filed this as a NEW real ask: agents retrying a workspace-parent
    refuse with a bigger ``--max-repo-files`` are following the wrong knob because the class lied.
    Defaults stay ``scan_limit`` / ``broad_scan_refused`` for the other policy ceilings; the
    workspace call site opts into ``workspace_root_refused`` on both fields.
    """
    from tensor_grep.cli.main import _emit_broad_scan_refusal

    _emit_broad_scan_refusal(
        "Error: broad workspace-root scan refused as a safety guard",
        json_output=True,
        path="/ws",
        incomplete_reason_class="workspace_root_refused",
        error_code="workspace_root_refused",
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["incomplete_reason_class"] == "workspace_root_refused"
    assert payload["error"]["code"] == "workspace_root_refused"


def test_workspace_root_json_refuse_emits_workspace_root_refused_not_scan_limit(
    tmp_path,
) -> None:
    """End-to-end: the workspace call site must wire the distinct class, not rely on defaults."""
    from pathlib import Path

    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    workspace = Path(tmp_path) / "projects"
    workspace.mkdir()
    for project_name, marker_name in (
        ("alpha", "pyproject.toml"),
        ("beta", "package.json"),
        ("gamma", "Cargo.toml"),
    ):
        project = workspace / project_name
        (project / "src").mkdir(parents=True)
        (project / marker_name).write_text("", encoding="utf-8")
        (project / "src" / "app.py").write_text("needle\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["search", "needle", str(workspace), "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["incomplete_reason_class"] == "workspace_root_refused", (
        "workspace-root refuse must not masquerade as scan_limit "
        f"(got {payload.get('incomplete_reason_class')!r})"
    )
    assert payload["error"]["code"] == "workspace_root_refused"
    # CONTROL: a non-workspace refuse path still defaults to scan_limit — covered by
    # test_the_zero_travels_with_its_qualifiers above on the bare emitter.
