from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
IS_WINDOWS = os.name == "nt"
ARTIFACT_TAIL_LINE_LIMIT = 20
ARTIFACT_TAIL_LINE_CHAR_LIMIT = 4000

from tensor_grep.cli.progress import (  # noqa: E402
    PROGRESS_MODES,
    ProgressReporter,
    positive_progress_interval_s,
)


class ReadinessError(RuntimeError):
    """Raised when an agent-readiness check returns invalid output."""


Validator = Callable[[str, Path, str], None]


class Check(NamedTuple):
    name: str
    command: list[str]
    description: str
    timeout_s: int = 60
    validator: Validator | None = None
    required: bool = True


_PYTHON_SUBPROCESS_TG_VERSION_PROBE = (
    "import subprocess, sys; "
    "completed = subprocess.run(['tg', '--version'], capture_output=True, text=True); "
    "sys.stdout.write(completed.stdout); "
    "sys.stderr.write(completed.stderr); "
    "raise SystemExit(completed.returncode)"
)


def read_project_version(repo_root: Path) -> str:
    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _json_from_stdout(stdout: str) -> Any:
    stripped = stdout.strip()
    if not stripped:
        raise ReadinessError("expected JSON output, got empty stdout")
    starts = [idx for idx in (stripped.find("{"), stripped.find("[")) if idx >= 0]
    if not starts:
        raise ReadinessError("expected JSON output, found no JSON object or array")
    decoder = json.JSONDecoder()
    payload, _offset = decoder.raw_decode(stripped[min(starts) :])
    return payload


def _norm_path(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\\", "/").rstrip("/").lower()


def validate_version_output(stdout: str, _repo_root: Path, expected_version: str) -> None:
    expected_lines = {f"tensor-grep {expected_version}", f"tg {expected_version}"}
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not expected_lines.intersection(lines):
        raise ReadinessError(
            f"expected one of {sorted(expected_lines)!r} in version output, got {lines or ['<empty>']}"
        )


def validate_repo_cli_warmup_version_output(
    stdout: str, repo_root: Path, expected_version: str
) -> None:
    try:
        validate_version_output(stdout, repo_root, expected_version)
    except ReadinessError as exc:
        raise ReadinessError(
            f"repo-local uv/tg entrypoint is stale or unsynchronized: {exc}. "
            "Run `uv sync` or `uv run --refresh-package tensor-grep tg --version` "
            "before trusting repo-local dogfood."
        ) from exc


def validate_windows_launcher_quoted_patterns(
    _stdout: str, repo_root: Path, _expected_version: str
) -> None:
    if not IS_WINDOWS:
        return

    probe_dir = repo_root / "artifacts" / "agent_readiness"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_file = probe_dir / "launcher_argv_probe.txt"
    probe_file.write_text("agent launcher sentinel\nplain text\n", encoding="utf-8")

    pattern = "agent no-such-phrase"
    tg_cmd = shutil.which("tg.cmd")
    if not tg_cmd:
        raise ReadinessError("could not resolve public tg.cmd for quoted-argument probe")

    cases = [
        (
            "cmd /c tg via Python subprocess.run([...])",
            ["cmd", "/c", "tg", "search", pattern, str(probe_file)],
        ),
        (
            "direct tg.cmd via Python subprocess.run([...])",
            [tg_cmd, "search", pattern, str(probe_file)],
        ),
    ]
    for label, command in cases:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            check=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        if completed.returncode != 1 or stdout or "no-such-phrase" in stderr:
            raise ReadinessError(
                f"{label} did not preserve quoted multi-word no-match pattern; "
                f"exit={completed.returncode}, stdout={stdout or '<empty>'}, "
                f"stderr={stderr or '<empty>'}"
            )


def _public_search_flag_sweep_cases(probe_dir: Path) -> list[tuple[str, list[str]]]:
    log_file = probe_dir / "app.log"
    inverse_config_flags = [
        "--no-pcre2-unicode",
        "--no-auto-hybrid-regex",
        "--no-text",
        "--no-binary",
        "--no-follow",
        "--no-glob-case-insensitive",
        "--no-ignore-file-case-insensitive",
        "--ignore-dot",
        "--ignore-exclude",
        "--ignore-files",
        "--ignore-global",
        "--ignore-messages",
        "--ignore-parent",
        "--ignore-vcs",
        "--no-one-file-system",
        "--no-block-buffered",
        "--no-byte-offset",
        "--no-column",
        "--no-crlf",
        "--no-encoding",
        "--no-fixed-strings",
        "--no-invert-match",
        "--no-mmap",
        "--no-multiline",
        "--no-multiline-dotall",
        "--no-pcre2",
        "--no-pre",
        "--no-search-zip",
        "--no-context-separator",
        "--no-include-zero",
        "--no-line-buffered",
        "--no-max-columns-preview",
        "--no-trim",
        "--no-json",
        "--no-stats",
    ]
    return [
        ("short-with-filename", ["tg", "search", "-H", "ERROR", str(log_file)]),
        (
            "long-with-filename",
            ["tg", "search", "--with-filename", "ERROR", str(log_file)],
        ),
        ("short-no-filename", ["tg", "search", "-I", "ERROR", str(log_file)]),
        (
            "long-no-filename",
            ["tg", "search", "--no-filename", "ERROR", str(log_file)],
        ),
        ("short-quiet", ["tg", "search", "-q", "ERROR", str(log_file)]),
        ("long-quiet", ["tg", "search", "--quiet", "ERROR", str(log_file)]),
        ("short-no-line-number", ["tg", "search", "-N", "ERROR", str(log_file)]),
        (
            "long-no-line-number",
            ["tg", "search", "--no-line-number", "ERROR", str(log_file)],
        ),
        ("stats", ["tg", "search", "--stats", "ERROR", str(log_file)]),
        ("debug", ["tg", "search", "--debug", "ERROR", str(log_file)]),
        ("trace", ["tg", "search", "--trace", "ERROR", str(log_file)]),
        ("pcre2-unicode", ["tg", "search", "--pcre2-unicode", "ERROR", str(log_file)]),
        (
            "rg-inverse-config-overrides",
            ["tg", "search", *inverse_config_flags, "ERROR", str(log_file)],
        ),
        (
            "column-no-column-last-wins",
            [
                "tg",
                "search",
                "--format",
                "rg",
                "--column",
                "--no-column",
                "-n",
                "-F",
                "ERROR",
                str(log_file),
            ],
        ),
        ("ignore", ["tg", "search", "--ignore", "ERROR", str(log_file)]),
        ("messages", ["tg", "search", "--messages", "ERROR", str(log_file)]),
        ("require-git", ["tg", "search", "--require-git", "ERROR", str(log_file)]),
        ("no-hidden", ["tg", "search", "--no-hidden", "ERROR", str(log_file)]),
        ("engine", ["tg", "search", "--engine", "auto", "ERROR", str(log_file)]),
        ("case-sensitive", ["tg", "search", "-s", "ERROR", str(log_file)]),
        ("line-regexp", ["tg", "search", "-x", "ERROR failed", str(log_file)]),
        ("threads", ["tg", "search", "-j", "1", "ERROR", str(log_file)]),
        ("iglob", ["tg", "search", "--iglob", "*.log", "ERROR", str(probe_dir)]),
        ("type-not", ["tg", "search", "-T", "rust", "ERROR", str(probe_dir)]),
        ("unrestricted", ["tg", "search", "-u", "ERROR", str(probe_dir)]),
        (
            "root-option-first-sort",
            ["tg", "--sort", "path", "-n", "-F", "ERROR", str(probe_dir)],
        ),
        ("root-option-first-type", ["tg", "-t", "rust", "fn", str(probe_dir)]),
        (
            "root-option-first-count-matches",
            ["tg", "--count-matches", "ERROR", str(log_file)],
        ),
    ]


def _extract_help_option_tokens(help_text: str) -> set[str]:
    return set(re.findall(r"(?<![\w-])-{1,2}[A-Za-z0-9][A-Za-z0-9_.-]*", help_text))


def _search_flag_tokens_for_sweep(command: list[str]) -> set[str]:
    tokens: set[str] = set()
    for arg in command[1:]:
        if arg == "--":
            break
        if re.fullmatch(r"-[A-Za-z0-9.]", arg) or re.fullmatch(
            r"--[A-Za-z0-9][A-Za-z0-9_.-]*", arg
        ):
            tokens.add(arg)
    return tokens


def validate_public_search_advertised_flag_sweep(
    _stdout: str, repo_root: Path, _expected_version: str
) -> None:
    if shutil.which("tg") is None:
        raise ReadinessError("could not resolve public tg command for search flag sweep")

    probe_dir = repo_root / "artifacts" / "agent_readiness" / "public_search_flags"
    probe_dir.mkdir(parents=True, exist_ok=True)
    (probe_dir / "app.log").write_text("ERROR failed\nINFO ok\n", encoding="utf-8")
    (probe_dir / "lib.rs").write_text("fn main() {}\n", encoding="utf-8")

    help_result = subprocess.run(
        ["tg", "search", "--help"],
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        check=False,
    )
    if help_result.returncode != 0:
        raise ReadinessError(
            "could not read public tg search --help for advertised flag sweep: "
            f"exit={help_result.returncode}, stderr={help_result.stderr.strip() or '<empty>'}"
        )
    advertised_flags = _extract_help_option_tokens(help_result.stdout)
    required_flags = set().union(
        *(
            _search_flag_tokens_for_sweep(command)
            for _label, command in _public_search_flag_sweep_cases(probe_dir)
        )
    )
    missing_flags = sorted(required_flags - advertised_flags)
    if missing_flags:
        raise ReadinessError(
            "search help missing advertised sweep flags: " + ", ".join(missing_flags)
        )

    failures: list[str] = []
    for label, command in _public_search_flag_sweep_cases(probe_dir):
        completed = subprocess.run(
            command,
            cwd=repo_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            check=False,
        )
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        if completed.returncode not in {0, 1}:
            failures.append(
                f"{label}: exit={completed.returncode}, command={command!r}, "
                f"stdout={stdout or '<empty>'}, stderr={stderr or '<empty>'}"
            )
            continue
        if "unexpected argument" in stderr.lower():
            failures.append(
                f"{label}: command={command!r} emitted unexpected-argument stderr: {stderr}"
            )

    if failures:
        raise ReadinessError("public search advertised flag sweep failed: " + "; ".join(failures))


def _validate_doctor_payload(
    stdout: str,
    _repo_root: Path,
    expected_version: str,
    *,
    require_fresh_shell_match: bool,
) -> None:
    payload = _json_from_stdout(stdout)
    if not isinstance(payload, dict):
        raise ReadinessError("doctor JSON must be an object")
    if payload.get("version") != expected_version:
        raise ReadinessError(
            f"doctor version mismatch: expected {expected_version}, got {payload.get('version')}"
        )
    first_matches = payload.get("path_tg_first_version_matches")
    if first_matches is False:
        message = "doctor reports PATH first tg version does not match"
        if warning := payload.get("path_tg_foreign_warning"):
            message = f"{message}: {warning}"
        if remediation := payload.get("path_tg_foreign_remediation"):
            message = f"{message} Remediation: {remediation}"
        raise ReadinessError(message)
    backend = payload.get("search_acceleration_backend")
    if backend not in {
        "rust-core-extension",
        "native-standalone",
        "standalone-native-tg",
        "python",
    }:
        raise ReadinessError(f"unexpected search acceleration backend: {backend!r}")
    launcher_kind = payload.get("path_tg_first_launcher_kind")
    fresh_launcher_kind = payload.get("fresh_shell_path_tg_first_launcher_kind")
    if not isinstance(launcher_kind, str) or not isinstance(fresh_launcher_kind, str):
        raise ReadinessError("doctor JSON missing launcher route diagnostics")
    fresh_matches = payload.get("fresh_shell_path_tg_first_version_matches")
    if fresh_matches is not True:
        message = "doctor reports fresh-shell tg version does not match"
        if warning := payload.get("fresh_shell_path_tg_foreign_warning"):
            message = f"{message}: {warning}"
        if remediation := payload.get("fresh_shell_path_tg_foreign_remediation"):
            message = f"{message} Remediation: {remediation}"
        fresh_is_foreign = payload.get("fresh_shell_path_tg_first_is_foreign") is True
        if require_fresh_shell_match or fresh_is_foreign:
            raise ReadinessError(message)
    if IS_WINDOWS:
        python_launcher_kind = payload.get("python_subprocess_path_tg_first_launcher_kind")
        if not isinstance(python_launcher_kind, str):
            raise ReadinessError("doctor JSON missing Python subprocess launcher route diagnostics")
        python_matches = payload.get("python_subprocess_path_tg_first_version_matches")
        if python_matches is not True:
            message = "doctor reports Python subprocess tg version does not match"
            if warning := payload.get("python_subprocess_path_tg_foreign_warning"):
                message = f"{message}: {warning}"
            if remediation := payload.get("python_subprocess_path_tg_foreign_remediation"):
                message = f"{message} Remediation: {remediation}"
            raise ReadinessError(message)
    rust_matches = payload.get("rust_binary_version_matches")
    rust_status = payload.get("rust_binary_version_status")
    rust_version_ok = rust_status == "matches" and rust_matches is True
    stale_skip_ok = rust_status == "stale-skipped" and rust_matches is None
    if not (rust_version_ok or stale_skip_ok):
        raise ReadinessError(
            "doctor reports managed native-upgrade contract drift: "
            f"rust_binary_version_matches={rust_matches!r}, "
            f"rust_binary_version_status={rust_status!r}"
        )


def validate_doctor_payload(stdout: str, repo_root: Path, expected_version: str) -> None:
    _validate_doctor_payload(
        stdout,
        repo_root,
        expected_version,
        require_fresh_shell_match=True,
    )


def validate_repo_doctor_payload(stdout: str, repo_root: Path, expected_version: str) -> None:
    _validate_doctor_payload(
        stdout,
        repo_root,
        expected_version,
        require_fresh_shell_match=False,
    )


def validate_context_render_payload(
    stdout: str, _repo_root: Path | None = None, *, expected_fragment: str
) -> None:
    payload = _json_from_stdout(stdout)
    if not isinstance(payload, dict):
        raise ReadinessError("context-render JSON must be an object")

    primary_file = _norm_path((payload.get("edit_plan_seed") or {}).get("primary_file"))
    if not primary_file:
        raise ReadinessError("context-render missing edit_plan_seed.primary_file")

    consistency = payload.get("context_consistency")
    if not isinstance(consistency, dict):
        raise ReadinessError("context-render missing context_consistency object")

    selected_files = {
        _norm_path(item.get("path")) for item in payload.get("files", []) if isinstance(item, dict)
    }
    selected_sources = {
        _norm_path(item.get("file") or item.get("path"))
        for item in payload.get("sources", [])
        if isinstance(item, dict)
    }
    follow_ups = {
        _norm_path(item.get("path") or item.get("file"))
        for item in (payload.get("navigation_pack") or {}).get("follow_up_reads", [])
        if isinstance(item, dict)
    }
    represented_paths = selected_files | selected_sources | follow_ups
    is_represented = any(
        path and (path == primary_file or path.endswith(primary_file)) for path in represented_paths
    )
    omitted_primary = consistency.get("omitted_primary_file")
    if not is_represented and not omitted_primary:
        raise ReadinessError(
            "edit_plan_seed.primary_file is not represented and no omission reason was emitted"
        )

    nav_file = _norm_path(
        ((payload.get("navigation_pack") or {}).get("primary_target") or {}).get("file")
    )
    if (
        nav_file
        and primary_file
        and nav_file != primary_file
        and not nav_file.endswith(primary_file)
    ):
        raise ReadinessError(
            "navigation_pack.primary_target contradicts edit_plan_seed.primary_file"
        )

    rendered_context = str(payload.get("rendered_context") or "")
    rendered_sources = "\n".join(
        str(item.get("rendered_source") or "")
        for item in payload.get("sources", [])
        if isinstance(item, dict)
    )
    if expected_fragment not in rendered_context and expected_fragment not in rendered_sources:
        raise ReadinessError(f"missing expected context fragment: {expected_fragment}")


def _validate_invoice_context(stdout: str, repo_root: Path, _expected_version: str) -> None:
    validate_context_render_payload(
        stdout, repo_root, expected_fragment="tax = subtotal * TAX_RATE"
    )


def validate_ast_info(stdout: str, _repo_root: Path, _expected_version: str) -> None:
    payload = _json_from_stdout(stdout)
    languages: object
    if isinstance(payload, dict):
        languages = payload.get("languages")
    else:
        languages = payload
    if not isinstance(languages, list) or "python" not in {str(item) for item in languages}:
        raise ReadinessError("ast-info JSON must expose the python language identifier")


def validate_ast_run(stdout: str, _repo_root: Path, _expected_version: str) -> None:
    payload = _json_from_stdout(stdout)
    text = json.dumps(payload, sort_keys=True)
    if "class" not in text and "name" not in text:
        raise ReadinessError("AST run smoke did not emit a class match payload")


def validate_docs_claims(_stdout: str, repo_root: Path, expected_version: str) -> None:
    required_docs = [
        repo_root / "AGENTS.md",
        repo_root / "README.md",
        repo_root / "SKILL.md",
        repo_root / "docs" / "SESSION_HANDOFF.md",
        repo_root / "docs" / "CONTINUATION_PLAN.md",
        repo_root / "docs" / "CONTRACTS.md",
    ]
    required_fragments = [
        f"v{expected_version}",
        "python scripts/agent_readiness.py",
        "context_consistency",
        "tg agent",
        "agent-capsule-hardcases",
        "validated compatibility set",
        "broad generated-root scan",
        "rg` remains",
        "ast-grep",
    ]
    missing: list[str] = []
    current_version_pattern = re.compile(
        r"current `v(?P<version>\d+\.\d+\.\d+)` "
        r"(?P<subject>shell/version resolution|positioning|release line)"
    )
    latest_release_patterns = [
        (
            "latest tagged GitHub release",
            re.compile(
                r"Latest tagged GitHub release:\s*\[`v(?P<version>\d+\.\d+\.\d+)`\]",
                re.IGNORECASE,
            ),
        ),
        (
            "latest complete PyPI release",
            re.compile(
                r"Latest complete PyPI release:\s*\[`v(?P<version>\d+\.\d+\.\d+)`\]",
                re.IGNORECASE,
            ),
        ),
    ]

    def allows_complete_public_release_lag(content: str, found_version: str) -> bool:
        publication_failed = (
            "asset/PyPI publication did not complete" in content
            or "`publish-pypi` did not complete" in content
        )
        return (
            publication_failed
            and "`publish-success-gate` failed" in content
            and f"PyPI latest remains `{found_version}`" in content
        )

    for path in required_docs:
        content = path.read_text(encoding="utf-8")
        for fragment in required_fragments:
            if fragment not in content:
                missing.append(f"{path.relative_to(repo_root)} missing `{fragment}`")
        for match in current_version_pattern.finditer(content):
            found = match.group("version")
            if found != expected_version:
                missing.append(
                    f"{path.relative_to(repo_root)} contains stale current release prose "
                    f"`v{found}` for {match.group('subject')}; expected `v{expected_version}`"
                )
        for subject, pattern in latest_release_patterns:
            for match in pattern.finditer(content):
                found = match.group("version")
                if found != expected_version:
                    if (
                        subject == "latest complete PyPI release"
                        and allows_complete_public_release_lag(content, found)
                    ):
                        continue
                    missing.append(
                        f"{path.relative_to(repo_root)} contains stale {subject} "
                        f"`v{found}`; expected `v{expected_version}`"
                    )

    gpu_docs = [
        repo_root / "README.md",
        repo_root / "docs" / "benchmarks.md",
        repo_root / "docs" / "gpu_crossover.md",
        repo_root / "docs" / "PAPER.md",
    ]
    gpu_fragments = [
        f"post-`v{expected_version}`",
        "1GB and 5GB correctness",
        "RTX 4070",
        "RTX 5070",
        "no crossover",
        "public managed",
        "not promotion-ready",
    ]
    banned_gpu_fragments = [
        "mathematically guaranteeing",
        "0ms interpreter lag",
        "peak theoretical throughput",
        "further buries",
        "designed to win on larger files",
        "GPU-ready",
    ]
    for path in gpu_docs:
        content = path.read_text(encoding="utf-8")
        lower_content = content.lower()
        for fragment in gpu_fragments:
            haystack = lower_content if fragment == "no crossover" else content
            needle = fragment if fragment != "no crossover" else fragment.lower()
            if needle not in haystack:
                missing.append(f"{path.relative_to(repo_root)} missing `{fragment}`")
        for fragment in banned_gpu_fragments:
            if fragment in content:
                missing.append(f"{path.relative_to(repo_root)} contains `{fragment}`")

    for path in (repo_root / "docs" / "benchmarks.md", repo_root / "docs" / "gpu_crossover.md"):
        content = path.read_text(encoding="utf-8")
        for fragment in (
            "fair baseline is `rg -F -e ... -e ...`",
            "sidecar-routed rows are unsupported for native CUDA promotion",
        ):
            if fragment not in content:
                missing.append(f"{path.relative_to(repo_root)} missing `{fragment}`")
    if missing:
        raise ReadinessError("; ".join(missing))


def build_check_plan(
    *,
    repo_root: Path,
    expected_version: str,
    include_shell_probes: bool,
    include_wsl_probe: bool,
) -> list[Check]:
    checks: list[Check] = []
    if include_shell_probes:
        powershell_probe = (
            ["powershell", "-NoProfile", "-Command", "tg --version"]
            if IS_WINDOWS
            else ["tg", "--version"]
        )
        checks.extend([
            Check(
                name="public-version-powershell",
                command=powershell_probe,
                description="Verify profiled shell public tg version.",
                timeout_s=30,
                validator=validate_version_output,
            ),
            Check(
                name="public-version-cmd",
                command=["cmd", "/c", "tg --version"],
                description="Verify cmd.exe public tg version.",
                timeout_s=30,
                validator=validate_version_output,
                required=False,
            ),
            Check(
                name="public-version-pwsh-noprofile",
                command=["pwsh", "-NoProfile", "-Command", "tg --version"],
                description="Verify unprofiled PowerShell public tg version.",
                timeout_s=30,
                validator=validate_version_output,
                required=False,
            ),
            Check(
                name="public-version-git-bash",
                command=["bash", "-lc", "command -v tg && tg --version"],
                description="Verify Git Bash/no-extension shim public tg version.",
                timeout_s=30,
                validator=validate_version_output,
                required=False,
            ),
        ])
        if include_wsl_probe:
            checks.append(
                Check(
                    name="public-version-wsl",
                    command=["wsl", "bash", "-lc", "command -v tg && tg --version"],
                    description="Verify WSL public tg shim version.",
                    timeout_s=30,
                    validator=validate_version_output,
                    required=False,
                )
            )
        if IS_WINDOWS:
            checks.extend([
                Check(
                    name="public-version-python-subprocess",
                    command=[
                        sys.executable,
                        "-c",
                        _PYTHON_SUBPROCESS_TG_VERSION_PROBE,
                    ],
                    description=("Verify Python subprocess(['tg', ...]) resolves tensor-grep."),
                    timeout_s=30,
                    validator=validate_version_output,
                ),
                Check(
                    name="public-doctor-cmd",
                    command=["cmd", "/c", "tg doctor --json --no-lsp"],
                    description="Verify cmd.exe public tg can run sidecar-backed doctor.",
                    timeout_s=90,
                    validator=validate_doctor_payload,
                ),
                Check(
                    name="public-doctor-pwsh-noprofile",
                    command=[
                        "pwsh",
                        "-NoProfile",
                        "-Command",
                        "tg doctor --json --no-lsp",
                    ],
                    description=(
                        "Verify unprofiled PowerShell public tg can run sidecar-backed doctor."
                    ),
                    timeout_s=90,
                    validator=validate_doctor_payload,
                    required=False,
                ),
                Check(
                    name="public-windows-launcher-quoted-patterns",
                    command=[],
                    description=(
                        "Verify cmd.exe and direct tg.cmd preserve quoted multi-word "
                        "no-match patterns."
                    ),
                    timeout_s=30,
                    validator=validate_windows_launcher_quoted_patterns,
                ),
            ])
        checks.append(
            Check(
                name="public-search-advertised-flag-sweep",
                command=[],
                description=(
                    "Verify installed public tg accepts advertised rg-style search flags "
                    "and option-first root search forwarding."
                ),
                timeout_s=60,
                validator=validate_public_search_advertised_flag_sweep,
            )
        )

    checks.extend([
        Check(
            name="repo-cli-build-warmup",
            command=["uv", "run", "tg", "--version"],
            description=(
                "Synchronize and warm the repo-local uv/tg editable build before bounded agent trust probes."
            ),
            timeout_s=240 if IS_WINDOWS else 180,
            validator=validate_repo_cli_warmup_version_output,
        ),
        Check(
            name="repo-doctor",
            command=["uv", "run", "--no-sync", "tg", "doctor", "--json", "--no-lsp"],
            description="Verify repo tg doctor reports version and PATH parity.",
            timeout_s=90,
            validator=validate_repo_doctor_payload,
        ),
        Check(
            name="context-render-trust",
            command=[
                "uv",
                "run",
                "--no-sync",
                "tg",
                "context-render",
                "tests/unit/test_trust_planning.py",
                "--query",
                "invoice tax calculation",
                "--json",
            ],
            description="Verify context-render keeps primary target and body context useful.",
            timeout_s=90,
            validator=_validate_invoice_context,
        ),
        Check(
            name="rg-parity-edges",
            command=[
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/e2e/test_rg_parity_edges.py",
                "-q",
            ],
            description="Verify deterministic rg parity edge cases.",
            timeout_s=180,
        ),
        Check(
            name="broad-generated-scan-guard",
            command=[
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/unit/test_cli_modes.py",
                "-q",
                "-k",
                "broad_generated_root_scan",
            ],
            description="Verify broad generated-root file-list scans require bounds or opt-in.",
            timeout_s=120,
        ),
        Check(
            name="ast-info-json",
            command=["uv", "run", "--no-sync", "tg", "ast-info", "--json"],
            description="Verify AST language inventory JSON is parseable.",
            timeout_s=60,
            validator=validate_ast_info,
        ),
        Check(
            name="ast-run-smoke",
            command=[
                "uv",
                "run",
                "--no-sync",
                "tg",
                "run",
                "--pattern",
                "class $NAME: $$$BODY",
                "tests/unit/test_trust_planning.py",
                "--lang",
                "python",
                "--json",
            ],
            description="Verify AST run smoke through the repo CLI.",
            timeout_s=90,
            validator=validate_ast_run,
        ),
        Check(
            name="mcp-context-render-smoke",
            command=[
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/unit/test_mcp_server.py",
                "-q",
                "-k",
                "test_tg_context_render_mcp_preserves_invoice_tax_body_and_primary_target",
            ],
            description="Verify MCP context-render preserves invoice tax body and target.",
            timeout_s=120,
        ),
        Check(
            name="agent-capsule",
            command=[
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/unit/test_cli_modes.py",
                "tests/unit/test_mcp_server.py",
                "-q",
                "-k",
                "agent_capsule",
            ],
            description="Verify tg agent Actionable Context Capsule CLI and MCP contracts.",
            timeout_s=120,
        ),
        Check(
            name="agent-capsule-mixed-language",
            command=[
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/unit/test_cli_modes.py",
                "-q",
                "-k",
                (
                    "(agent_capsule and (language or validation or invoice)) "
                    "or context_render_filters_pytest_only_validation_for_typescript_primary "
                    "or edit_plan_filters_pytest_only_validation_for_typescript_primary"
                ),
            ],
            description="Verify mixed-language invoice capsule and validation trust stay aligned.",
            timeout_s=120,
        ),
        Check(
            name="agent-capsule-hardcases",
            command=[
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "tests/unit/test_agent_capsule_hardcases.py",
                "-q",
            ],
            description=(
                "Verify polyglot monorepo, generated-noise, and Rust/Python/JS/TS "
                "agent capsule hardcases."
            ),
            timeout_s=120,
        ),
        Check(
            name="docs-claim-check",
            command=[],
            description="Verify public docs keep the current positioning and gate command.",
            timeout_s=5,
            validator=validate_docs_claims,
        ),
    ])
    return checks


def _command_available(command: list[str]) -> bool:
    if not command:
        return True
    return shutil.which(command[0]) is not None


def _argparse_progress_interval_s(value: str) -> float:
    try:
        return positive_progress_interval_s(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _bounded_tail_lines(
    text: str,
    *,
    line_limit: int = ARTIFACT_TAIL_LINE_LIMIT,
    char_limit: int = ARTIFACT_TAIL_LINE_CHAR_LIMIT,
) -> list[str]:
    bounded: list[str] = []
    for line in text.splitlines()[-line_limit:]:
        if len(line) <= char_limit:
            bounded.append(line)
        else:
            omitted = len(line) - char_limit
            bounded.append(f"{line[:char_limit]}... <truncated {omitted} chars>")
    return bounded


def run_check(check: Check, *, repo_root: Path, expected_version: str) -> dict[str, Any]:
    started = time.monotonic()
    if not _command_available(check.command):
        if check.required:
            status = "failed"
            message = f"required command not found: {check.command[0]}"
        else:
            status = "skipped"
            message = f"optional command not found: {check.command[0]}"
        return {
            "name": check.name,
            "status": status,
            "duration_s": round(time.monotonic() - started, 3),
            "command": check.command,
            "message": message,
        }

    stdout = ""
    stderr = ""
    returncode = 0
    try:
        if check.command:
            env = dict(os.environ)
            env.setdefault("PYTHONUTF8", "1")
            completed = subprocess.run(
                check.command,
                cwd=repo_root,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=check.timeout_s,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            returncode = completed.returncode
            if completed.returncode != 0:
                raise ReadinessError(
                    f"exit {completed.returncode}; stderr={stderr.strip() or '<empty>'}"
                )
        if check.validator is not None:
            check.validator(stdout, repo_root, expected_version)
    except subprocess.TimeoutExpired as exc:
        status = "failed"
        message = f"timed out after {check.timeout_s}s"
        stdout = str(exc.stdout or "")
        stderr = str(exc.stderr or "")
    except ReadinessError as exc:
        status = "failed"
        message = str(exc)
    else:
        status = "passed"
        message = "ok"

    return {
        "name": check.name,
        "status": status,
        "duration_s": round(time.monotonic() - started, 3),
        "command": check.command,
        "returncode": returncode,
        "message": message,
        "stdout_tail": _bounded_tail_lines(stdout),
        "stderr_tail": _bounded_tail_lines(stderr),
    }


def build_report(
    *,
    results: list[dict[str, Any]],
    expected_version: str,
    repo_root: Path,
) -> dict[str, Any]:
    return {
        "artifact": "agent_readiness_report",
        "expected_version": expected_version,
        "root": str(repo_root.resolve()),
        "summary": {
            "passed": sum(1 for result in results if result["status"] == "passed"),
            "failed": sum(1 for result in results if result["status"] == "failed"),
            "skipped": sum(1 for result in results if result["status"] == "skipped"),
        },
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the fast tensor-grep agent-readiness dogfood gate."
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root to validate.")
    parser.add_argument(
        "--expected-version", help="Expected tensor-grep version. Defaults to pyproject."
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout.")
    parser.add_argument(
        "--progress",
        choices=PROGRESS_MODES,
        default="auto",
        help="Progress reporting mode: auto, always, or never. Emits to stderr only.",
    )
    parser.add_argument(
        "--progress-interval-s",
        type=_argparse_progress_interval_s,
        default=30.0,
        help="Seconds between progress heartbeats for the active phase.",
    )
    parser.add_argument(
        "--no-shell-probes", action="store_true", help="Skip public shell version probes."
    )
    parser.add_argument("--no-wsl-probe", action="store_true", help="Skip the optional WSL probe.")
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()
    expected_version = args.expected_version or read_project_version(repo_root)
    checks = build_check_plan(
        repo_root=repo_root,
        expected_version=expected_version,
        include_shell_probes=not args.no_shell_probes,
        include_wsl_probe=not args.no_wsl_probe,
    )

    progress = ProgressReporter(
        mode=args.progress,
        interval_s=args.progress_interval_s,
        json_output=args.json,
    )
    results: list[dict[str, Any]] = []
    for check in checks:
        with progress.phase(check.name):
            result = run_check(check, repo_root=repo_root, expected_version=expected_version)
        results.append(result)
        if not args.json:
            print(
                f"[{result['status'].upper()}] {check.name} "
                f"({result['duration_s']}s): {result['message']}"
            )

    report = build_report(results=results, expected_version=expected_version, repo_root=repo_root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
