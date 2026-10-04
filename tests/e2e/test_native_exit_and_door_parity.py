"""Native `tg` exit-code honesty and root-door flag parity (bug hunt 2026-10-03, Part I).

Drives the REAL compiled native binary (never CliRunner). Named ``test_native_*.py`` so the
``native-build-smoke`` job runs it with ``TG_REQUIRE_RG_PARITY=1`` (a missing binary is then a hard
failure rather than a silent skip) and ``test_native_e2e_ci_coverage_contract.py`` covers it.

Contract (CONTRACTS.md): exit 0 = found, 1 = genuinely no match, 2 = error / incomplete.

* J-02/J-09: a killed/crashed child (rg, sidecar) is incomplete, never "no match".
* J-05: ``tg run`` errors exit 2 (no-match stays 1).
* J-04: a partial ``--apply`` failure reports the written/failed files and exits 2.
* J-01: ``--index`` decodes non-UTF-8 files lossily; a genuine read failure exits 2.
* J-03: the root door accepts the flags ``tg search`` accepts.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def _helpers():
    spec = importlib.util.find_spec("helpers.rg_parity")
    assert spec is not None, "tests/helpers/rg_parity.py must be importable"
    return importlib.import_module("helpers.rg_parity")


def _require_native_tg():
    """Resolve the native `tg`, or SKIP -- LOUDLY when the caller demanded coverage."""
    helpers = _helpers()
    required = os.environ.get("TG_REQUIRE_RG_PARITY", "").strip().lower() in {"1", "true", "yes"}
    tg_binary = helpers.resolve_native_tg_binary()
    if tg_binary is None:
        message = "native exit-code/door-parity guard needs the native tg binary (cargo build --release in rust_core/)"
        if required:
            pytest.fail(f"TG_REQUIRE_RG_PARITY=1 but {message}")
        pytest.skip(message)
    return tg_binary


def _tg(tg: Path, args: list[str], cwd: Path, env: dict[str, str] | None = None):
    return subprocess.run(
        [str(tg), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# --------------------------------------------------------------------------------------
# I.1 -- crashed / killed children exit 2 (J-02, J-09)
# --------------------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="signal-kill fake rg is POSIX-only; the exit_codes unit test covers Windows",
)
def test_killed_rg_child_exits_2_not_no_match(tmp_path: Path) -> None:
    tg = _require_native_tg()
    fake = tmp_path / "rg"
    fake.write_text("#!/bin/sh\nkill -9 $$\n", encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    target = tmp_path / "a.txt"
    target.write_text("needle\n", encoding="utf-8")
    env = {**os.environ, "TG_RG_PATH": str(fake)}
    r = _tg(tg, ["search", "-c", "needle", str(target)], tmp_path, env)
    assert r.returncode == 2, (r.returncode, r.stderr)


# --------------------------------------------------------------------------------------
# I.2 -- `tg run` errors exit 2; no-match stays 1 (J-05)
# --------------------------------------------------------------------------------------


def test_run_error_exits_2_but_no_match_still_exits_1(tmp_path: Path) -> None:
    tg = _require_native_tg()
    (tmp_path / "a.py").write_text("print_marker()\n", encoding="utf-8")

    def run(*a: str):
        return _tg(tg, ["run", "--lang", "python", *a], tmp_path)

    assert run("print_marker()", ".").returncode == 0
    assert run("absent_marker()", ".").returncode == 1, "no-match must stay 1 (CONTRACTS.md:112)"
    missing = run("print_marker()", "./nodir")
    assert missing.returncode == 2 and "Path not found" in missing.stderr, (
        missing.returncode,
        missing.stderr,
    )


# --------------------------------------------------------------------------------------
# I.3 -- partial-apply report (J-04)
# --------------------------------------------------------------------------------------


def test_run_apply_partial_failure_reports_written_and_failed_files(tmp_path: Path) -> None:
    tg = _require_native_tg()
    if os.name == "posix" and os.geteuid() == 0:
        pytest.skip("root bypasses read-only permission")
    for n in "abc":
        (tmp_path / f"{n}.py").write_text(f"def {n}(x): return x\n", encoding="utf-8")
    (tmp_path / "b.py").chmod(0o444)
    try:
        r = _tg(
            tg,
            [
                "run",
                "--lang",
                "python",
                "-p",
                "def $F($$$A): return $E",
                "-r",
                "lambda $$$A: $E",
                "--apply",
                ".",
            ],
            tmp_path,
        )
    finally:
        (tmp_path / "b.py").chmod(0o644)
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "2 file(s) written" in r.stderr and "a.py" in r.stderr and "c.py" in r.stderr, r.stderr
    assert "files_failed" in r.stderr and "b.py" in r.stderr, r.stderr


# --------------------------------------------------------------------------------------
# I.4 -- `--index` lossy decode + indexed-search errors exit 2 (J-01)
# --------------------------------------------------------------------------------------


def test_index_search_survives_non_utf8_file(tmp_path: Path) -> None:
    tg = _require_native_tg()
    (tmp_path / "good.txt").write_text("needle ok\n", encoding="utf-8")
    (tmp_path / "bad.txt").write_bytes(b"needle\xff bad\nplain\n")
    r = _tg(tg, ["search", "--index", "needle", "."], tmp_path)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "good.txt" in r.stdout and "bad.txt" in r.stdout, r.stdout


def _break_file_read(path: Path):
    """Make `path` unreadable WITHOUT touching its size or mtime (so the index stays 'fresh').

    Windows: a mandatory byte-range lock held by this process (ReadFile -> ERROR_LOCK_VIOLATION in
    the child). POSIX: ``chmod 000`` with the mtime restored. Returns a release callable, or skips.
    """
    if sys.platform == "win32":
        import msvcrt

        handle = open(path, "r+b")  # held open on purpose until release()
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 4)

        def release() -> None:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 4)
            finally:
                handle.close()

        return release
    if os.geteuid() == 0:
        pytest.skip("root bypasses read permission")
    stat = path.stat()
    path.chmod(0o000)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    def release() -> None:
        path.chmod(0o644)

    return release


def test_indexed_search_read_failure_exits_2_with_match_and_no_match_controls(
    tmp_path: Path,
) -> None:
    tg = _require_native_tg()
    (tmp_path / "a.txt").write_text("needle one\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle two\n", encoding="utf-8")
    # Controls on the fresh, readable tree: a match exits 0, a non-match exits 1.
    built = _tg(tg, ["search", "--index", "needle", "."], tmp_path)
    assert built.returncode == 0, (built.returncode, built.stderr)
    assert _tg(tg, ["search", "--index", "zzznotthere", "."], tmp_path).returncode == 1

    release = _break_file_read(tmp_path / "b.txt")
    try:
        r = _tg(tg, ["search", "--index", "needle", "."], tmp_path)
    finally:
        release()
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "b.txt" in r.stderr, r.stderr


# --------------------------------------------------------------------------------------
# I.7 -- did-you-mean ranks before truncating, identically in both doors (A-04)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["ru", "sq", "scn", "qqq"])
def test_native_did_you_mean_matches_the_python_door_rank_order(tmp_path: Path, token: str) -> None:
    import re

    from tensor_grep.cli import bootstrap

    tg = _require_native_tg()
    expected = bootstrap._nearest_commands(token)
    assert expected, token
    r = _tg(tg, [token, "--help"], tmp_path)
    assert r.returncode == 2, (r.returncode, r.stderr)
    found = re.search(r"\(did you mean (.+?)\?\)", r.stderr)
    assert found is not None, r.stderr
    assert found.group(1).split(", ") == expected, (token, found.group(1), expected)


# --------------------------------------------------------------------------------------
# I.5 -- `-c --json` / `--ndjson` emits only structured output (J-06)
# --------------------------------------------------------------------------------------


def test_count_with_json_emits_a_single_parseable_envelope(tmp_path: Path) -> None:
    tg = _require_native_tg()
    (tmp_path / "a.txt").write_text("needle one\nneedle two\nother\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("nothing\n", encoding="utf-8")
    for target in (".", "a.txt"):
        r = _tg(tg, ["search", "-c", "--json", "needle", target], tmp_path)
        assert r.returncode == 0, (r.returncode, r.stderr)
        payload = json.loads(r.stdout)  # a plain `a.txt:2` line makes this raise
        assert payload["total_matches"] == 2, r.stdout
        assert list(payload["match_counts_by_file"].values()) == [2], r.stdout
    nd = _tg(tg, ["search", "-c", "--ndjson", "needle", "."], tmp_path)
    assert nd.returncode == 0, (nd.returncode, nd.stderr)
    lines = [ln for ln in nd.stdout.splitlines() if ln.strip()]
    assert lines, "ndjson output must not be empty"
    for ln in lines:
        json.loads(ln)


# --------------------------------------------------------------------------------------
# I.6 -- the root door accepts the flags `tg search` accepts (J-03)
# --------------------------------------------------------------------------------------

_STRICT_ROOT_FLAGS = [
    "-e",
    "--regexp",
    "-a",
    "--text",
    "-L",
    "--follow",
    "--passthru",
    "--null-data",
    "--files-without-match",
]
_LENIENT_ROOT_FLAGS = ["--index", "--ast", "--allow-broad-generated-scan", "--no-config"]


def _root_args(flag: str) -> list[str]:
    if flag in {"-e", "--regexp"}:
        return [flag, "needle", "a.txt"]
    return ["needle", flag, "a.txt"]


# rg flags `tg search` does not take as a search option. Each entry carries its reason.
_RG_FLAGS_TG_SEARCH_REJECTS = {
    "--help": "handled by the help passthrough before search parsing, not a search option",
    "--version": "handled by the version short-circuit before search parsing, not a search option",
}


def test_every_rg_documented_flag_is_recognised_by_the_native_root_door() -> None:
    """Static drift arm (J-03): the registry must cover every flag `rg --help` documents.

    The sibling unit test (tests/unit/test_root_door_flag_registry_drift.py) compares against the
    Python door's flag set; this arm needs the real `rg`, so it fails closed when CI demands it.
    """
    import re
    import shutil

    rg = shutil.which("rg")
    if rg is None:
        if os.environ.get("TG_REQUIRE_RG_PARITY", "").strip().lower() in {"1", "true", "yes"}:
            pytest.fail("TG_REQUIRE_RG_PARITY=1 but rg is not installed")
        pytest.skip("rg not installed")
    root = Path(__file__).resolve().parents[2]
    registry_src = (root / "rust_core" / "src" / "search_flag_registry.rs").read_text("utf-8")
    body = registry_src.split("pub(crate) fn raw_args_contain_any_flag")[0]
    known = set(re.findall(r'^\s*"(-{1,2}[\w-]+)",', body, re.M))
    main_src = (root / "rust_core" / "src" / "main.rs").read_text("utf-8")
    struct = re.search(r"pub struct PositionalCli \{(.*?)\n\}\n", main_src, re.S)
    assert struct is not None, "PositionalCli struct not found"
    for fm in re.finditer(r"#\[arg\(([^\]]*)\)\]\s*\n\s*pub (\w+):", struct.group(1)):
        attrs, field = fm.groups()
        known.update("-" + s for s in re.findall(r"short\s*=\s*'(\w)'", attrs))
        known.update("--" + n for n in re.findall(r'(?:long|alias)\s*=\s*"([\w-]+)"', attrs))
        if re.search(r"\blong\b(?!\s*=)", attrs):
            known.add("--" + field.replace("_", "-"))
    known |= set(_RG_FLAGS_TG_SEARCH_REJECTS)
    assert "--glob" in known and "--json" in known, "flag-registry parse looks wrong"

    help_text = subprocess.run(
        [rg, "--help"], capture_output=True, text=True, timeout=30, check=True
    ).stdout
    rg_flags = set(re.findall(r"^ {4}(?:-\w, )?(--[\w-]+)", help_text, re.M))
    rg_flags |= set(re.findall(r"^ {8}(--[\w-]+)", help_text, re.M))
    assert len(rg_flags) > 50, f"rg --help parse looks wrong ({len(rg_flags)} flags)"
    missing = sorted(flag for flag in rg_flags if flag not in known)
    assert not missing, (
        f"rg flags the native root door does not recognise: {missing}. Add them to "
        "SEARCH_OPTION_FIRST_FLAGS (rust_core/src/search_flag_registry.rs), or to "
        "_RG_FLAGS_TG_SEARCH_REJECTS with a reason if `tg search` also rejects them."
    )


@pytest.mark.parametrize("flag", _STRICT_ROOT_FLAGS)
def test_root_door_accepts_search_only_flag_strict(tmp_path: Path, flag: str) -> None:
    tg = _require_native_tg()
    (tmp_path / "a.txt").write_text("needle one\n", encoding="utf-8")
    r = _tg(tg, _root_args(flag), tmp_path)
    assert "unexpected argument" not in r.stderr, (flag, r.stderr)
    assert r.returncode in (0, 1), (flag, r.returncode, r.stderr)


@pytest.mark.parametrize("flag", _LENIENT_ROOT_FLAGS)
def test_root_door_accepts_search_only_flag_lenient(tmp_path: Path, flag: str) -> None:
    tg = _require_native_tg()
    (tmp_path / "a.txt").write_text("needle one\n", encoding="utf-8")
    r = _tg(tg, _root_args(flag), tmp_path)
    assert "unexpected argument" not in r.stderr, (flag, r.stderr)
