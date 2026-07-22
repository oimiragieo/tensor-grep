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


def _record(ok: bool, desc: str, code: int, detail: str, combined: str = "") -> None:
    """Shared PASS/FAIL bookkeeping + printing for ``check()`` and the custom checks below that
    need to inspect something ``check()``'s own must_contain/json_key primitives cannot express
    (a specific list entry's fields, or a value captured for reuse in a LATER command)."""
    _RESULTS.append((ok, desc, code, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {desc}  (exit {code}){'  -> ' + detail if detail else ''}")
    if not ok and combined:
        for line in combined.strip().splitlines()[:2]:
            print(f"        | {line[:160]}")


def check(
    desc: str,
    args: list[str],
    *,
    want_exit: int = 0,
    must_contain: str | None = None,
    must_not_contain: str | None = None,
    json_key: str | None = None,
) -> None:
    """Run ``tg <args>`` and record pass/fail against the given expectations.

    ``json_key`` accepts a dotted path (e.g. ``"session_daemon.autostart"``) to reach a NESTED
    field, walking one dict level per ``.``-separated segment; a bare key (no dot) behaves exactly
    as a single-segment path always has -- a flat top-level presence check.
    """
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
            cursor: object = json.loads(out)
            for part in json_key.split("."):
                if not isinstance(cursor, dict) or part not in cursor:
                    ok, detail = False, f"json missing key path {json_key!r}"
                    break
                cursor = cursor[part]
        except json.JSONDecodeError as exc:
            ok, detail = False, f"invalid json: {exc}"
    _record(ok, desc, code, detail, combined)


def _check_json(
    desc: str,
    args: list[str],
    *,
    want_exit: int = 0,
    predicate,
) -> None:
    """Like ``check()`` but for assertions its must_contain/json_key primitives cannot express --
    ``predicate(parsed_json) -> (ok, detail)`` gets the full parsed payload (e.g. to inspect one
    entry of a list field)."""
    code, out, err = _run(args)
    ok, detail = True, ""
    if code != want_exit:
        ok, detail = False, f"exit {code} != {want_exit}"
    if ok:
        try:
            ok, detail = predicate(json.loads(out))
        except json.JSONDecodeError as exc:
            ok, detail = False, f"invalid json: {exc}"
    _record(ok, desc, code, detail, out + err)


def check_output_file(desc: str, args: list[str], out_file: Path, *, want_exit: int = 0) -> None:
    """Run ``tg <args>`` (expected to write ``out_file`` as a SIDE EFFECT, e.g. ``prepare --out``)
    and verify it exists with valid, non-empty capsule-shaped JSON -- covers behavior ``check()``'s
    stdout/stderr-only primitives cannot (a file written on disk, not printed)."""
    code, out, err = _run(args)
    ok, detail = True, ""
    if code != want_exit:
        ok, detail = False, f"exit {code} != {want_exit}"
    if ok and not out_file.exists():
        ok, detail = False, f"{out_file} was not written"
    if ok:
        try:
            payload = json.loads(out_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or "version" not in payload:
                ok, detail = False, "output file is not capsule-shaped JSON"
        except (OSError, json.JSONDecodeError) as exc:
            ok, detail = False, f"invalid output file: {exc}"
    _record(ok, desc, code, detail, out + err)


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
    # A13/#706 ledger PATH-canonicalization dogfood: `_discover_repo_root` only checks a `.git`
    # entry's EXISTENCE (never reads git plumbing), so an empty marker directory is enough to make
    # `ledger claim <root>/sub` and `ledger list <root>` canonicalize to the SAME physical store --
    # proving the rollup fix rather than exercising the (unchanged) non-git literal-path fallback.
    (root / ".git").mkdir(exist_ok=True)
    (root / "sub").mkdir(exist_ok=True)
    # A10/#703 dynamic-import decoy-exclusion dogfood: a RELATIVE `import_module(...)` call plus a
    # same-named top-level decoy module. Before the fix, a naive resolver could strip the leading
    # dot and match the decoy; the fix must report `dynamic_unresolved` with no resolved edge.
    (root / "dynamic_import_demo.py").write_text(
        "from importlib import import_module\n\n"
        'import_module(".sibling_dynamic", package=__package__)\n',
        encoding="utf-8",
    )
    (root / "sibling_dynamic.py").write_text("def decoy():\n    return 'decoy'\n", encoding="utf-8")


def _dynamic_import_entry_is_honest(payload: dict) -> tuple[bool, str]:
    """A10/#703 predicate: the relative ``import_module(".sibling_dynamic", ...)`` entry must be
    stamped ``dynamic_unresolved`` with NO resolved path -- never a same-named decoy edge."""
    entries = [entry for entry in payload.get("imports", []) if entry.get("dynamic")]
    if not entries:
        return False, "no dynamic import entry found"
    entry = entries[0]
    if not entry.get("dynamic_unresolved"):
        return False, f"expected dynamic_unresolved=true, got {entry.get('dynamic_unresolved')!r}"
    if entry.get("resolved") is not None:
        return False, f"expected resolved=null (no decoy edge), got {entry.get('resolved')!r}"
    return True, ""


def main() -> int:
    print(f"=== tensor-grep full-feature dogfood (binary: {TG}) ===")
    code, ver, _ = _run(["--version"])
    print(f"version: {ver.strip()} (exit {code})\n")

    # ignore_cleanup_errors: on Windows, AV/the search indexer can hold a
    # transient lock on tg-touched files, so rmtree at __exit__ raises
    # PermissionError AFTER every check passed -> the harness exited 1 on a
    # 10/10 green run (false negative). Swallow only the cleanup rmtree error;
    # check outcomes are already recorded in _RESULTS inside the block. (#201)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
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

        # --- session-capture v1.93.2 dogfood additions ---

        # A13/#706: ledger claim/list/release PATH canonicalization + subtree rollup. Claim scoped
        # to a SUBDIRECTORY, then list from the fixture ROOT (an ancestor) must roll the claim up --
        # the exact footgun #706 fixed (claim/list used to resolve two different physical stores).
        claim_code, claim_out, claim_err = _run([
            "ledger",
            "claim",
            str(fixture / "sub"),
            "--symbol",
            "RoundTripSymbol",
            "--json",
        ])
        claim_ok, claim_detail, claim_id = True, "", None
        if claim_code != 0:
            claim_ok, claim_detail = False, f"claim exit {claim_code} != 0"
        else:
            try:
                claim_id = json.loads(claim_out)["claim"]["claim_id"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                claim_ok, claim_detail = False, f"claim JSON missing claim_id: {exc}"
        _record(claim_ok, "ledger claim (subdir)", claim_code, claim_detail, claim_out + claim_err)
        if claim_ok and claim_id:
            check(
                "ledger list (ancestor rolls up the subdir claim -- A13)",
                ["ledger", "list", fx, "--json"],
                must_contain=str(claim_id),
            )

        # A12(a): every dense-absent hint (incl. `tg find`'s BM25-only degrade) leads with the
        # one-shot `tg install-dense` command, not the raw module-CLI fetch invocation.
        check(
            "find (dense-absent hint leads with install-dense -- A12(a))",
            ["find", "hub_fn", fx, "--json"],
            must_contain="install-dense",
        )

        # A12(b): a cold/never-warmed session daemon gets an honest `autostart` posture string
        # instead of a bare `running: false` that reads as broken. Needs its OWN never-touched
        # directory: `agent --json` above (and `find`/`prepare` below) non-blockingly autostart a
        # daemon for whichever path they're pointed at, which would flip `running` to true and
        # make this field disappear by the time this check runs against the SHARED fixture `fx`.
        doctor_probe = Path(td) / "doctor_probe"
        doctor_probe.mkdir()
        check(
            "doctor --json (session_daemon.autostart honesty -- A12(b))",
            ["doctor", str(doctor_probe), "--json", "--no-lsp"],
            json_key="session_daemon.autostart",
        )

        # A12(d): `tg prepare --out FILE` persists the full capsule JSON to disk, and refuses to
        # write through a pre-existing symlink destination.
        cap_path = Path(td) / "capsule.json"
        check_output_file(
            "prepare --out (persists valid capsule JSON -- A12(d))",
            ["prepare", fx, "hub_fn", "--out", str(cap_path), "--json"],
            cap_path,
        )
        symlink_path = Path(td) / "capsule_link.json"
        try:
            symlink_path.symlink_to(fixture / "main.py")
            symlink_supported = True
        except OSError:
            symlink_supported = False
        if symlink_supported:
            check(
                "prepare --out (refuses a pre-existing symlink dest -- A12(d))",
                ["prepare", fx, "hub_fn", "--out", str(symlink_path), "--json"],
                want_exit=1,
                must_contain="Refusing to write",
            )
        else:
            print(
                "[SKIP] prepare --out (refuses a pre-existing symlink dest -- A12(d))  "
                "(symlink creation unsupported in this environment)"
            )

        # A10/#703: a relative dynamic import never resolves to a same-named top-level decoy.
        _check_json(
            "imports (relative dynamic import -> dynamic_unresolved, no decoy edge -- A10)",
            ["imports", str(fixture / "dynamic_import_demo.py"), "--json"],
            predicate=_dynamic_import_entry_is_honest,
        )

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
