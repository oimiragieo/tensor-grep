"""Derive the ground-truth ledger for the tg-skill-audit workflow.

``.claude/workflows/tg-skill-audit.js`` used to hand twelve fixed shell commands to a
cheap model seat and trust it to transcribe their output. Nothing about those commands
needs judgment, so this script runs them directly (list argv, no shell, bounded
timeouts) and prints one JSON object whose keys and types match ``LEDGER_SCHEMA`` in
that workflow: ``repo_root``, ``head_sha``, ``git_status``, ``skill_manifest``
(``path`` + ``blob_oid`` per tracked file under ``.claude/skills/``), ``facts``
(``name``/``value``/``derivation``) and ``raw_output``.

A command that fails or times out is reported as ``ERROR: ...`` in that fact's value
and in ``raw_output``; it is never silently dropped.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

GIT_TIMEOUT_S = 30
PY_TIMEOUT_S = 60
NET_TIMEOUT_S = 10
TG_TIMEOUT_S = 30


def _run(argv: list[str], cwd: Path, timeout: float) -> tuple[bool, str]:
    """Run ``argv`` without a shell; return (ok, stdout-or-error-text)."""
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"ERROR: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return False, f"ERROR: exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()}"
    return True, proc.stdout


def repo_root() -> Path:
    ok, out = _run(["git", "rev-parse", "--show-toplevel"], Path.cwd(), GIT_TIMEOUT_S)
    if not ok:
        raise SystemExit(out)
    return Path(out.strip())


def parse_ls_files_stage(text: str) -> list[dict[str, str]]:
    """Parse ``git ls-files -s`` rows (``mode oid stage<TAB>path``)."""
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        meta, sep, path = line.partition("\t")
        parts = meta.split()
        if sep and len(parts) == 3:
            rows.append({"path": path, "blob_oid": parts[1]})
    return rows


def _py_probe(root: Path, code: str) -> tuple[bool, str]:
    return _run([sys.executable, "-c", code], root, PY_TIMEOUT_S)


def build_ledger(root: Path, offline: bool = False) -> dict[str, Any]:
    raw: list[str] = []
    facts: list[dict[str, str]] = []

    def record(label: str, ok: bool, out: str) -> str:
        raw.append(f"$ {label}\n{out.rstrip()}")
        return out.strip()

    def git(argv: list[str]) -> tuple[bool, str]:
        ok, out = _run(["git", *argv], root, GIT_TIMEOUT_S)
        record("git " + " ".join(argv), ok, out)
        return ok, out

    ok, out = git(["rev-parse", "--show-toplevel"])
    repo_root_value = out.strip()
    ok, out = git(["rev-parse", "HEAD"])
    head_sha = out.strip()
    ok, out = git(["status", "--porcelain"])
    git_status = out.rstrip("\n") if ok else out
    ok, out = git(["ls-files", "-s", "--", ".claude/skills/"])
    manifest = parse_ls_files_stage(out) if ok else []

    def fact(name: str, derivation: str, ok: bool, out: str) -> None:
        facts.append({"name": name, "value": record(derivation, ok, out), "derivation": derivation})

    sys_path = "import sys;sys.path.insert(0,'src');"
    d1 = (
        sys_path + "from tensor_grep.cli import repo_map as r;"
        "print(r._symbol_navigation_descriptor())"
    )
    fact(
        "symbol_navigation_descriptor",
        f'python -c "{d1}"',
        *_py_probe(root, d1),
    )

    deriv2 = 'grep -c "lang_registry.register_language(" src/tensor_grep/cli/repo_map.py'
    try:
        text = (root / "src/tensor_grep/cli/repo_map.py").read_text(encoding="utf-8")
        count2 = sum(1 for ln in text.splitlines() if "lang_registry.register_language(" in ln)
        fact("register_language_calls", deriv2, True, str(count2))
    except OSError as exc:
        fact("register_language_calls", deriv2, False, f"ERROR: {exc}")

    deriv3 = "GET https://pypi.org/pypi/tensor-grep/json -> info.version"
    tg_name = "tg_version (may lag PyPI; this is the installed tg)"
    if offline:
        fact("pypi_version", deriv3, False, "SKIPPED: --offline")
        fact(tg_name, "tg --version", False, "SKIPPED: --offline")
    else:
        try:
            with urllib.request.urlopen(
                "https://pypi.org/pypi/tensor-grep/json", timeout=NET_TIMEOUT_S
            ) as resp:
                fact("pypi_version", deriv3, True, str(json.load(resp)["info"]["version"]))
        except (OSError, ValueError, KeyError) as exc:
            fact("pypi_version", deriv3, False, f"ERROR: {type(exc).__name__}: {exc}")
        ok, out = _run(["tg", "--version"], root, TG_TIMEOUT_S)
        fact(tg_name, "tg --version", ok, out)

    skills = root / ".claude" / "skills"
    n_dirs = (
        sum(1 for p in skills.iterdir() if p.is_dir() and not p.name.startswith("."))
        if skills.is_dir()
        else 0
    )
    fact("skill_folder_count", "ls -1d .claude/skills/*/ | wc -l", True, str(n_dirs))

    deriv6 = r'grep -oE "^\*\*Form [0-9]+" AGENTS.md | sort -u | wc -l'
    try:
        agents = (root / "AGENTS.md").read_text(encoding="utf-8")
        forms = set(re.findall(r"^\*\*Form [0-9]+", agents, flags=re.MULTILINE))
        fact("agents_form_count", deriv6, True, str(len(forms)))
    except OSError as exc:
        fact("agents_form_count", deriv6, False, f"ERROR: {exc}")

    deriv7 = "wc -l .github/workflows/ci.yml"
    try:
        data = (root / ".github/workflows/ci.yml").read_bytes()
        fact("ci_yml_lines", deriv7, True, str(data.count(b"\n")))
    except OSError as exc:
        fact("ci_yml_lines", deriv7, False, f"ERROR: {exc}")

    d8 = (
        sys_path + "from tensor_grep.cli import mcp_server as m;"
        "print(m._TG_MCP_SERVER_CONTRACT_VERSION)"
    )
    fact("mcp_server_contract_version", f'python -c "{d8}"', *_py_probe(root, d8))

    return {
        "repo_root": repo_root_value,
        "head_sha": head_sha,
        "git_status": git_status,
        "skill_manifest": manifest,
        "facts": facts,
        "raw_output": "\n\n".join(raw),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive the ground-truth ledger for the tg-skill-audit workflow."
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip the PyPI and `tg --version` facts (reported as SKIPPED)",
    )
    parser.add_argument("--indent", type=int, default=None, help="pretty-print JSON")
    args = parser.parse_args(argv)
    ledger = build_ledger(repo_root(), offline=args.offline)
    sys.stdout.write(json.dumps(ledger, indent=args.indent) + "\n")
    if not re.fullmatch(r"[0-9a-f]{40}", ledger["head_sha"]) or not ledger["skill_manifest"]:
        sys.stderr.write("ledger invalid: head_sha is not 40-hex or skill_manifest is empty\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
