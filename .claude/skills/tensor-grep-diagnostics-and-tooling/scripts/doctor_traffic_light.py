#!/usr/bin/env python3
"""Fast, read-only PASS/WARN/FAIL summary of a ``tg doctor --json`` payload.

WHY THIS EXISTS: ``tg doctor --json`` emits 35-50+ top-level fields (more with
``--with-lsp``), and the human-readable ``tg doctor`` renderer
(``_render_doctor_payload`` in ``src/tensor_grep/cli/main.py``) is a straight
field dump -- it does not tell you which fields are load-bearing or what a
bad value looks like. The only thing that DOES compute "is this doctor
payload healthy" today is ``scripts/agent_readiness.py``'s internal
``_validate_doctor_payload`` -- but that lives inside a ~3-5 minute, 13+
check gate you have to run in full to see it. This script extracts the same
interpretation logic into a ~2-5 second, standalone, read-only check you can
run against ANY installed ``tg`` (not just a repo checkout) while iterating.

This is a SUPPLEMENT to, not a replacement for, the real gates:
  - `python scripts/agent_readiness.py --json`  (the governed pre-push gate)
  - `tg dogfood --json`                          (verdict + JSON envelope)
See `.claude/skills/tensor-grep-diagnostics-and-tooling/SKILL.md` for what
each of those actually proves. Passing this traffic light is NOT a substitute
for either -- it only tells you whether one `tg doctor --json` snapshot looks
sane before you spend the 3-5 minutes.

READ-ONLY CONTRACT: this script never writes into the repo. It only writes to
a path YOU pass via --output (same convention as scripts/agent_readiness.py
and `tg dogfood`).

Usage:
    python doctor_traffic_light.py                       # tg on PATH, cwd root, human table
    python doctor_traffic_light.py --root C:/some/repo --tg-bin tg
    python doctor_traffic_light.py --with-lsp             # slower; probes LSP providers too
    python doctor_traffic_light.py --json                 # machine-readable summary on stdout
    python doctor_traffic_light.py --output out.json      # also write the JSON summary to a file
    python doctor_traffic_light.py --from-file doctor.json  # interpret an already-captured payload

Exit code: 0 if no check is FAIL (WARN/INFO do not fail the exit code, mirroring
`tg doctor`'s own non-gating nature -- it is a diagnostic, not a CI gate).
Exit code: 1 if any check is FAIL, or if `tg doctor --json` itself could not
be run/parsed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Ground-truthed against src/tensor_grep/cli/main.py as of v1.17.25 (2026-07-02).
# Re-verify with: grep -n "search_acceleration_backend\"" src/tensor_grep/cli/main.py
KNOWN_BACKENDS = {
    "standalone-native-tg",
    "rust-core-extension",
    "python",
    "native-standalone",  # accepted by scripts/agent_readiness.py's validator but not
    # currently emitted by _build_doctor_payload; kept here so this script
    # never FAILs on a value the governed gate would still accept.
}
KNOWN_RUST_BINARY_STATUSES = {"matches", "stale-skipped", "missing", "stale", "mismatch"}
KNOWN_LAUNCHER_KINDS = {
    "native-exe",
    "managed-native",
    "cmd-shim",
    "powershell-shim",
    "python-entrypoint",
    "bash-shim",
    "foreign",
    "unknown",
}


class Check:
    __slots__ = ("name", "status", "detail")

    def __init__(self, name: str, status: str, detail: str) -> None:
        self.name = name
        self.status = status  # one of PASS / WARN / FAIL / INFO
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def _truncate(text: object, limit: int = 220) -> str:
    rendered = str(text) if text is not None else ""
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + f"... <truncated {len(rendered) - limit} chars>"


def run_doctor_json(
    *, tg_bin: str, root: Path, with_lsp: bool, timeout_s: float
) -> tuple[dict[str, Any] | None, str]:
    """Run ``<tg_bin> doctor --json [--with-lsp|--no-lsp]`` and parse stdout.

    Returns (payload_or_None, error_message). Never raises -- callers treat a
    None payload as a single FAIL check.
    """
    cmd = [tg_bin, "doctor", "--json", "--with-lsp" if with_lsp else "--no-lsp"]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return None, f"could not find `{tg_bin}` on PATH (or as a direct path)"
    except subprocess.TimeoutExpired:
        return None, f"`{' '.join(cmd)}` timed out after {timeout_s:g}s"
    if completed.returncode != 0:
        return None, (
            f"`{' '.join(cmd)}` exited {completed.returncode}: "
            f"{_truncate(completed.stderr.strip() or completed.stdout.strip())}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return None, f"doctor stdout was not valid JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "doctor JSON must be an object"
    return payload, ""


def build_checklist(payload: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []

    version = payload.get("version")
    checks.append(
        Check("version", "PASS" if version else "FAIL", f"installed tensor-grep {version!r}")
    )

    backend = payload.get("search_acceleration_backend")
    checks.append(
        Check(
            "search_acceleration_backend",
            "PASS" if backend in KNOWN_BACKENDS else "WARN",
            str(backend),
        )
    )

    launcher_kind = payload.get("path_tg_first_launcher_kind")
    path_matches = payload.get("path_tg_first_version_matches")
    if path_matches is False:
        detail = payload.get("path_tg_foreign_warning") or (
            "first PATH `tg` reports a different version than the installed package"
        )
        remediation = payload.get("path_tg_foreign_remediation")
        if remediation:
            detail = f"{detail} | remediation: {remediation}"
        checks.append(Check("path_tg_first_version_matches", "FAIL", _truncate(detail)))
    else:
        note = "" if launcher_kind in KNOWN_LAUNCHER_KINDS else " (unrecognized launcher_kind)"
        checks.append(
            Check(
                "path_tg_first_version_matches",
                "PASS",
                f"launcher_kind={launcher_kind}{note}",
            )
        )

    rust_status = payload.get("rust_binary_version_status")
    if rust_status in {"matches", "stale-skipped"}:
        checks.append(Check("rust_binary_version_status", "PASS", str(rust_status)))
    elif rust_status == "missing":
        # Expected/benign for backend == "python" or a fresh rust-core-extension-only
        # install with no standalone native binary at all.
        checks.append(
            Check(
                "rust_binary_version_status",
                "WARN",
                f"missing (backend={backend!r}); benign unless you expect a standalone native binary",
            )
        )
    elif rust_status in {"stale", "mismatch"}:
        warning = payload.get("rust_binary_version_warning") or ""
        remediation = payload.get("rust_binary_remediation") or ""
        checks.append(
            Check(
                "rust_binary_version_status",
                "FAIL",
                _truncate(f"{rust_status}: {warning} | remediation: {remediation}"),
            )
        )
    else:
        checks.append(
            Check("rust_binary_version_status", "WARN", f"unrecognized status: {rust_status!r}")
        )

    mcp_warning = payload.get("mcp_stdio_launcher_warning")
    if mcp_warning:
        checks.append(Check("mcp_stdio_launcher_warning", "WARN", _truncate(mcp_warning)))
    else:
        checks.append(Check("mcp_stdio_launcher_warning", "PASS", "no MCP stdio shim ambiguity"))

    gpu = payload.get("gpu") if isinstance(payload.get("gpu"), dict) else {}
    tier = gpu.get("tier") if isinstance(gpu.get("tier"), dict) else {}
    checks.append(
        Check(
            "gpu",
            "INFO",
            f"available={gpu.get('available')} search_ready={gpu.get('search_ready')} "
            f"promotion_proof={tier.get('promotion_proof')} "
            "(GPU is experimental-until-proven; this is never PASS/FAIL, see docs/gpu_crossover.md)",
        )
    )

    lsp = payload.get("lsp") if isinstance(payload.get("lsp"), dict) else {}
    if lsp.get("enabled"):
        providers = lsp.get("providers") if isinstance(lsp.get("providers"), list) else []
        ready = sum(
            1
            for provider in providers
            if isinstance(provider, dict) and provider.get("health_status") == "ready"
        )
        checks.append(
            Check(
                "lsp",
                "INFO",
                f"enabled providers={len(providers)} health_status=ready:{ready} "
                "(provider availability is not navigation proof -- see AGENTS.md LSP rules)",
            )
        )
    else:
        checks.append(Check("lsp", "INFO", "disabled (ran with --no-lsp / doctor default probe skipped)"))

    ast_grep = payload.get("ast_grep") if isinstance(payload.get("ast_grep"), dict) else {}
    checks.append(
        Check(
            "ast_grep",
            "PASS" if ast_grep.get("available") else "WARN",
            f"available={ast_grep.get('available')} binary={ast_grep.get('binary') or 'missing'}",
        )
    )

    session_daemon = (
        payload.get("session_daemon") if isinstance(payload.get("session_daemon"), dict) else {}
    )
    checks.append(
        Check(
            "session_daemon",
            "INFO",
            f"running={session_daemon.get('running')} "
            f"stale_metadata={session_daemon.get('stale_metadata')}",
        )
    )

    skipped_native = payload.get("skipped_native_tg_binaries")
    if isinstance(skipped_native, list) and skipped_native:
        checks.append(
            Check(
                "skipped_native_tg_binaries",
                "INFO",
                f"{len(skipped_native)} stale in-tree binary(ies) correctly ignored",
            )
        )

    return checks


def render_human(checks: list[Check], *, source: str) -> str:
    glyph = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]", "INFO": "[INFO]"}
    lines = [f"tg doctor traffic light -- source: {source}"]
    width = max((len(c.name) for c in checks), default=0)
    for check in checks:
        lines.append(f"{glyph.get(check.status, '[????]')} {check.name.ljust(width)}  {check.detail}")
    fails = [c for c in checks if c.status == "FAIL"]
    warns = [c for c in checks if c.status == "WARN"]
    if fails:
        overall = f"OVERALL: FAIL ({len(fails)} failing check(s))"
    elif warns:
        overall = f"OVERALL: PASS_WITH_WARNINGS ({len(warns)} warning(s))"
    else:
        overall = "OVERALL: PASS"
    lines.append(overall)
    return "\n".join(lines)


def overall_status(checks: list[Check]) -> str:
    if any(c.status == "FAIL" for c in checks):
        return "FAIL"
    if any(c.status == "WARN" for c in checks):
        return "PASS_WITH_WARNINGS"
    return "PASS"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fast read-only PASS/WARN/FAIL summary of `tg doctor --json`. "
            "Supplements, does not replace, scripts/agent_readiness.py and `tg dogfood`."
        )
    )
    parser.add_argument("--root", type=Path, default=Path("."), help="cwd for the doctor subprocess.")
    parser.add_argument("--tg-bin", default="tg", help="tg executable to invoke (default: tg on PATH).")
    parser.add_argument(
        "--with-lsp",
        action="store_true",
        help="Include LSP provider probes (slower; default runs --no-lsp for speed).",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Subprocess timeout in seconds.")
    parser.add_argument("--json", action="store_true", help="Print the JSON summary instead of a table.")
    parser.add_argument(
        "--output", type=Path, default=None, help="Also write the JSON summary to this path."
    )
    parser.add_argument(
        "--from-file",
        type=Path,
        default=None,
        help="Interpret an already-captured `tg doctor --json` payload instead of running tg.",
    )
    args = parser.parse_args(argv)

    started = time.monotonic()
    if args.from_file is not None:
        try:
            payload = json.loads(args.from_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: could not read/parse {args.from_file}: {exc}", file=sys.stderr)
            return 1
        source = str(args.from_file)
        error = ""
    else:
        payload, error = run_doctor_json(
            tg_bin=args.tg_bin,
            root=args.root.resolve(),
            with_lsp=args.with_lsp,
            timeout_s=args.timeout,
        )
        source = f"{args.tg_bin} doctor --json {'--with-lsp' if args.with_lsp else '--no-lsp'} (root={args.root.resolve()})"

    if payload is None:
        checks = [Check("tg_doctor_invocation", "FAIL", error)]
    else:
        checks = build_checklist(payload)

    elapsed_s = round(time.monotonic() - started, 3)
    summary = {
        "artifact": "doctor_traffic_light",
        "source": source,
        "elapsed_s": elapsed_s,
        "overall": overall_status(checks),
        "checks": [c.to_dict() for c in checks],
    }

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(render_human(checks, source=source))

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return 1 if summary["overall"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
