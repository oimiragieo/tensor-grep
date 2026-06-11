"""Regression tests for M3/M4 dogfood fixes in ast_workflows.run_command.

M3: All tg run --json payload shapes must carry consistent version, schema_version,
    mode, and total_matches keys.
M4: Batch-rewrite format is documented in run_command; the cryptic "$" error is
    confirmed to come from Rust (flagged in the cross-file section below).
"""

from __future__ import annotations

import json
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture_run_command(
    *args: Any,
    fake_plan: dict[str, Any] | None = None,
    fake_apply: dict[str, Any] | None = None,
    fake_plan_exit: int = 0,
    fake_apply_exit: int = 0,
    **kwargs: Any,
) -> tuple[str, int]:
    """Call run_command with optional monkeypatched rewrite functions, return (stdout, retcode)."""
    import sys

    import tensor_grep.cli.ast_workflows as aw

    def _plan(**kw: Any) -> tuple[str, int]:
        payload = fake_plan or {
            "version": 1,
            "routing_backend": "AstBackend",
            "routing_reason": "ast-native",
            "sidecar_used": False,
            "total_edits": 0,
            "edits": [],
        }
        return json.dumps(payload), fake_plan_exit

    def _apply(**kw: Any) -> tuple[str, int]:
        payload = fake_apply or {
            "version": 1,
            "routing_backend": "AstBackend",
            "routing_reason": "ast-native",
            "sidecar_used": False,
            "applied_edits": 0,
        }
        return json.dumps(payload), fake_apply_exit

    old_stdout = sys.stdout
    captured = StringIO()
    sys.stdout = captured
    try:
        with (
            patch.object(aw, "execute_rewrite_plan_json", side_effect=_plan),
            patch.object(aw, "execute_rewrite_apply_json", side_effect=_apply),
        ):
            ret = aw.run_command(*args, **kwargs)
    finally:
        sys.stdout = old_stdout
    return captured.getvalue().strip(), ret


# ---------------------------------------------------------------------------
# M3: consistent JSON envelope
# ---------------------------------------------------------------------------


class TestRunJsonModeSearch:
    """M3 - search mode must carry version, schema_version, mode='search', total_matches."""

    def test_stdin_mode_has_required_envelope_keys(self) -> None:
        """Search via --stdin must include all four mandatory envelope keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "hello.py"
            src.write_text("x = 1\n", encoding="utf-8")

            import sys

            import tensor_grep.cli.ast_workflows as aw

            old_stdin = sys.stdin
            old_stdout = sys.stdout
            captured = StringIO()
            sys.stdout = captured
            try:
                from io import StringIO as _SIO

                sys.stdin = _SIO("x = 1\n")
                aw.run_command("x", json_mode=True, stdin=True, lang="python")
            finally:
                sys.stdin = old_stdin
                sys.stdout = old_stdout

            output = captured.getvalue().strip()
            assert output, "Expected non-empty stdout"
            payload = json.loads(output)

            assert payload.get("version") == 1, f"version missing: {payload}"
            assert payload.get("schema_version") == 1, f"schema_version missing: {payload}"
            assert payload.get("mode") == "stdin", f"mode should be 'stdin': {payload}"
            assert "total_matches" in payload, f"total_matches missing: {payload}"

    def test_search_mode_flag_is_search_not_stdin(self) -> None:
        """When stdin=False, mode must be 'search'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "hello.py"
            src.write_text("x = 1\n", encoding="utf-8")

            import sys

            import tensor_grep.cli.ast_workflows as aw

            old_stdout = sys.stdout
            captured = StringIO()
            sys.stdout = captured
            try:
                aw.run_command("x", str(tmpdir), json_mode=True)
            finally:
                sys.stdout = old_stdout

            output = captured.getvalue().strip()
            assert output, "Expected non-empty stdout"
            payload = json.loads(output)

            assert payload.get("mode") == "search", f"mode should be 'search': {payload}"
            assert "total_matches" in payload, f"total_matches missing: {payload}"
            assert payload.get("schema_version") == 1, f"schema_version missing: {payload}"
            assert payload.get("version") == 1, f"version missing: {payload}"


class TestRunJsonModeRewritePlan:
    """M3 - rewrite-plan mode must carry the four mandatory envelope keys."""

    def test_rewrite_plan_injects_mode_and_schema_version(self) -> None:
        """execute_rewrite_plan_json output must be enriched with mode/schema_version."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Provide a minimal plan payload that lacks mode + total_matches
            minimal_plan: dict[str, Any] = {
                "version": 1,
                "routing_backend": "AstBackend",
                "routing_reason": "ast-native",
                "sidecar_used": False,
                "total_edits": 0,
                "edits": [],
            }
            stdout, _ret = _capture_run_command(
                "x",
                tmpdir,
                rewrite="y",
                fake_plan=minimal_plan,
                fake_plan_exit=0,
            )
            assert stdout, "Expected non-empty stdout"
            payload = json.loads(stdout)

            assert payload.get("version") == 1, f"version missing: {payload}"
            assert payload.get("schema_version") == 1, f"schema_version missing: {payload}"
            assert payload.get("mode") == "rewrite-plan", (
                f"mode should be 'rewrite-plan': {payload}"
            )
            assert "total_matches" in payload, f"total_matches missing: {payload}"
            assert payload["total_matches"] == 0

    def test_rewrite_plan_preserves_existing_keys(self) -> None:
        """Injection must not overwrite existing keys already set by mcp_server."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_with_extras: dict[str, Any] = {
                "version": 1,
                "schema_version": 1,
                "routing_backend": "AstBackend",
                "routing_reason": "ast-native",
                "sidecar_used": False,
                "total_edits": 3,
                "edits": [],
            }
            stdout, _ret = _capture_run_command(
                "x",
                tmpdir,
                rewrite="y",
                fake_plan=plan_with_extras,
            )
            payload = json.loads(stdout)
            # Existing keys must be preserved
            assert payload["total_edits"] == 3
            assert payload["routing_backend"] == "AstBackend"
            # New keys must be injected
            assert payload.get("mode") == "rewrite-plan"
            assert "total_matches" in payload


class TestRunJsonModeApply:
    """M3 - apply mode must carry the four mandatory envelope keys."""

    def test_apply_mode_injects_mode_and_schema_version(self) -> None:
        """execute_rewrite_apply_json output must be enriched with mode/schema_version."""
        with tempfile.TemporaryDirectory() as tmpdir:
            minimal_apply: dict[str, Any] = {
                "version": 1,
                "routing_backend": "AstBackend",
                "routing_reason": "ast-native",
                "sidecar_used": False,
                "applied_edits": 0,
            }
            stdout, _ret = _capture_run_command(
                "x",
                tmpdir,
                rewrite="y",
                apply=True,
                fake_apply=minimal_apply,
                fake_apply_exit=0,
            )
            assert stdout, "Expected non-empty stdout"
            payload = json.loads(stdout)

            assert payload.get("version") == 1
            assert payload.get("schema_version") == 1
            assert payload.get("mode") == "apply", f"mode should be 'apply': {payload}"
            assert "total_matches" in payload
            assert payload["total_matches"] == 0


class TestInjectRunJsonFields:
    """Unit tests for _inject_run_json_fields helper."""

    def test_adds_missing_fields(self) -> None:
        import tensor_grep.cli.ast_workflows as aw

        raw = json.dumps({"version": 1, "routing_backend": "X"})
        result = aw._inject_run_json_fields(raw, "search")
        parsed = json.loads(result)
        assert parsed["schema_version"] == 1
        assert parsed["mode"] == "search"
        assert parsed["total_matches"] == 0

    def test_does_not_overwrite_existing_values(self) -> None:
        import tensor_grep.cli.ast_workflows as aw

        raw = json.dumps({"version": 1, "schema_version": 2, "mode": "stdin", "total_matches": 5})
        result = aw._inject_run_json_fields(raw, "search")
        parsed = json.loads(result)
        # Must preserve existing values
        assert parsed["schema_version"] == 2
        assert parsed["mode"] == "stdin"
        assert parsed["total_matches"] == 5

    def test_handles_non_json_gracefully(self) -> None:
        import tensor_grep.cli.ast_workflows as aw

        bad = "not json"
        assert aw._inject_run_json_fields(bad, "search") == bad

    def test_handles_json_array_gracefully(self) -> None:
        import tensor_grep.cli.ast_workflows as aw

        arr = json.dumps([1, 2, 3])
        assert aw._inject_run_json_fields(arr, "search") == arr

    def test_all_four_modes(self) -> None:
        import tensor_grep.cli.ast_workflows as aw

        for mode in ("search", "rewrite-plan", "apply", "stdin"):
            raw = json.dumps({"version": 1})
            parsed = json.loads(aw._inject_run_json_fields(raw, mode))
            assert parsed["mode"] == mode, f"mode not set for {mode}"
            assert "total_matches" in parsed
            assert "schema_version" in parsed


# ---------------------------------------------------------------------------
# M4: batch-rewrite error documentation (Python-side validation)
# ---------------------------------------------------------------------------


class TestBatchRewriteDocumentation:
    """M4 - ensure run_command docstring / comments document the batch-rewrite format.

    The cryptic ``$`` error ("invalid batch rewrite config field `$`: expected object")
    originates in Rust:
      rust_core/src/main.rs  function parse_batch_rewrite_config_value  (~line 6088)
    That function is called by load_batch_rewrite_config when the JSON root is not
    an object.  Python cannot intercept it because bootstrap.py dispatches
    ``tg run --batch-rewrite`` directly to the native binary via
    _run_native_tg_command, bypassing run_command entirely.
    """

    def test_run_command_source_documents_batch_rewrite_format(self) -> None:
        """The ast_workflows.py source must contain the expected-object format hint."""
        import inspect

        import tensor_grep.cli.ast_workflows as aw

        src = inspect.getsource(aw.run_command)
        assert '"rewrites"' in src, (
            "run_command source must document the 'rewrites' key for --batch-rewrite"
        )
        assert '"pattern"' in src, (
            "run_command source must document the 'pattern' key for batch-rewrite entries"
        )
        assert '"replacement"' in src, (
            "run_command source must document the 'replacement' key for batch-rewrite entries"
        )

    def test_batch_rewrite_error_description_in_source(self) -> None:
        """The source must reference the Rust location for the cryptic $ error."""
        import inspect

        import tensor_grep.cli.ast_workflows as aw

        src = inspect.getsource(aw.run_command)
        assert "parse_batch_rewrite_config_value" in src, (
            "run_command must reference parse_batch_rewrite_config_value in Rust for the $ error"
        )
