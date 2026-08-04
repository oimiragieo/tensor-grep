#!/usr/bin/env python3
"""CI-only diagnostic for task 22 / PR #868 -- OBSERVE which dispatch route an explicit
``--gpu-device-ids`` search actually takes on THIS runner, instead of inferring it from source.

Background: PR #868 added a Python-side rule (``gpu_request_unhonoured``,
``src/tensor_grep/cli/formatters/json_fmt.py``) that forces ``tg search --gpu-device-ids ...``
to exit 2 when the request could not be honoured. ``tests/unit/test_cli_modes.py``'s
``test_cli_search_warns_when_gpu_device_id_out_of_local_inventory`` mocks ``Pipeline``/
``DirectoryScanner`` (via ``_patch_cli_dependencies``) to force the *Python* code path and
asserts exit 2 -- but that assertion is only trustworthy if ``search_command`` actually reaches
the Python tail rather than delegating the whole request to a native ``tg`` subprocess
(``resolve_native_tg_binary()`` + ``_can_delegate_to_native_tg_search``,
``src/tensor_grep/cli/main.py``). That test fails on CI's ``test-python`` matrix with
``exit_code == 0`` and a clean (non-crashing) ``Result`` -- consistent with a REAL, working,
version-matching native binary answering the request instead of the mocked Python route ever
being consulted.

EARLY LESSON FROM BUILDING THIS PROBE (kept as a comment, not silently dropped): the first draft
of this script ran the search against the REAL, unmocked ``Pipeline`` and immediately hit an
UNRELATED hard failure -- ``Pipeline._raise_explicit_gpu_configuration_error`` raises
``ConfigurationError`` (exit 2, via ``_exit_search_error``'s own default) whenever
``--gpu-device-ids`` is requested and neither CuDF nor Torch can be imported, which is true on
most plain CPU boxes. That exit-2 predates PR #868 entirely and has NOTHING to do with
``gpu_request_unhonoured`` -- but because both share the same numeric code, a naive real-Pipeline
probe would report "exit 2 either way" as if it had confirmed something, when it had actually
observed two unrelated mechanisms landing on the same number (the same-arm-twice trap). This
probe therefore NEVER calls the real ``Pipeline`` -- it installs the exact same
``_FakePipeline``/``_FakeScanner``/``RipgrepBackend.is_available=False`` mocks the failing unit
test uses, so a GPU-incapable CI runner cannot short-circuit the very thing being measured, and
additionally instruments ``subprocess.run`` directly to make "delegation was attempted" an
observed fact instead of an inference from the exit code.

Two competing hypotheses were raised without being empirically settled:

* (A) ``resolve_native_tg_binary()`` returns ``None`` on ``test-python`` (a comment in
  ``.github/workflows/ci.yml`` claims the job "never builds ``rust_core/target/release/tg``"),
  so something ELSE would have to explain the exit-0 -- unconfirmed by that comment alone.
* (B) ``rust_core/Cargo.toml`` defines ``[[bin]] name = "tg"`` (and ``tg-search-fast``) in the
  SAME package/manifest maturin builds for the `pyo3/extension-module` cdylib
  (``pyproject.toml``'s ``[tool.maturin] manifest-path = "rust_core/Cargo.toml"``). If maturin's
  underlying ``cargo build`` is not scoped to ``--lib``, it would ALSO produce
  ``rust_core/target/release/tg`` as a side effect of ``pip install -e ".[dev,ast]"`` -- which is
  exactly Priority 2 of ``resolve_native_tg_binary()``, checked BEFORE any PATH scan.

This script prints the actual runtime values and runs THREE controlled comparisons (two
structural, always-correct-by-construction controls, plus the literally-requested ``--ltl``
sibling) so a human (or a future automated gate) can read the verdict directly off the CI log, on
every OS/Python leg, instead of trusting more static reasoning about either hypothesis.

Never fails the JOB on its own -- wired into ``ci.yml`` with ``continue-on-error: true``, because
this is instrumentation, not a merge gate. It DOES exit non-zero if its own positive/negative
controls fail to discriminate (see ``main`` below), because a diagnostic that cannot prove it ran
at all is worse than none (the false-zero trap: an instrument that never fires and one that fires
and finds nothing look identical unless the instrument proves it CAN fire both ways).
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _log(label: str, value: object) -> None:
    # `::notice::` makes this show up in the GitHub Actions annotations panel, not just the raw
    # step log, so it is findable without expanding the full log -- printed with a stable prefix
    # so a future automated reader can grep for it without parsing GHA-specific markup.
    print(f"::notice::[task22-diag] {label} = {value}")
    print(f"[task22-diag] {label} = {value}")


def _log_section(title: str) -> None:
    print(f"\n=== [task22-diag] {title} ===")


@dataclasses.dataclass
class _FakeBackend:
    results_by_file: dict

    def search(self, file_path, pattern, config=None):
        from tensor_grep.core.result import SearchResult

        return self.results_by_file.get(
            file_path, SearchResult(matches=[], total_files=0, total_matches=0)
        )


class _FakePipeline:
    """Byte-for-byte the same shape as ``tests/unit/test_cli_modes.py::_FakePipeline`` -- a
    Pipeline stand-in that never touches CuDF/Torch/real hardware detection, so a GPU-incapable
    CI runner cannot short-circuit the thing this probe is trying to observe."""

    def __init__(self, force_cpu=False, config=None):
        self.backend = _FAKE_BACKEND
        self.selected_backend_name = "FakeBackend"
        self.selected_backend_reason = "task22_diag_fake_pipeline"
        self.selected_gpu_device_ids = []
        self.selected_gpu_chunk_plan_mb = []

    def get_backend(self):
        return self.backend


class _FakeScanner:
    def __init__(self, config=None):
        self.scan_truncated = False
        self.scan_truncation_cause = None
        self.unreadable_path_count = 0
        self.unreadable_path_sample: list = []
        self.max_scan_entries = 200_000

    def walk(self, path):
        yield from _FAKE_WALK.get(path, [])


_FAKE_WALK: dict = {".": ["a.log"]}
_FAKE_BACKEND: _FakeBackend | None = None


class _MockedEnvironment:
    """Context manager installing the exact fixture-mocks the failing unit test uses, plus a
    ``subprocess.run`` interceptor so "a delegation attempt happened" is an OBSERVED fact
    (recorded argv) rather than inferred from the exit code alone."""

    def __init__(self):
        self.delegation_calls: list[list[str]] = []
        self._originals: dict = {}

    def __enter__(self):
        import tensor_grep.core.pipeline as pipeline_mod
        import tensor_grep.io.directory_scanner as scanner_mod
        from tensor_grep.backends.ripgrep_backend import RipgrepBackend
        from tensor_grep.cli import main as tg_main

        self._originals = {
            "Pipeline": pipeline_mod.Pipeline,
            "DirectoryScanner": scanner_mod.DirectoryScanner,
            "is_available": RipgrepBackend.is_available,
            "subprocess_run": tg_main.subprocess.run,
        }
        pipeline_mod.Pipeline = _FakePipeline
        scanner_mod.DirectoryScanner = _FakeScanner
        RipgrepBackend.is_available = lambda self: False

        original_run = self._originals["subprocess_run"]

        def _tracking_run(cmd, *args, **kwargs):
            self.delegation_calls.append([str(part) for part in cmd])
            return original_run(cmd, *args, **kwargs)

        tg_main.subprocess.run = _tracking_run
        return self

    def __exit__(self, *exc_info):
        import tensor_grep.core.pipeline as pipeline_mod
        import tensor_grep.io.directory_scanner as scanner_mod
        from tensor_grep.backends.ripgrep_backend import RipgrepBackend
        from tensor_grep.cli import main as tg_main

        pipeline_mod.Pipeline = self._originals["Pipeline"]
        scanner_mod.DirectoryScanner = self._originals["DirectoryScanner"]
        RipgrepBackend.is_available = self._originals["is_available"]
        tg_main.subprocess.run = self._originals["subprocess_run"]
        return False


def _invoke(args: list[str], *, resolve_native_override=None) -> dict[str, object]:
    """Run ``tg`` in-process via the real ``CliRunner`` (exactly how pytest exercises it --
    NOT a fresh subprocess against the real Pipeline, see the module docstring for why), with the
    fixture mocks installed, and optionally with ``resolve_native_tg_binary`` overridden."""
    from typer.testing import CliRunner

    from tensor_grep.cli import main as tg_main

    global _FAKE_BACKEND
    from tensor_grep.core.result import MatchLine, SearchResult

    _FAKE_BACKEND = _FakeBackend(
        results_by_file={
            "a.log": SearchResult(
                matches=[MatchLine(line_number=1, text="ERROR", file="a.log")],
                total_files=1,
                total_matches=1,
            )
        }
    )

    with _MockedEnvironment() as mocked:
        original_resolver = tg_main.resolve_native_tg_binary
        if resolve_native_override is not _SENTINEL_NO_OVERRIDE:
            tg_main.resolve_native_tg_binary = lambda: resolve_native_override
        try:
            result = CliRunner().invoke(tg_main.app, args)
        finally:
            tg_main.resolve_native_tg_binary = original_resolver

    return {
        "args": args,
        "resolve_native_override": (
            "unset (ambient)"
            if resolve_native_override is _SENTINEL_NO_OVERRIDE
            else resolve_native_override
        ),
        "exit_code": result.exit_code,
        "exception": repr(result.exception) if result.exception else None,
        "output": result.output,
        "delegation_calls": mocked.delegation_calls,
    }


_SENTINEL_NO_OVERRIDE = object()


def _summarize(run: dict[str, object]) -> str:
    return (
        f"exit_code={run['exit_code']} "
        f"delegation_calls={run['delegation_calls']} "
        f"exception={run['exception']}"
    )


def _static_checks() -> None:
    import os
    import sys as _sys

    from tensor_grep.cli.main import resolve_native_tg_binary
    from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary

    _log_section("environment identity")
    _log("sys.executable", _sys.executable)
    _log("sys.version", _sys.version.replace("\n", " "))
    _log("platform", _sys.platform)

    _log_section("static in-tree candidate check (Priority 2, independent of resolver logic)")
    binary_name = "tg.exe" if _sys.platform.startswith("win") else "tg"
    for build_profile in ("release", "debug"):
        candidate = _REPO_ROOT / "rust_core" / "target" / build_profile / binary_name
        _log(f"rust_core/target/{build_profile}/{binary_name} exists", candidate.is_file())
        if candidate.is_file():
            try:
                stat = candidate.stat()
                _log("  -> size_bytes", stat.st_size)
                _log("  -> mtime", stat.st_mtime)
            except OSError as exc:
                _log("  -> stat() failed", repr(exc))

    _log_section("PATH scan for every 'tg'-named executable (Priority 3 candidates)")
    tg_name = "tg.exe" if _sys.platform.startswith("win") else "tg"
    ts_name = "tensor-grep.exe" if _sys.platform.startswith("win") else "tensor-grep"
    seen: set[str] = set()
    for raw_entry in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_entry:
            continue
        for name in (tg_name, ts_name):
            candidate = Path(raw_entry).expanduser() / name
            if candidate.is_file():
                resolved = str(candidate.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                _log(f"PATH candidate ({name})", resolved)
    if not seen:
        _log("PATH candidates found", "none")

    _log_section("resolve_native_tg_binary() -- UNMODIFIED ambient value")
    try:
        ambient_native = resolve_native_tg_binary()
    except Exception as exc:  # pragma: no cover -- diagnostic must never crash the workflow
        _log("resolve_native_tg_binary() raised", repr(exc))
        ambient_native = None
    _log("resolve_native_tg_binary() [ambient]", ambient_native)
    if ambient_native is not None:
        import subprocess

        try:
            version_probe = subprocess.run(
                [str(ambient_native), "--version"], capture_output=True, text=True, timeout=5
            )
            _log(
                "ambient resolved binary --version (proves it is a REAL, responding "
                "executable, not a stale/broken path)",
                (version_probe.stdout or version_probe.stderr).strip(),
            )
        except Exception as exc:
            _log("ambient resolved binary --version FAILED", repr(exc))

    _log_section("resolve_ripgrep_binary()")
    try:
        resolved_rg = resolve_ripgrep_binary()
    except Exception as exc:
        _log("resolve_ripgrep_binary() raised", repr(exc))
        resolved_rg = None
    _log("resolve_ripgrep_binary()", resolved_rg)


def main() -> int:
    _static_checks()

    _log_section("CONTROL A (must fire): resolve_native_tg_binary forced to a bogus real Path")
    # Proves the interceptor CAN observe a delegation attempt -- a probe that only ever reports
    # "no delegation" is indistinguishable from a probe that is not wired up at all.
    control_forced_delegation = _invoke(
        ["search", "ERROR", ".", "--gpu-device-ids", "5"],
        resolve_native_override=Path("/nonexistent/task22-diag-forced-path"),
    )
    _log("forced-delegation control", _summarize(control_forced_delegation))
    if not control_forced_delegation["delegation_calls"]:
        print(
            "POSITIVE-CONTROL FAILURE: forcing resolve_native_tg_binary() to a bogus Path did "
            "NOT produce an observed subprocess.run(...) call. The interceptor itself is broken "
            "(or _can_delegate_to_native_tg_search's eligibility gate rejected this exact "
            "invocation for an unrelated reason) -- every comparison below would be reporting "
            "noise as signal. Aborting."
        )
        return 1

    _log_section(
        "CONTROL B (must NOT fire): resolve_native_tg_binary forced to None -- the hermetic "
        "Python route, exactly what the failing unit test's fixtures assume"
    )
    control_forced_python = _invoke(
        ["search", "ERROR", ".", "--gpu-device-ids", "5"],
        resolve_native_override=None,
    )
    _log("forced-Python control", _summarize(control_forced_python))
    # This control asserts ZERO delegation calls -- that is the property it exists to test.
    #
    # It used to ALSO require `exit_code == 2`, on the since-retired rule that an unhonoured
    # explicit --gpu-device-ids request forced exit 2. Backlog #22 was RETIRED as an exit-code
    # rule on 2026-08-01 (PR #868): the request is disclosed IN-BAND
    # (gpu_evidence_status / native_gpu_unavailable / not_gpu_proof_reason) and does NOT
    # independently change the exit code, which stays the ordinary complete/not-found 0/1.
    #
    # Left as-is, this control aborted with "the fixture mocks are wrong" -- blaming the SUBJECT
    # for a change in the CONTRACT, which is the exact shape of a stale instrument reporting a
    # confident false negative. It now asserts the retirement instead: exit 2 must NOT appear.
    if control_forced_python["delegation_calls"] or control_forced_python["exit_code"] == 2:
        print(
            "NEGATIVE-CONTROL FAILURE: resolve_native_tg_binary() forced to None still shows a "
            f"delegation attempt and/or a retired exit code ({_summarize(control_forced_python)})"
            " -- expected zero delegation_calls and the ordinary 0/1 exit. An exit of 2 here would"
            " mean the retired gpu_request_unhonoured exit-code rule (backlog #22 / PR #868) has"
            " been reintroduced. Either the override is not taking effect or that rule is back."
        )
        return 1

    _log(
        "both controls",
        "PASS -- forcing a real path triggers an observed delegation attempt, forcing None "
        "reaches the mocked Python tail with zero delegation calls and does NOT exit 2 "
        "(the gpu_request_unhonoured exit-code rule was retired: backlog #22 / PR #868).",
    )

    _log_section(
        "COMPARISON 1: AMBIENT (no override at all -- whatever THIS runner naturally resolves) "
        "vs the two controls above"
    )
    subject_ambient = _invoke(
        ["search", "ERROR", ".", "--gpu-device-ids", "5"],
        resolve_native_override=_SENTINEL_NO_OVERRIDE,
    )
    _log("subject (ambient, no override)", _summarize(subject_ambient))
    if subject_ambient["delegation_calls"]:
        _log(
            "COMPARISON 1 verdict",
            "AMBIENT DELEGATION CONFIRMED on this leg -- search_command called "
            "subprocess.run(...) with a native tg binary INSTEAD of consulting the "
            "FakePipeline/FakeScanner mocks this probe installed. This IS the mechanism behind "
            f"the CI failure. delegation argv: {subject_ambient['delegation_calls']}",
        )
    elif subject_ambient["exit_code"] == 2:
        _log(
            "COMPARISON 1 verdict",
            "NO delegation observed and exit_code == 2, matching the forced-Python control -- "
            "on THIS leg, resolve_native_tg_binary() plausibly returned None (or "
            "_can_delegate_to_native_tg_search refused for an unrelated reason) and the "
            "gpu_request_unhonoured rule fired correctly via the mocked Python tail.",
        )
    else:
        _log(
            "COMPARISON 1 verdict",
            "NEITHER control matched: no delegation was observed, but exit_code is not 2 "
            f"either ({_summarize(subject_ambient)}). This is a THIRD, previously undescribed "
            "behavior on this leg and needs its own investigation -- do not assume it is "
            "covered by hypothesis A or B.",
        )

    _log_section(
        "COMPARISON 2 (explicitly requested control): the sibling --ltl invocation, which is "
        "refused from native delegation via _NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS "
        "REGARDLESS of resolve_native_tg_binary() -- run with NO override, same as the ambient "
        "subject above"
    )
    control_ltl = _invoke(
        ["search", "ERROR", ".", "--ltl", "--gpu-device-ids", "5"],
        resolve_native_override=_SENTINEL_NO_OVERRIDE,
    )
    _log("control (--ltl, structurally refuses delegation)", _summarize(control_ltl))
    _log("subject (ambient, no override, from Comparison 1)", _summarize(subject_ambient))
    if control_ltl["delegation_calls"]:
        _log(
            "COMPARISON 2 sanity check FAILED",
            "the --ltl invocation delegated, which should be structurally impossible -- "
            "_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS may have changed; do not trust this "
            "comparison until that is investigated.",
        )
    elif bool(subject_ambient["delegation_calls"]) != bool(control_ltl["delegation_calls"]):
        _log(
            "COMPARISON 2 verdict",
            "DIFFER as expected if ambient delegation is happening -- the ambient subject "
            "delegated while the structurally-refused --ltl control did not, corroborating "
            "Comparison 1.",
        )
    else:
        _log(
            "COMPARISON 2 verdict",
            "SAME (neither delegated) -- consistent with the ambient subject also having taken "
            "the Python route on this leg.",
        )

    _log_section("done")
    print(
        "[task22-diag] This step is continue-on-error and never fails ci.yml on its own; read "
        "the verdicts above (backed by an OBSERVED subprocess.run(...) call, not an inferred "
        "exit code) to settle which route the ambient environment took on THIS leg."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
