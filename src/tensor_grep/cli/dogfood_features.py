#!/usr/bin/env python3
"""Selected feature dogfood: run the REAL installed ``tg`` binary on a
generated fixture repo, asserting exit codes + output shape.

Why this exists: our unit/integration tests use ``CliRunner``, which invokes the typer ``app``
DIRECTLY and BYPASSES the real ``tg`` front door (``tensor_grep.cli.bootstrap:main_entry``, which
forwards plain searches to ripgrep). v1.14.0's ``tg search --rank`` shipped broken in plain-text mode
(``rg: unrecognized flag --rank``) precisely because no test ran the real binary. This script does —
it is meant to run post-release in a clean Docker container / venv against the PUBLISHED artifact:

    pip install "tensor-grep==<version>"
    python dogfood_features.py            # uses the `tg` on PATH

Exit 0 = all selected checks pass. Exit 1 = a regression (with the failing
command + output). Add a new ``check(...)`` line whenever a feature ships so the battery grows.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tensor_grep.cli.dogfood import _terminate_process_tree
from tensor_grep.cli.frontdoor_hops import probe_env

# In Docker/venv `tg` is on PATH; TG_BIN lets you point at a specific binary for local verification.
TG = os.environ.get("TG_BIN") or shutil.which("tg") or "tg"
_RESULTS: list[tuple[bool, str, int, str]] = []
_SKIPS: list[str] = []
_DEADLINE = float("inf")
_IDENTITY: dict[str, Any] = {}
_EXPECTED_VERSION: str | None = None
_INVENTORY: dict[str, Any] = {}


def _run(args: list[str], *, extra_env: dict[str, str] | None = None) -> tuple[int, str, str]:
    remaining = _DEADLINE - time.monotonic()
    if remaining <= 0:
        return 124, "", "shared dogfood deadline exceeded"
    try:
        proc = subprocess.Popen(
            [TG, *args],
            env={**os.environ, "TG_SESSION_DAEMON_AUTOSTART": "0", **(extra_env or {})},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            ),
            start_new_session=os.name != "nt",
        )
        try:
            out, err = proc.communicate(timeout=min(120, remaining))
        except subprocess.TimeoutExpired as exc:
            _terminate_process_tree(proc.pid)
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err = "", "process output did not settle after termination"
            return 124, out, f"{err}\n{exc}"
    except OSError as exc:
        return 2, "", str(exc)
    return int(proc.returncode), out, err


def _artifact_identity() -> dict[str, Any]:
    executable = Path(shutil.which(TG) or TG).expanduser().resolve()
    if not executable.is_file():
        raise RuntimeError(f"Selected executable does not exist: {executable}")
    code, output, error = _run(["--version"])
    observed = re.search(r"\b(?:tg|tensor-grep) (\d+\.\d+\.\d+[a-zA-Z0-9.+-]*)", output)
    if code != 0 or observed is None:
        raise RuntimeError(f"Cannot verify selected artifact version: {output} {error}")
    if _EXPECTED_VERSION is not None and observed[1] != _EXPECTED_VERSION:
        raise RuntimeError(
            f"Stale artifact: expected {_EXPECTED_VERSION}, got {observed[1]} at {executable}"
        )
    try:
        expected = importlib.metadata.version("tensor-grep")
    except importlib.metadata.PackageNotFoundError:
        expected = None
    if expected is not None and observed[1] != expected:
        raise RuntimeError(
            f"Stale artifact: expected {expected}, got {observed[1]} at {executable}. "
            "Refresh with `uv run --refresh-package tensor-grep tg --version`; run the harness with the selected artifact's environment interpreter."
        )
    python = os.environ.get("TG_SIDECAR_PYTHON") or sys.executable
    probe = subprocess.run(
        [
            python,
            "-I",
            "-c",
            "import json,importlib.metadata,tensor_grep,tensor_grep.rust_core; "
            "print(json.dumps({'package_origin':tensor_grep.__file__,"
            "'extension_origin':tensor_grep.rust_core.__file__,"
            "'package_version':importlib.metadata.version('tensor-grep')}))",
        ],
        env=dict(os.environ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=15,
        check=False,
    )
    origins = (
        json.loads(probe.stdout.decode("utf-8", errors="strict"))
        if probe.returncode == 0
        else {
            "package_origin": None,
            "extension_origin": None,
            "origin_probe_error": probe.stderr.decode("utf-8", errors="replace"),
        }
    )
    if os.environ.get("TG_SIDECAR_PYTHON") and origins.get("package_version") != observed[1]:
        raise RuntimeError(
            f"Stale or unverifiable sidecar package: expected {observed[1]}, "
            f"got {origins.get('package_version')!r} from {python}"
        )
    with executable.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    native_override = os.environ.get("TG_NATIVE_TG_BINARY") or os.environ.get("TG_MCP_TG_BINARY")
    native_identity = None
    if native_override:
        native = Path(native_override).expanduser().resolve()
        if not native.is_file():
            raise RuntimeError(f"Selected native executable does not exist: {native}")
        native_env = probe_env()
        if native_env is None:
            raise RuntimeError("Native artifact version unverified: TG_FRONTDOOR_HOPS limit")
        native_probe = subprocess.run(
            [str(native), "--version"],
            env=native_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
            check=False,
        )
        native_version = re.search(
            r"\btg (\d+\.\d+\.\d+[a-zA-Z0-9.+-]*)",
            native_probe.stdout.decode("utf-8", errors="strict"),
        )
        if (
            native_probe.returncode != 0
            or native_version is None
            or native_version[1] != observed[1]
        ):
            raise RuntimeError(
                f"Stale or unverifiable native artifact at {native}: {native_probe.stdout!r}"
            )
        with native.open("rb") as stream:
            native_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        native_identity = {
            "executable": str(native),
            "sha256": native_digest,
            "version": native_version[1],
        }
    for field in ("package_origin", "extension_origin"):
        origin = origins.get(field)
        if origin and Path(origin).is_file():
            with Path(origin).open("rb") as stream:
                origins[field + "_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "executable": str(executable),
        "sha256": digest,
        "version": observed[1],
        "expected_package_version": expected,
        "candidate_commit": os.environ.get("TG_DOGFOOD_CANDIDATE_COMMIT"),
        "candidate_commit_provenance": "caller-supplied; build provenance not verified",
        "origin_probe_python": python,
        "native_override": native_identity,
        "native_selection": (
            "disabled"
            if os.environ.get("TG_DISABLE_NATIVE_TG") == "1"
            else "explicit"
            if native_identity
            else "automatic_artifact_unrecorded"
        ),
        **origins,
    }


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
    must_match: str | None = None,
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
    if ok and must_match is not None and re.search(must_match, combined) is None:
        ok, detail = False, f"no match for pattern {must_match!r}"
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
    predicate: Callable[[dict[str, Any]], tuple[bool, str]],
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
    # #706 ledger PATH-canonicalization dogfood: `_discover_repo_root` only checks a `.git`
    # entry's EXISTENCE (never reads git plumbing), so an empty marker directory is enough to make
    # `ledger claim <root>/sub` and `ledger list <root>` canonicalize to the SAME physical store --
    # proving the rollup fix rather than exercising the (unchanged) non-git literal-path fallback.
    (root / ".git").mkdir(exist_ok=True)
    (root / "sub").mkdir(exist_ok=True)
    # #703 dynamic-import decoy-exclusion dogfood: a RELATIVE `import_module(...)` call plus a
    # same-named top-level decoy module. Before the fix, a naive resolver could strip the leading
    # dot and match the decoy; the fix must report `dynamic_unresolved` with no resolved edge.
    (root / "dynamic_import_demo.py").write_text(
        "from importlib import import_module\n\n"
        'import_module(".sibling_dynamic", package=__package__)\n',
        encoding="utf-8",
    )
    (root / "sibling_dynamic.py").write_text("def decoy():\n    return 'decoy'\n", encoding="utf-8")


def _dynamic_import_entry_is_honest(payload: dict[str, Any]) -> tuple[bool, str]:
    """#703 predicate: the relative ``import_module(".sibling_dynamic", ...)`` entry must be
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
    global _IDENTITY, _INVENTORY
    _RESULTS.clear()
    _SKIPS.clear()
    print(f"=== tensor-grep selected feature dogfood (binary: {TG}) ===")
    try:
        _IDENTITY = _artifact_identity()
        print("artifact: " + json.dumps(_IDENTITY, sort_keys=True))
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ARTIFACT REFUSED: {exc}")
        return 1

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
        try:
            discovered = subprocess.run(
                [
                    os.environ.get("TG_SIDECAR_PYTHON") or sys.executable,
                    "-I",
                    "-m",
                    "tensor_grep.cli.dogfood_inventory",
                ],
                cwd=fixture,
                capture_output=True,
                timeout=min(30, max(0.1, _DEADLINE - time.monotonic())),
                check=False,
            )
            _INVENTORY = (
                json.loads(discovered.stdout.decode("utf-8", errors="strict"))
                if discovered.returncode == 0
                else {}
            )
            _record(
                bool(_INVENTORY.get("mcp_tools")) and bool(_INVENTORY.get("mcp_actions")),
                "CLI/subcommand and MCP/action inventories",
                discovered.returncode,
                discovered.stderr.decode("utf-8", errors="replace")[:400],
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            _record(False, "CLI/subcommand and MCP/action inventories", 2, str(exc))

        # `--version` output DIVERGES between the two front doors, measured 2026-09-12:
        #     published wheel, clean Docker container : "tensor-grep 1.119.8"
        #     managed NATIVE binary on PATH           : "tg 1.119.7"   (rust_core/src/main.rs
        #                                               prints `println!("tg {}", ...)`)
        # The old `must_contain="tensor-grep"` therefore FAILS against a native binary, and a bare
        # `tg <semver>` pattern FAILS against the published wheel this harness is built to test --
        # so either product name alone makes the gate wrong for half its own population.
        #
        # This harness runs against WHATEVER `tg` is on PATH (wheel in Docker, native locally), so
        # it asserts the part both doors genuinely owe: a semver, behind either product name. That
        # is not an endorsement of the divergence -- `tests/e2e/test_routing_parity.py` never
        # compares `--version` ACROSS launchers (it only uses it as a skip-guard probe), which is
        # the coverage gap that let two doors drift apart; closing that is a separate slice with a
        # public-output decision in it, not something to silently pick a winner for here.
        check("version", ["--version"], must_match=r"\b(?:tg|tensor-grep) \d+\.\d+\.\d+")
        check("CLI command inventory", ["--help"], must_contain="dogfood")
        query_file = Path(td) / "query.sql"
        query_file.write_text("SELECT 1 AS probe", encoding="utf-8-sig")
        check(
            "SQL UTF-8 BOM query file",
            ["sql", fx, "--query-file", str(query_file), "--json"],
            json_key="rows",
        )
        check(
            "SQL conflicting inputs refusal",
            ["sql", fx, "SELECT 1", "--query-file", str(query_file)],
            want_exit=2,
            must_contain="cannot be combined",
        )
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
        from tensor_grep.cli.dogfood_regressions import run_regressions

        run_regressions(Path(td), run=_run, record=_record)

        # --- session-capture v1.93.2 dogfood additions ---

        # #706: ledger claim/list/release PATH canonicalization + subtree rollup. Claim scoped
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
                "ledger list (ancestor rolls up the subdir claim -- #706)",
                ["ledger", "list", fx, "--json"],
                must_contain=str(claim_id),
            )
            check(
                "ledger release (disposable claim)",
                ["ledger", "release", fx, "--claim-id", str(claim_id), "--json"],
                json_key="released",
            )
        check(
            "checkpoint create (disposable fixture)",
            ["checkpoint", "create", fx, "--json"],
            json_key="checkpoint_id",
        )
        check("checkpoint list (disposable fixture)", ["checkpoint", "list", fx, "--json"])

        # dense-hint: every dense-absent hint (incl. `tg find`'s BM25-only degrade) leads with the
        # one-shot `tg install-dense` command, not the raw module-CLI fetch invocation.
        _check_json(
            "find (available dense route or disclosed BM25 fallback)",
            ["find", "hub_fn", fx, "--json"],
            predicate=lambda payload: (
                (True, "")
                if (
                    bool(payload.get("routing_backend"))
                    and (
                        payload.get("routing_backend") != "Bm25FindBackend"
                        or "install-dense" in json.dumps(payload)
                    )
                )
                else (False, "missing route or dense-unavailable guidance")
            ),
        )

        # importers: the reverse of `tg imports`, and the release harness never exercised it.
        # Pins the EXPLICIT-ROOT form on purpose. `tg importers FILE` with no ROOT resolves ROOT
        # from the CURRENT DIRECTORY, so an agent running it from a multi-project home scans that
        # home, hits the repo-file ceiling, and returns `result_incomplete` with exit 2 -- a real
        # footgun reported from a Cursor home workspace. Asserting the confirmed reverse edge AND
        # a complete scan means a regression that silently truncates the walk cannot pass.
        _check_json(
            "importers FILE ROOT (confirmed reverse edge, complete scan)",
            ["importers", str(fixture / "src" / "hub.py"), fx, "--json"],
            predicate=lambda payload: (
                (
                    True,
                    "",
                )
                if (
                    not payload.get("result_incomplete")
                    and int(payload.get("importer_count") or 0) >= 1
                    and any(
                        "leaf.py" in str(entry.get("file", ""))
                        for entry in (payload.get("importers") or [])
                    )
                )
                else (
                    False,
                    f"expected a complete scan with leaf.py as a confirmed importer; got "
                    f"count={payload.get('importer_count')!r} "
                    f"incomplete={payload.get('result_incomplete')!r}",
                )
            ),
        )

        # daemon-autostart: a cold/never-warmed session daemon gets an honest `autostart` posture string
        # instead of a bare `running: false` that reads as broken. Needs its OWN never-touched
        # directory: `agent --json` above (and `find`/`prepare` below) non-blockingly autostart a
        # daemon for whichever path they're pointed at, which would flip `running` to true and
        # make this field disappear by the time this check runs against the SHARED fixture `fx`.
        doctor_probe = Path(td) / "doctor_probe"
        doctor_probe.mkdir()
        check(
            "doctor --json (session_daemon.autostart honesty -- daemon-autostart)",
            ["doctor", str(doctor_probe), "--json", "--no-lsp"],
            json_key="session_daemon.autostart",
        )

        # prepare-out: `tg prepare --out FILE` persists the full capsule JSON to disk, and refuses to
        # write through a pre-existing symlink destination.
        cap_path = Path(td) / "capsule.json"
        check_output_file(
            "prepare --out (persists valid capsule JSON -- prepare-out)",
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
                "prepare --out (refuses a pre-existing symlink dest -- prepare-out)",
                ["prepare", fx, "hub_fn", "--out", str(symlink_path), "--json"],
                want_exit=1,
                must_contain="Refusing to write",
            )
        else:
            _SKIPS.append("prepare --out symlink refusal: symlink creation unavailable")
            print(
                "[SKIP] prepare --out (refuses a pre-existing symlink dest -- prepare-out)  "
                "(symlink creation unsupported in this environment)"
            )

        # #703: a relative dynamic import never resolves to a same-named top-level decoy.
        _check_json(
            "imports (relative dynamic import -> dynamic_unresolved, no decoy edge -- #703)",
            ["imports", str(fixture / "dynamic_import_demo.py"), "--json"],
            predicate=_dynamic_import_entry_is_honest,
        )

    failures = [r for r in _RESULTS if not r[0]]
    print()
    print(
        f"=== {len(_RESULTS) - len(failures)}/{len(_RESULTS)} selected checks passed; "
        f"{len(_SKIPS)} unavailable checks skipped ==="
    )
    if failures:
        print("DOGFOOD FAILURES:")
        for _, desc, code, detail in failures:
            print(f"  - {desc} (exit {code}) {detail}")
        return 1
    print("All selected dogfood checks passed. Unlisted features remain untested.")
    return 0


if __name__ == "__main__":
    import argparse
    import contextlib
    import io

    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-s", type=float, default=900)
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    _EXPECTED_VERSION = args.expected_version
    _DEADLINE = time.monotonic() + args.timeout_s
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        code = main()
    results = [
        {
            "name": name,
            "status": "passed" if ok else "timed_out" if exit_code == 124 else "failed",
            "exit_code": exit_code,
            "message": detail,
            "tested_behavior": "refusal"
            if "refus" in name.lower()
            else "fallback"
            if "fallback" in name.lower()
            else "success",
        }
        for ok, name, exit_code, detail in _RESULTS
    ]
    results.extend({"name": name, "status": "unavailable", "message": name} for name in _SKIPS)
    if not results:
        results.append({
            "name": "artifact-provenance",
            "status": "failed",
            "message": stream.getvalue(),
        })
    if args.expected_version and _IDENTITY.get("version") != args.expected_version:
        results.insert(
            0,
            {
                "name": "artifact-version",
                "status": "failed",
                "message": f"expected {args.expected_version}, got {_IDENTITY.get('version')}",
            },
        )
        code = 1
    summary = {
        status: sum(item["status"] == status for item in results)
        for status in ("passed", "failed", "timed_out", "skipped", "unavailable")
    }
    print(
        json.dumps({
            "artifact": "feature_dogfood_report",
            "artifact_identity": _IDENTITY,
            "inventories": _INVENTORY,
            "results": results,
            "selected_checks": [item["name"] for item in results],
            "summary": summary,
            "log_tail": stream.getvalue().splitlines()[-20:],
        })
    )
    sys.exit(code)
