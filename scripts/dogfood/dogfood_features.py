#!/usr/bin/env python3
"""Full-feature dogfood: run the REAL installed ``tg`` binary across every user-facing feature on a
generated fixture repo, asserting exit codes + output shape.

Why this exists: our unit/integration tests use ``CliRunner``, which invokes the typer ``app``
DIRECTLY and BYPASSES the real ``tg`` front door (``tensor_grep.cli.bootstrap:main_entry``, which
forwards plain searches to ripgrep). v1.14.0's ``tg search --rank`` shipped broken in plain-text mode
(``rg: unrecognized flag --rank``) precisely because no test ran the real binary. This script does —
it is meant to run post-release in a clean Docker container / venv against the PUBLISHED artifact:

    pip install "tensor-grep==<version>"
    python dogfood_features.py            # uses the `tg` on PATH

Exit 0 = the shipped CLI installs and every feature works. Exit 1 = a regression (with the failing
command + output). Add a new ``check(...)`` line whenever a feature ships so the battery grows.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# In Docker/venv `tg` is on PATH; TG_BIN lets you point at a specific binary for local verification.
TG = os.environ.get("TG_BIN") or shutil.which("tg") or "tg"
_RESULTS: list[tuple[bool, str, int, str]] = []


def _run(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run([TG, *args], capture_output=True, text=True, timeout=120, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def check(
    desc: str,
    args: list[str],
    *,
    want_exit: int = 0,
    must_contain: str | None = None,
    must_not_contain: str | None = None,
    json_key: str | None = None,
) -> None:
    """Run ``tg <args>`` and record pass/fail against the given expectations."""
    code, out, err = _run(args)
    combined = out + err
    ok, detail = True, ""
    if code != want_exit:
        ok, detail = False, f"exit {code} != {want_exit}"
    if ok and must_contain is not None and must_contain not in combined:
        ok, detail = False, f"missing {must_contain!r}"
    if ok and must_not_contain is not None and must_not_contain in combined:
        ok, detail = False, f"contains forbidden {must_not_contain!r}"
    if ok and json_key is not None:
        try:
            ok = json_key in json.loads(out)
            detail = "" if ok else f"json missing key {json_key!r}"
        except json.JSONDecodeError as exc:
            ok, detail = False, f"invalid json: {exc}"
    snippet = combined.strip().splitlines()[:2]
    _RESULTS.append((ok, desc, code, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {desc}  (exit {code}){'  -> ' + detail if detail else ''}")
    if not ok:
        for line in snippet:
            print(f"        | {line[:160]}")


def _build_fixture(root: Path) -> None:
    """A tiny multi-file repo: hub imported by two others (gives the import-graph features edges)."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "hub.py").write_text(
        "def hub_fn(amount):\n    return amount * 2\n", encoding="utf-8"
    )
    (root / "src" / "leaf.py").write_text(
        "import hub\n\n\ndef leaf_fn():\n    return hub.hub_fn(21)\n", encoding="utf-8"
    )
    (root / "src" / "other.py").write_text(
        "import hub\n\n\ndef other_fn():\n    return hub.hub_fn(7)\n", encoding="utf-8"
    )
    (root / "main.py").write_text(
        "from src.leaf import leaf_fn\n\n\ndef run():\n    return leaf_fn()\n", encoding="utf-8"
    )
    (root / "lib.rs").write_text(
        "pub fn parse(input: &str) -> usize {\n    input.len()\n}\n", encoding="utf-8"
    )


def main() -> int:
    print(f"=== tensor-grep full-feature dogfood (binary: {TG}) ===")
    code, ver, _ = _run(["--version"])
    print(f"version: {ver.strip()} (exit {code})\n")

    with tempfile.TemporaryDirectory() as td:
        fixture = Path(td) / "repo"
        _build_fixture(fixture)
        fx = str(fixture)
        empty = Path(td) / "empty"
        empty.mkdir()

        check("version", ["--version"], must_contain="tensor-grep")
        # --- text search (the ripgrep-compatible front door) ---
        check("search (plain text)", ["search", "hub_fn", fx], must_contain="hub")
        # REGRESSION GUARD (v1.14.0/v1.15.1): plain-text --rank must NOT leak to ripgrep.
        check(
            "search --rank (PLAIN -regression guard)",
            ["search", "hub_fn", fx, "--rank"],
            must_not_contain="unrecognized flag",
        )
        check(
            "search --rank --json",
            ["search", "hub_fn", fx, "--rank", "--json"],
            json_key="matched_file_paths",
        )
        check("search --json", ["search", "hub_fn", fx, "--json"], json_key="matched_file_paths")
        # --- orientation capsule (v1.15.0) ---
        check("orient", ["orient", fx], must_contain="orientation")
        check("orient --json", ["orient", fx, "--json"], json_key="routing_reason")
        check("orient (empty dir -graceful)", ["orient", str(empty)], must_contain="orientation")
        # --- repo map + agent context ---
        check("map", ["map", fx])
        check("agent --json", ["agent", "--query", "hub", "--json", fx], json_key="version")

    failures = [r for r in _RESULTS if not r[0]]
    print()
    print(f"=== {len(_RESULTS) - len(failures)}/{len(_RESULTS)} checks passed ===")
    if failures:
        print("DOGFOOD FAILURES:")
        for _, desc, code, detail in failures:
            print(f"  - {desc} (exit {code}) {detail}")
        return 1
    print("ALL DOGFOOD CHECKS PASSED -shipped artifact installs and every feature works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
