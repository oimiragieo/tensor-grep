# Bug-hunt Wave 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the wave-1 findings (security + silent-wrong-result P1s) from the 2026-10-03 bug hunt, in five independent PRs.

**Architecture:** Five Parts (A-E), each one PR touching a disjoint file set, each TDD (RED test that fails on current main for the right reason -> minimal fix -> green). No Part depends on another Part's code.

**Tech Stack:** Python 3.11+, Typer, pytest, uv (`uv run --no-sync`), ruff (check + `format --preview`), mypy (src only). Rust untouched in wave 1.

**Spec:** `docs/audits/2026-10-03-bughunt-tracker.md` (finding IDs, evidence, dispositions).

## Global Constraints

- Every command runs as `uv run --no-sync ...` (plain `uv run` re-syncs away the dev tree-sitter grammars).
- `ruff format --check --preview` is a separate gate from `ruff check`; never pass `--preview` to `ruff check`.
- Local runs are targeted test files only — the named files, never the full suite, benchmarks or cargo (shared server, AGENTS.md A12); full pytest / Rust matrix run in CI.
- Every RED test must be COLLECTABLE on current main and fail on a behavioural assertion: reference new symbols as `module.Symbol` inside the test body (never a module-level `from x import NewSymbol`, which turns RED into an ImportError).
- Backend Fail-Closed Contract: failures raise `BackendExecutionError` or set `result_incomplete`; never return an empty result as if complete.
- CLI output stays ASCII-only.
- New user-controlled values reaching a subprocess argv go after a `--` / `--end-of-options` sentinel (CWE-88).
- A test that pins old behaviour is updated deliberately in the same PR, with the reason in the commit message; never relaxed silently.
- JSON payload changes are additive (new keys only) unless a Part states otherwise.
- `src/tensor_grep/cli/main.py` is size-ratcheted (`scripts/file_size_allowlist.json`): it may shrink, never grow.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- Option-shaped user input (`-x`, `--flag=value`) reaching any `git` / `rg` / `ast-grep` argv (Parts A, C, D).
- Files that are not UTF-8, carry a BOM, or are huge (Parts C, D, E).
- Windows paths: drive letters, backslashes, case-insensitive roots (Parts B, E).
- A legitimate user's working configuration must keep working after the hardening (Part B daemon discovery, Part E installer re-run, Part A refs such as `main..HEAD`).
- Exit codes: 0 = found/ok, 1 = genuinely none, 2 = error/incomplete — no error may surface as 1.

---

## Part A: fix(diff-impact): close git argv injection, parse quoted/space/deleted paths, fail closed on git failure

**Findings closed:** G-01, G-02 (parts 2, 3, 5), B-04, G-07 (fail-on-risk validation + exit_reason).
**Dropped / deferred:**
- G-07 `>` vs `>=` — by design: `--fail-threshold` help says "exceeds", `--fail-on-risk` says "at or above". Pinned by a test so it stays.
- G-07 `--max-repo-files`/`--root` plumbing — needs a new Typer option in size-ratcheted `main.py`; follow-up.
- G-02 parts 1 & 4 (symbols of deleted files / removed symbols in surviving files) — needs old-side extraction (`git show` + extractors on content); separate feature PR (wave 3). This PR surfaces deleted files honestly instead.
- Auditor premise corrected: `git diff -z` does NOT change patch-format output (probed, git 2.55), so `-z` is not used; `-c core.quotepath=false` + explicit C-unquote of header operands instead.

**Risk class:** security (G-01) -> adversarial gate required.

**Files:** Modify `src/tensor_grep/cli/diff_impact.py`. Test `tests/unit/test_diff_impact.py`. `main.py` not touched.

**Seams verified:**
- `diff_impact.py :: extract_diff_hunks_from_git` (105-142): `["git","diff","-U0"]` + `--cached` + raw `ref`, no sentinel, no validation; every failure returns `{}` (deadline 123-125; `OSError/ValueError/TimeoutError` 136-137; `returncode != 0` 139-140). `subprocess.TimeoutExpired` is not builtin `TimeoutError` -> currently escapes uncaught.
- `diff_impact.py :: parse_git_diff_hunks` (36-102): `diff --git a/(.+) b/(.+)` ambiguous with spaces, misses quoted names; `_DIFF_PLUS_FILE_RE` (33) keeps git's trailing TAB on space-containing names; `+++ /dev/null` drops deleted files; any `+++ ` line is treated as a header even inside a hunk body (a removed line `-- x` renders as `--- x`).
- `diff_impact.py :: build_diff_blast_radius` (233-391): empty extract -> "no changes" payload (268-286).
- `diff_impact.py :: diff_impact_command` (394-443): prints payload before computing `breached`; `Exit(2)` for partial-or-breached, `Exit(1)` no files, `Exit(0)` otherwise; unvalidated `fail_on_risk` maps bogus -> rank 1 -> always exit 2.
- `main.py :: diff_impact` (13480-13515) is the only caller. Native `tg.exe` passes the command to Python with `allow_hyphen_values` (rust_core main.rs ~902, ~7390) — which is why the native door rejected `--output=` while `python -m tensor_grep` executed it.
- MCP: no tool calls `build_diff_blast_radius` / `extract_diff_hunks_from_git` / `parse_git_diff_hunks` (only `tg_audit_diff`, `tg_rewrite_diff`, unrelated) -> no MCP surface, no contract-version bump.
- git 2.55 probes: `git diff -U0 --end-of-options HEAD --` works; `git diff --end-of-options --output=x --` -> `fatal: bad revision`, no file; `core.quotepath=false` keeps non-ASCII literal; space names get a trailing TAB on `---`/`+++`.

**Contracts/governance touched:** `tests/unit/test_diff_impact.py::test_parse_git_diff_hunks_basic` asserts a deleted file is `not in parsed` — deliberately updated to `parsed[deleted_path] == []`. Other CLI tests monkeypatch `extract_diff_hunks_from_git` / `build_diff_blast_radius`; signatures kept, new payload keys additive. No `docs/CONTRACTS.md` text for diff-impact (searched CONTRACTS.md, AGENTS.md, docs/*.md).

### Task A.1: ref validation, hardened git argv, `DiffError`, fail-closed extraction

**Files:** Modify `src/tensor_grep/cli/diff_impact.py`; Test `tests/unit/test_diff_impact.py`.
**Interfaces:** Produces `class DiffError(RuntimeError)` with attribute `reason: str` (exported in `__all__`); `extract_diff_hunks_from_git` now raises `DiffError` instead of returning `{}` on failure.

- [ ] **Step 1: Write the failing tests** (append; add `import subprocess`, `import pytest`, and `import tensor_grep.cli.diff_impact as di` — reference `di.DiffError` inside test bodies so the file still collects on main; in the code below read `DiffError` as `di.DiffError`)

```python
def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")


@pytest.mark.parametrize("ref", ["--output=pwned.txt", "-p", "--ext-diff", "a\nb", "a\x00b"])
def test_extract_diff_hunks_rejects_option_like_ref_without_running_git(
    monkeypatch: Any, ref: str
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.run_subprocess", lambda *a, **k: calls.append(a)
    )
    # Council round 5: capture with an EXISTING type first so main fails BEHAVIOURALLY (git was
    # invoked), not on a missing-attribute lookup; only then check the new exception's identity.
    try:
        di.extract_diff_hunks_from_git(ref=ref)
        raised: BaseException | None = None
    except Exception as exc:  # noqa: BLE001 - deliberate: classify after the behavioural check
        raised = exc
    assert calls == [], "an option-like ref must be refused BEFORE git is invoked"
    assert type(raised).__name__ == "DiffError" and getattr(raised, "reason", None) == "invalid_ref"


@pytest.mark.parametrize("ref", ["HEAD~1", "main..HEAD", "main...HEAD"])
def test_extract_diff_hunks_git_argv_is_hardened(monkeypatch: Any, ref: str) -> None:
    seen: list[list[str]] = []

    class P:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.run_subprocess",
        lambda cmd, **k: seen.append(list(cmd)) or P(),
    )
    extract_diff_hunks_from_git(ref=ref, staged=True)
    cmd = seen[0]
    assert cmd[:5] == ["git", "-c", "core.quotepath=false", "-c", "core.fsmonitor=false"]
    for flag in ("--no-ext-diff", "--no-textconv", "--cached"):
        assert flag in cmd
    assert "--no-renames" not in cmd  # council round 5: keep main's rename semantics
    assert cmd[-3:] == ["--end-of-options", ref, "--"]


def test_option_like_ref_never_writes_file_in_real_repo(tmp_path: Path) -> None:
    # Behavioural RED on main: no new symbol needed; main creates pwned.txt.
    _init_repo(tmp_path)
    try:
        extract_diff_hunks_from_git(ref="--output=pwned.txt", root=tmp_path)
    except Exception:  # post-fix: di.DiffError
        pass
    assert not (tmp_path / "pwned.txt").exists()


def test_git_failure_is_incomplete_not_no_changes(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    payload = build_diff_blast_radius(root=tmp_path)  # not a git repo
    assert payload["partial"] is True
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "git_diff_failed"
    assert "git_diff_failed" in payload["downgrade_reasons"]
    monkeypatch.chdir(tmp_path)  # council round 2: never run git in the real repo cwd (RED would write ./x there)
    res = runner.invoke(app, ["diff-impact", "--json", "--", "--output=x"])
    assert not (tmp_path / "x").exists()
    assert res.exit_code == 2
    assert json.loads(res.stdout)["incomplete_reason"] == "invalid_ref"
```

- [ ] **Step 2: Run to verify RED**

Run: `uv run --no-sync pytest tests/unit/test_diff_impact.py -q -k "option_like or hardened or git_failure"`
Expected on main: `test_option_like_ref_never_writes_file_in_real_repo` fails `assert not ...pwned.txt.exists()` (behavioural RED, matches the orchestrator repro); the argv test fails on `cmd[:5]`; the others fail with `AttributeError: module ... has no attribute 'DiffError'` at call time (collection succeeds).

- [ ] **Step 3: Minimal implementation** (`diff_impact.py`)

```python
import subprocess


class DiffError(RuntimeError):
    """git diff could not be computed; the result must be reported incomplete, never empty."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


def _validate_ref(ref: str) -> str:
    if not ref or ref.startswith("-") or any(c in ref for c in "\x00\n\r"):
        raise DiffError("invalid_ref", f"invalid git ref: {ref!r}")
    return ref
```

Replace the body of `extract_diff_hunks_from_git`:

```python
    cmd = [
        # core.fsmonitor=false: a repo-local .git/config (e.g. from an extracted archive) must not
        # get to run an fsmonitor hook during our read (council round 3, defence in depth).
        # No --no-renames (council round 5): it would turn a pure rename into delete-all + add-all,
        # reporting the old path as deleted and the whole new file as changed, where main reports
        # nothing; the header-state parser reads `---/+++` and handles rename diffs as main does.
        "git", "-c", "core.quotepath=false", "-c", "core.fsmonitor=false", "diff", "-U0",
        "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/",
    ]
    if staged:
        cmd.append("--cached")
    if ref:
        cmd += ["--end-of-options", _validate_ref(ref), "--"]
    base_timeout = configured_git_timeout_seconds()
    timeout = deadline_capped_timeout_seconds(base_timeout, deadline_monotonic=deadline_monotonic)
    if timeout is None:
        raise DiffError("deadline_exceeded")
    try:
        proc = run_subprocess(
            cmd, cwd=str(root), stdout=-1, stderr=-1, text=True,
            encoding="utf-8", errors="surrogateescape", timeout_seconds=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DiffError("git_diff_timeout") from exc
    except (OSError, ValueError, TimeoutError) as exc:
        raise DiffError("git_diff_failed", str(exc)) from exc
    if proc.returncode != 0:
        raise DiffError("git_diff_failed", (proc.stderr or "").strip()[:500])
    return parse_git_diff_hunks(proc.stdout or "")
```

Add `"DiffError"` to `__all__`. Add helper `_empty_payload(root, ref, staged, *, partial: bool, reason: str | None = None, error: str | None = None) -> dict` returning the existing zeroed "no changes" dict plus `deleted_files: []`, `result_incomplete: partial`, `incomplete_reason: reason if partial else None`, and when `partial`: `partial: True`, `downgrade_reasons: [reason]`, `error: error`. In `build_diff_blast_radius` wrap the extract call:

```python
        try:
            changed_files_with_lines = extract_diff_hunks_from_git(
                ref=ref, staged=staged, root=root, deadline_monotonic=deadline_monotonic
            )
        except DiffError as exc:
            return _empty_payload(root, ref, staged, partial=True, reason=exc.reason, error=str(exc))
```

and make the existing "no files changed" early return use `_empty_payload(root, ref, staged, partial=False)`.

- [ ] **Step 4: Run** (together with Task A.2, which updates the one pinned test): `uv run --no-sync pytest tests/unit/test_diff_impact.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(diff-impact): reject option-like refs, harden git argv, fail closed on git failure`

### Task A.2: parse quoted/space/deleted paths; match headers only in header state

**Files:** Modify `src/tensor_grep/cli/diff_impact.py`; Test `tests/unit/test_diff_impact.py`.
**Interfaces:** Consumes `_init_repo`/`_git` test helpers from A.1. Produces `_git_header_path(raw: str) -> Path | None`; `parse_git_diff_hunks` maps deleted files to `[]`; payload gains `deleted_files: list[str]`, `result_incomplete: bool`, `incomplete_reason: str | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_handles_spaces_quotes_and_deleted_files() -> None:
    diff = (
        "diff --git a/sp ace.py b/sp ace.py\n--- a/sp ace.py\t\n+++ b/sp ace.py\t\n"
        "@@ -1 +1 @@\n-x\n+--- y\n"
        'diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"\n'
        '--- "a/caf\\303\\251.py"\n+++ "b/caf\\303\\251.py"\n@@ -2,0 +3,2 @@\n+a\n+b\n'
        "diff --git a/lib.py b/lib.py\ndeleted file mode 100644\n"
        "--- a/lib.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed[Path("sp ace.py")] == [(1, 1)]
    assert parsed[Path("café.py")] == [(3, 4)]
    assert parsed[Path("lib.py")] == []


def test_deleted_file_hunk_body_lines_are_never_parsed_as_headers() -> None:
    # Council round 5: removed lines whose CONTENT starts with "-- " / "++ " render as
    # "--- ..." / "+++ ..." inside a deleted file's hunk body; they must not become paths.
    diff = (
        "diff --git a/old.py b/old.py\ndeleted file mode 100644\n"
        "--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n--- x\n-++ y\n"
        "diff --git a/keep.py b/keep.py\n--- a/keep.py\n+++ b/keep.py\n@@ -3,0 +4,1 @@\n+z\n"
    )
    parsed = parse_git_diff_hunks(diff)
    assert parsed == {Path("old.py"): [], Path("keep.py"): [(4, 4)]}


def test_rename_with_edit_maps_to_the_new_path() -> None:
    diff = (
        "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n"
        "--- a/old.py\n+++ b/new.py\n@@ -2,0 +3,1 @@\n+x\n"
    )
    assert parse_git_diff_hunks(diff) == {Path("new.py"): [(3, 3)]}


def test_git_header_path_unquotes_escaped_quote() -> None:
    from tensor_grep.cli.diff_impact import _git_header_path

    assert _git_header_path('"a/q\\"x.py"') == Path('q"x.py')
    assert _git_header_path("/dev/null") is None


def test_real_repo_spaces_nonascii_and_deleted(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (tmp_path / "café.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (tmp_path / "sp ace.py").write_text("def b():\n    return 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("from lib import helper\nhelper()\n", encoding="utf-8")
    _git(tmp_path, "add", "--", "lib.py", "café.py", "sp ace.py", "app.py")
    _git(tmp_path, "commit", "-qm", "i")
    (tmp_path / "café.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    (tmp_path / "sp ace.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    (tmp_path / "lib.py").unlink()
    payload = build_diff_blast_radius(root=tmp_path)
    assert payload["changed_files"] == ["café.py", "lib.py", "sp ace.py"]
    assert {s["name"] for s in payload["changed_symbols"]} == {"a", "b"}
    assert payload["deleted_files"] == ["lib.py"]
    assert "deleted_files_symbols_not_analyzed" in payload["downgrade_reasons"]
    assert payload["partial"] is False  # orchestrator decision, see below
```

Update `test_parse_git_diff_hunks_basic`: replace `assert deleted_path not in parsed` with `assert parsed[deleted_path] == []`.

**Orchestrator decision (deleted files):** deleted files are LISTED (`deleted_files`) and add the downgrade reason `deleted_files_symbols_not_analyzed`, but do NOT set `partial`. Rationale: `partial` drives exit 2, the CI fail code; making every deletion-bearing diff exit 2 would break existing CI gates. The honest-lower-bound signal is the downgrade reason plus the list; old-side analysis is the wave-3 follow-up.

- [ ] **Step 2: Run to verify RED**

Run: `uv run --no-sync pytest tests/unit/test_diff_impact.py -q -k "spaces or header_path or real_repo"`
Expected RED (behavioural): `KeyError: ...'sp ace.py'` (or the `'sp ace.py\t'` key) and `lib.py` missing from `changed_files`. The `_git_header_path` unit test's `ImportError` is helper coverage, not RED (council round 13).

- [ ] **Step 3: Minimal implementation** — delete `_DIFF_GIT_FILE_RE` and `_DIFF_PLUS_FILE_RE`; add:

```python
_C_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}


def _git_header_path(raw: str) -> Path | None:
    """Decode a ---/+++ header operand ('/dev/null' -> None, quoted C-string -> text)."""
    if raw == "/dev/null":
        return None
    if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
        body, out, i = raw[1:-1], bytearray(), 0
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                if nxt in "01234567":
                    j = i + 1
                    while j < len(body) and j < i + 4 and body[j] in "01234567":
                        j += 1
                    out.append(int(body[i + 1 : j], 8) & 0xFF)
                    i = j
                    continue
                if nxt in _C_ESCAPES:
                    out.append(_C_ESCAPES[nxt])
                    i += 2
                    continue
            out.extend(ch.encode("utf-8", errors="surrogateescape"))
            i += 1
        raw = out.decode("utf-8", errors="surrogateescape")
    elif raw.endswith("\t"):
        raw = raw[:-1]
    return Path(raw[2:]) if raw[:2] in ("a/", "b/") else Path(raw)
```

Rewrite the `parse_git_diff_hunks` loop as a header-state machine: a `diff --git ` line sets `in_header=True`, `old_path=None`, `current_file=None`; while `in_header`, `--- X` sets `old_path = _git_header_path(X)`; while `in_header`, `+++ X` sets `new = _git_header_path(X)` -> if `new is None`, `result.setdefault(old_path, [])` (when `old_path` is set) and `current_file=None`, else `current_file = new`; ANY `@@ ` line sets `in_header=False` unconditionally (council round 5: headers never follow a hunk; gating on `current_file` left a DELETED file's hunk body in header state, so removed lines `-- x` / `++ y` rendered as `--- x` / `+++ y` were parsed as headers) and, when a `current_file` is set, appends ranges with the existing range logic. `---`/`+++` are only recognised while `in_header`. Docstring: "Deleted files map to an empty range list."

In `build_diff_blast_radius`, after parsing:

```python
    deleted_files = sorted(
        str(p).replace("\\", "/") for p, ranges in changed_files_with_lines.items() if not ranges
    )
    if deleted_files:
        downgrade_reasons.append("deleted_files_symbols_not_analyzed")
```

Add `"deleted_files": deleted_files`, `"result_incomplete": partial`, `"incomplete_reason": (partial_reasons[0] if partial and partial_reasons else None)` to the main return payload, where `partial_reasons` is a new local list that receives ONLY the reasons that set `partial = True` (e.g. `repo_map_scan_incomplete`) — council round 3: `downgrade_reasons[0]` could be the non-partial `deleted_files_symbols_not_analyzed`, mislabelling the incomplete reason. Documented limitation (PR body + docstring): a deleted BINARY or EMPTY file has no `---`/`+++` header lines, so it does not appear in `deleted_files`. The no-change early return checks `not changed_files_with_lines` (deleted files now count as changes).

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_diff_impact.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(diff-impact): parse quoted, space and deleted paths; match file headers only in header state`

### Task A.3: `--fail-on-risk` validation + `exit_reason` / `gate_breached`

**Files:** Modify `src/tensor_grep/cli/diff_impact.py :: diff_impact_command`; Test `tests/unit/test_diff_impact.py`.
**Interfaces:** Payload gains `gate_breached: bool`, `exit_reason: "incomplete" | "gate_breached" | "no_changes" | "ok"`. Exit codes UNCHANGED (0/1/2).

- [ ] **Step 1: Write the failing tests**

```python
def test_cli_diff_impact_bogus_fail_on_risk_is_usage_error(monkeypatch: Any) -> None:
    monkeypatch.setattr("tensor_grep.cli.diff_impact.extract_diff_hunks_from_git", lambda **k: {})
    res = runner.invoke(app, ["diff-impact", "--fail-on-risk", "bogus"])
    assert res.exit_code == 2
    # click 8.4.2 (uv.lock) separates streams; the error is echoed to stderr (council round 2)
    assert "fail-on-risk" in (res.stdout or "") + (res.stderr or "")


def _payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "root": ".", "ref": None, "staged": False, "changed_files": ["a.py"],
        "changed_symbols": [], "callers": [], "affected_files": ["a.py"],
        "affected_tests": [], "blast_radius_score": 0.5, "risk_tier": "medium",
        "partial": False, "downgrade_reasons": [], "symbol_count": 0,
        "caller_count": 0, "file_count": 1, "test_count": 0,
    }
    base.update(overrides)
    return base


def test_cli_diff_impact_exit_reason_and_strict_threshold(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.build_diff_blast_radius", lambda **k: _payload()
    )
    equal = runner.invoke(app, ["diff-impact", "--json", "--fail-threshold", "0.5"])
    assert equal.exit_code == 0  # strict '>' per help text ("exceeds")
    assert json.loads(equal.stdout)["exit_reason"] == "ok"
    breach = runner.invoke(app, ["diff-impact", "--json", "--fail-on-risk", "medium"])
    assert breach.exit_code == 2
    assert json.loads(breach.stdout)["exit_reason"] == "gate_breached"


def test_cli_diff_impact_exit_reason_incomplete(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "tensor_grep.cli.diff_impact.build_diff_blast_radius",
        lambda **k: _payload(partial=True, downgrade_reasons=["git_diff_failed"]),
    )
    res = runner.invoke(app, ["diff-impact", "--json"])
    assert res.exit_code == 2
    assert json.loads(res.stdout)["exit_reason"] == "incomplete"
```

- [ ] **Step 2: Run to verify RED** `uv run --no-sync pytest tests/unit/test_diff_impact.py -q -k "bogus or exit_reason"` -> the bogus test fails on the `"fail-on-risk" in output` assertion (exit code 2 matches by accident today); the others fail with `KeyError: 'exit_reason'`.

- [ ] **Step 3: Minimal implementation** in `diff_impact_command`, before building/printing:

```python
    risk_rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    if fail_on_risk is not None and fail_on_risk.lower() not in risk_rank:
        typer.echo("Error: --fail-on-risk must be one of low, medium, high, critical", err=True)
        raise typer.Exit(2)
```

Compute `breached` and `incomplete = bool(payload.get("partial") or repo_map._scan_did_not_finish(payload))` BEFORE printing; set `payload["gate_breached"] = breached` and `payload["exit_reason"]` = first of `"incomplete"` (incomplete), `"gate_breached"` (breached), `"no_changes"` (no changed files), `"ok"`. Keep exit codes as today. Text mode: when `payload.get("result_incomplete")`, echo `Diff impact INCOMPLETE: {incomplete_reason}` to stderr.

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_diff_impact.py -q` and `uv run --no-sync pytest tests/e2e/test_routing_parity.py -q -k diff-impact` -> PASS.
- [ ] **Step 5: Commit** `fix(diff-impact): validate --fail-on-risk, add exit_reason and gate_breached`

### Part A verification gates

```
uv run --no-sync ruff check src/tensor_grep/cli/diff_impact.py tests/unit/test_diff_impact.py
uv run --no-sync ruff format --check --preview src/tensor_grep/cli/diff_impact.py tests/unit/test_diff_impact.py
uv run --no-sync mypy src/tensor_grep
uv run --no-sync pytest tests/unit/test_diff_impact.py -q
uv run --no-sync pytest tests/e2e/test_routing_parity.py -q -k diff-impact
```

Dogfood (source front door): `python -m tensor_grep diff-impact --json -- --output=pwned.txt` -> exit 2, `incomplete_reason: invalid_ref`, no file created.

### Part A out of scope

Old-side symbol lookup for deleted/removed symbols (wave 3); `--max-repo-files`/`--root` plumbing; splitting exit 2 into separate gate/incomplete codes (breaking for CI users — `exit_reason` is the additive disambiguator); B-08 schema_version.

---
## Part B: fix(session-daemon): authenticate the daemon endpoint, detect added files in empty sessions, disclose rebuild failures

**Findings closed:** F-01, F-02, F-03 (auditor probes `scratch-F/probe1.py`, `probe3.py`, `probe4.py` match the code read). **Dropped:** none. F-03 refined: the failure IS returned as structured JSON (outer handler) but the trigger is lost and the code is mislabeled `invalid_request`.
**Risk class:** security (F-01) -> adversarial gate required; F-02/F-03 correctness.

**Files:** Modify `src/tensor_grep/cli/session_daemon.py`, `src/tensor_grep/cli/session_store.py` (one line), `tests/unit/test_session_cli.py` (stale comment ~949), `tests/conftest.py` (session-scoped env), `docs/CONTRACTS.md` (one sentence). Create `tests/unit/test_session_daemon_endpoint_trust.py`, `tests/unit/test_session_store_empty_snapshot.py`, `tests/unit/test_session_daemon_refresh_failed.py`. `freshness.py` needs no edit (`freshness.py :: _session_status` (26) delegates to `session_store._ensure_session_not_stale(payload, detect_added_files=True)`).

**Seams verified:**
- `session_daemon.py :: _probe_daemon` (569) reads `daemon.json`, pings `metadata.get("host")` with the file's token, trusts `response.get("ok")` + public `package_version`.
- `session_daemon.py :: _daemon_request` (517) connects to any host. All clients go through it: `_merge_live_daemon_stats` (598), `stop_session_daemon` (861), `request_session_daemon` (934), `request_running_session_daemon` (949).
- `_SessionDaemonHandler.handle` (1815): `ping` branch (1880) returns only `{"version","ok"}`; rebuild block 1948-1983; outer `except Exception` (2021) emits `invalid_request`.
- `run_session_daemon_server` (2079) writes `daemon.json` (token, pid, root). Windows ACL path `_write_daemon_metadata` (208) / `_write_daemon_metadata_windows` (233) / `_restrict_windows_file_to_current_user` (294) — untouched.
- `session_store.py :: _stale_changeset` (509): `if not snapshot: return None` at 513 runs before the `detect_added_files` branch. Callers 659 (`_ensure_session_not_stale`), 816 (`detect_added_files=True`), 1017 (`False`).

**Design rationale (F-01).** The attacker writes `daemon.json`, so they know the token and port; echoing pid/root is forgeable. The only unforgeable proof is a secret that never lives in the repo: (1) refuse non-loopback hosts; (2) the client sends a fresh nonce and the daemon must return `HMAC(per-user secret, nonce|pid|root)`, where the per-user secret lives in the user's state dir (`%LOCALAPPDATA%\tensor-grep\` / `$XDG_STATE_HOME/tensor-grep/`), owner-only. A repo-planted fake cannot produce it. Moving `daemon.json` itself out of the repo is a follow-up (`_daemon_metadata_path` is used in 25 files and discovery walks `daemon.json`); after this PR a planted file can at most cause one ~0.5s loopback connect attempt, never forged results or remote exfiltration.

**Contracts/governance touched:** `tests/unit/test_cli_atomic_writer_ratchet.py` (only helper-backed `_write_json_atomic` used; run it). Real-daemon tests `test_session_daemon_version_skew.py`, `test_symbol_daemon_autostart.py`, `test_session_daemon_metadata_ownership.py`, `test_session_daemon_security.py` keep working via the session-scoped secret-dir fixture; they write `pid=0` in metadata, so the proof binds the REPLY's pid/root, not metadata pid. `docs/CONTRACTS.md` line ~100 (added-file discovery) stays true. `test_session_cli.py` ~949 comment becomes false -> reworded. `test_freshness.py` mocks `_ensure_session_not_stale` -> unaffected.

### Task B.1: F-01 endpoint trust

**Interfaces:** Produces in `session_daemon.py`: `_is_loopback_host(host: object) -> bool`, `_daemon_secret_path() -> Path`, `_read_user_secret(path: Path) -> bytes | None`, `_load_or_create_user_secret() -> bytes | None`, `_daemon_ping_proof(secret: bytes, nonce: str, pid: int, root: str, port: int) -> str`, `_verify_ping_reply(response: dict, nonce: str, root: Path, connected_port: int) -> bool`. Env override `TG_DAEMON_SECRET_DIR` (test hook only).

**Relay defence (council round 1):** the proof binds the daemon's OWN listening port, and the client accepts it only if that port equals the port it connected to. A loopback relay that forwards the nonce to the genuine daemon gets a proof for the genuine port, not the relay's, and is rejected; every later request goes to the connected (verified) port. **Secret-path stability (council round 1):** the path depends only on `LOCALAPPDATA` (Windows) or `Path.home()` (POSIX) — no `XDG_STATE_HOME` — so client and daemon (spawned with the client's environment) always agree; a mismatch can only cause one cold fallback + respawn, never a wrong answer.

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_session_daemon_endpoint_trust.py`:

```python
from __future__ import annotations

import json
import os
import socket
import socketserver
import sys
import threading
from pathlib import Path
from typing import Any, Callable

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli.runtime_paths import _expected_tg_version


@pytest.fixture(autouse=True)
def _secret_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))


class _Fake:
    def __init__(self, reply: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        class H(socketserver.StreamRequestHandler):
            def handle(self_inner) -> None:
                req = json.loads(self_inner.rfile.readline())
                self_inner.wfile.write((json.dumps(reply(req)) + "\n").encode())

        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.port = int(self.srv.server_address[1])
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


def _plant(root: Path, *, host: str = "127.0.0.1", port: int) -> None:
    sd._write_daemon_metadata(root, {
        "version": 1, "package_version": _expected_tg_version(), "root": str(root),
        "host": host, "port": port, "pid": 1, "started_at": "x", "token": "attacker",
    })


def test_forged_ok_reply_is_rejected(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    fake = _Fake(lambda req: {"ok": True})
    try:
        _plant(root, port=fake.port)
        assert sd._probe_daemon(root) is None
    finally:
        fake.close()


def test_forged_proof_with_wrong_secret_is_rejected(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    sd._load_or_create_user_secret()

    holder: dict[str, int] = {}

    def reply(req: dict[str, Any]) -> dict[str, Any]:
        port = holder["port"]
        proof = sd._daemon_ping_proof(b"x" * 32, req["nonce"], 1, str(root), port)
        return {"ok": True, "pid": 1, "root": str(root), "port": port, "proof": proof}

    fake = _Fake(reply)
    holder["port"] = fake.port
    try:
        _plant(root, port=fake.port)
        assert sd._probe_daemon(root) is None
    finally:
        fake.close()


def test_non_loopback_host_is_never_connected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Record calls instead of raising: _probe_daemon swallows exceptions, so a raising canary
    # would pass on broken code (vacuous). Recording proves the request was never attempted.
    root = tmp_path.resolve()
    _plant(root, host="10.255.255.1", port=9)
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(sd, "_daemon_request", lambda *a, **k: calls.append(a) or {"ok": True})
    assert sd._probe_daemon(root) is None
    assert calls == []


def test_relayed_proof_for_a_different_port_is_rejected(tmp_path: Path) -> None:
    # A loopback relay forwards the client's nonce to the GENUINE daemon and returns its valid
    # proof. The proof binds the genuine daemon's own listening port, which differs from the
    # relay's port the client connected to -> must be rejected.
    root = tmp_path.resolve()
    secret = sd._load_or_create_user_secret()
    assert secret is not None
    genuine_port = 1  # any port != the relay's

    def reply(req: dict[str, Any]) -> dict[str, Any]:
        proof = sd._daemon_ping_proof(secret, req["nonce"], 1, str(root), genuine_port)
        return {"ok": True, "pid": 1, "root": str(root), "port": genuine_port, "proof": proof}

    relay = _Fake(reply)
    try:
        _plant(root, port=relay.port)
        assert sd._probe_daemon(root) is None
    finally:
        relay.close()


@pytest.mark.parametrize("host", ["example.com", "10.0.0.1", "127.0.0.2", "::1", "localhost"])
def test_daemon_request_refuses_any_host_but_the_canonical_bind(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    # Record instead of connecting: RED on main must not make a real outbound connection (council round 2).
    calls: list[Any] = []

    def _record(*a: Any, **k: Any) -> Any:
        calls.append(a)
        raise OSError("blocked by test")

    monkeypatch.setattr(socket, "create_connection", _record)
    with pytest.raises(ValueError):
        sd._daemon_request(host, 80, {"command": "ping"})
    assert calls == []


def test_same_port_relay_on_another_loopback_address_is_never_contacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Council round 2: a relay on 127.0.0.2 at the GENUINE daemon's port would carry a valid
    # port-bound proof; only the canonical bind host 127.0.0.1 is ever connected to.
    # Council round 3: record calls (a refused connection is swallowed by _probe_daemon, so
    # asserting only `is None` would be vacuous).
    root = tmp_path.resolve()
    _plant(root, host="127.0.0.2", port=45678)
    calls: list[Any] = []
    monkeypatch.setattr(sd, "_daemon_request", lambda *a, **k: calls.append(a) or {"ok": True})
    assert sd._probe_daemon(root) is None
    assert calls == []


def test_malformed_planted_port_is_rejected_not_crash(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    sd._write_daemon_metadata(root, {
        "version": 1, "package_version": _expected_tg_version(), "root": str(root),
        "host": "127.0.0.1", "port": "not-a-port", "pid": 1, "started_at": "x", "token": "t",
    })
    assert sd._probe_daemon(root) is None


def test_genuine_daemon_is_still_accepted(tmp_path: Path) -> None:
    # POSITIVE CONTROL: without it the tests above would pass against a probe that rejects everything.
    root = tmp_path.resolve()
    server = sd._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        sd._write_daemon_metadata(root, {
            "version": 1, "package_version": _expected_tg_version(), "root": str(root),
            "host": "127.0.0.1", "port": int(server.server_address[1]), "pid": 0,
            "started_at": "x", "token": "tok",
        })
        assert sd._probe_daemon(root) is not None
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_group_readable_secret_is_not_trusted(tmp_path: Path) -> None:
    sd._load_or_create_user_secret()
    path = sd._daemon_secret_path()
    os.chmod(path, 0o640)
    assert sd._read_user_secret(path) is None


def test_symlinked_secret_is_not_trusted(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"secret": "a" * 64}), encoding="utf-8")
    link = sd._daemon_secret_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")
    assert sd._read_user_secret(link) is None
```

- [ ] **Step 2: Run to verify RED** `uv run --no-sync pytest tests/unit/test_session_daemon_endpoint_trust.py -q` -> `test_forged_ok_reply_is_rejected` fails `assert {...} is None` (probe returns the planted metadata = the F-01 bug); others fail `AttributeError: ... '_load_or_create_user_secret'` / `DID NOT RAISE`.

- [ ] **Step 3: Minimal implementation** (`session_daemon.py`; add `import stat as _stat`; confirm `_DAEMON_HOST == "127.0.0.1"` and that `run_session_daemon_server` binds and writes exactly that host (verified by council round 2 at ~2084/2098); `hmac`, `hashlib`, `secrets` imports if not present):

```python
_DAEMON_SECRET_DIR_ENV = "TG_DAEMON_SECRET_DIR"
_DAEMON_SECRET_FILE = "daemon-secret.json"


def _is_loopback_host(host: object) -> bool:
    # Council round 2: "any loopback" is not enough -- a relay on 127.0.0.2 can share the genuine
    # daemon's port. The daemon always binds _DAEMON_HOST ("127.0.0.1"), so accept exactly that.
    return str(host) == _DAEMON_HOST


def _daemon_secret_path() -> Path:
    override = os.environ.get(_DAEMON_SECRET_DIR_ENV)
    if override:
        return Path(override).expanduser() / _DAEMON_SECRET_FILE
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "tensor-grep" / _DAEMON_SECRET_FILE
    return Path.home() / ".local" / "state" / "tensor-grep" / _DAEMON_SECRET_FILE


def _read_user_secret(path: Path) -> bytes | None:
    try:
        st = path.lstat()  # lstat: a symlinked secret is not a regular file
        if not _stat.S_ISREG(st.st_mode):
            return None
        if sys.platform != "win32" and (st.st_uid != os.geteuid() or st.st_mode & 0o077):
            return None
        raw = json.loads(path.read_text(encoding="utf-8")).get("secret")
    except (OSError, ValueError, AttributeError):
        return None
    return raw.encode("ascii") if isinstance(raw, str) and len(raw) >= 32 and raw.isascii() else None


def _load_or_create_user_secret() -> bytes | None:
    path = _daemon_secret_path()
    existing = _read_user_secret(path)
    if existing is not None:
        return existing
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {"secret": secrets.token_hex(32)}
        if sys.platform == "win32":
            # Council round 4: never write-then-lock (the ordering audit #81 #13 removed);
            # reuse the existing lock-before-content Windows writer used for daemon.json.
            _write_daemon_metadata_windows(path, payload)
        else:
            _write_json_atomic(path, payload, mode=_DAEMON_METADATA_MODE)
    except OSError:
        return None
    return _read_user_secret(path)


def _daemon_ping_proof(secret: bytes, nonce: str, pid: int, root: str, port: int) -> str:
    msg = "\n".join(("tg-daemon-ping-v1", nonce, str(pid), root, str(port))).encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def _verify_ping_reply(response: dict[str, Any], nonce: str, root: Path, connected_port: int) -> bool:
    secret = _read_user_secret(_daemon_secret_path())
    pid, reply_root = response.get("pid"), response.get("root")
    port, proof = response.get("port"), response.get("proof")
    if secret is None:
        return False
    for value in (pid, port):
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    if port != connected_port:  # relay defence: the proof must be for the endpoint we connected to
        return False
    if not isinstance(reply_root, str) or not isinstance(proof, str) or not proof.isascii():
        return False
    if os.path.normcase(reply_root) != os.path.normcase(str(root)):
        return False
    expected = _daemon_ping_proof(secret, nonce, pid, reply_root, port)
    return hmac.compare_digest(proof.encode("ascii"), expected.encode("ascii"))
```

(`proof.isascii()` + bytes comparison: `hmac.compare_digest(str, str)` raises `TypeError` on non-ASCII — the 2026-10-02 chain-integrity receipt.)

- `_daemon_request`: first line `if not _is_loopback_host(host): raise ValueError(f"refusing non-loopback session daemon host: {host!r}")`.
- `_probe_daemon`: before connecting, `host = metadata.get("host", _DAEMON_HOST)`; `if not _is_loopback_host(host): return None`; `nonce = secrets.token_hex(16)`; ping payload `{"command": "ping", "nonce": nonce}`; after `if not response.get("ok"): return None` add `if not _verify_ping_reply(response, nonce, root, connected_port): return None` (the port the request was actually sent to), where BEFORE connecting `_probe_daemon` computes `try: connected_port = int(metadata["port"]) except (KeyError, TypeError, ValueError): return None` (council round 3: a malformed planted port must be rejected, not raise) and uses `connected_port` for the request itself. `_write_json_atomic` is today only a function-local import inside `_write_daemon_metadata` (~209) — add a module-level import of it for `_load_or_create_user_secret` (council round 3). The `package_version` check stays last.
- Ping branch in `handle`:

```python
response = {"version": _SESSION_VERSION, "ok": True}
nonce = request.get("nonce")
if isinstance(nonce, str) and 16 <= len(nonce) <= 64 and nonce.isascii():
    secret = _load_or_create_user_secret()
    if secret is not None:
        own_port = int(server.server_address[1])
        response.update(
            pid=os.getpid(),
            root=str(server.root),
            port=own_port,
            proof=_daemon_ping_proof(secret, nonce, os.getpid(), str(server.root), own_port),
        )
```

- `run_session_daemon_server`: before binding, `if _load_or_create_user_secret() is None: raise RuntimeError("cannot establish per-user daemon secret; refusing to serve")` (clients then take the existing cold path).
- `tests/conftest.py`: session-scoped autouse fixture that sets `os.environ["TG_DAEMON_SECRET_DIR"]` to `tmp_path_factory.mktemp("tg-secret")` via `os.environ.setdefault` and pops it on teardown only if it set it (not `monkeypatch`; see the existing conftest docstring ~110 for why).

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_session_daemon_endpoint_trust.py tests/unit/test_session_daemon_security.py tests/unit/test_session_daemon_version_skew.py tests/unit/test_session_daemon_metadata_ownership.py tests/unit/test_session_daemon_stop_honesty.py tests/unit/test_symbol_daemon_autostart.py tests/unit/test_cli_atomic_writer_ratchet.py tests/unit/test_session_cli.py tests/unit/test_session_daemon_metrics.py tests/unit/test_orient_agent_daemon.py tests/unit/test_symbol_daemon_response_cache.py -q` -> PASS (the last four drive `_daemon_request` / real servers — council round 4). (Verified: `session_daemon.py:233 _write_daemon_metadata_windows(path: Path, payload: dict)` is path-based — reuse it directly; `_write_json_atomic` lives in `session_store.py:398`, import it from there at module level.)
- [ ] **Step 5: Commit** `fix(session-daemon): refuse non-loopback hosts and require a per-user HMAC ping proof before trusting daemon.json`

### Task B.2: F-02 empty-snapshot staleness

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_session_store_empty_snapshot.py`:

```python
from pathlib import Path

import pytest

from tensor_grep.cli import session_store as ss
from tensor_grep.cli.freshness import check_freshness


def _open_empty(tmp_path: Path):
    root = tmp_path.resolve()
    (root / ".git").mkdir()
    opened = ss.open_session(str(root))
    return root, ss.get_session(opened.session_id, str(root))


def test_added_file_makes_an_empty_session_stale(tmp_path: Path) -> None:
    root, payload = _open_empty(tmp_path)
    assert not payload.get("snapshot")
    (root / "new_mod.py").write_text("def brand_new():\n    pass\n", encoding="utf-8")
    with pytest.raises(ss.SessionStaleError):
        ss._ensure_session_not_stale(payload, detect_added_files=True)
    assert check_freshness(root)["stale_count"] == 1


def test_empty_session_default_path_stays_cheap(tmp_path: Path) -> None:
    root, payload = _open_empty(tmp_path)
    (root / "new_mod.py").write_text("x = 1\n", encoding="utf-8")
    ss._ensure_session_not_stale(payload)  # detect_added_files=False must not walk or raise


def test_untouched_empty_session_is_current(tmp_path: Path) -> None:
    root, _ = _open_empty(tmp_path)
    assert check_freshness(root)["stale_count"] == 0
```

- [ ] **Step 2: Run to verify RED** `uv run --no-sync pytest tests/unit/test_session_store_empty_snapshot.py -q` -> first test `DID NOT RAISE SessionStaleError`.
- [ ] **Step 3: Minimal implementation** — `session_store.py` line 513: `if not snapshot and not detect_added_files: return None`. Reword the `test_session_cli.py` ~949 comment (non-empty snapshot no longer required). `docs/CONTRACTS.md` (~100): add "A session opened on a repo with zero context files is still subject to added-file detection on the explicit paths."
- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_session_store_empty_snapshot.py tests/unit/test_freshness.py tests/unit/test_session_cli.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(session): detect added files in sessions opened on an empty repo`

### Task B.3: F-03 `refresh_failed` disclosure

- [ ] **Step 1: Write the failing tests.** The existing helper (verified, `tests/unit/test_session_daemon_refresh_disclosure.py:25`) is `_drive(tmp_path, monkeypatch, *, first_attempt_raises, refresh_on_stale=True)`; it patches `_serve_daemon_response_with_cache` with a fake that raises `first_attempt_raises` on attempt 1, and patches `session_daemon.refresh_session` to a no-op. The new test file defines this EXTENDED copy of it (see the importlib note below — the original stays untouched):

```python
def _drive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    first_attempt_raises: Exception | None,
    refresh_on_stale: bool = True,
    second_attempt_raises: Exception | None = None,
    refresh_session_impl: Any = None,
) -> dict[str, Any]:
    ...
    def _fake_serve(**_kwargs: Any) -> tuple[dict[str, Any], str]:
        attempts["count"] += 1
        if attempts["count"] == 1 and first_attempt_raises is not None:
            raise first_attempt_raises
        if attempts["count"] == 2 and second_attempt_raises is not None:
            raise second_attempt_raises
        return {"session_id": opened["session_id"], "routing_reason": "test"}, "bypass"

    monkeypatch.setattr(session_daemon, "_serve_daemon_response_with_cache", _fake_serve)
    monkeypatch.setattr(
        session_daemon, "refresh_session",
        refresh_session_impl if refresh_session_impl is not None else (lambda *a, **k: {}),
    )
    ...  # rest unchanged
```

**Council round 2 (verified):** pytest runs with `--import-mode=importlib` (pyproject.toml:50) and there is no `tests/__init__.py` / `tests/unit/__init__.py`, so one test module cannot import another. Therefore do NOT extend the existing `_drive`; instead define the extended helper (the code block above, named `_drive`) directly inside the NEW test file — copying the existing helper's body verbatim and adding the two keyword-only params. The existing `test_session_daemon_refresh_disclosure.py` is left untouched.

Create `tests/unit/test_session_daemon_refresh_failed.py` (imports: `json`, `threading`, `Path`, `Any`, `pytest`, `CliRunner`, `from tensor_grep.cli import session_daemon`, `from tensor_grep.cli.main import app`; then the extended `_drive` above, then):

```python
from pathlib import Path

import pytest


def _raise(exc: BaseException):
    def _f(*a, **k):
        raise exc

    return _f


def test_rebuild_failure_discloses_trigger_and_both_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    err = _drive(
        tmp_path, monkeypatch, first_attempt_raises=KeyError("orig"),
        refresh_session_impl=_raise(OSError("disk gone")),
    )["error"]
    assert err["code"] == "refresh_failed"
    assert err["refresh_trigger"] == "KeyError"
    assert "orig" in err["original_error"]
    assert "disk gone" in err["rebuild_error"]


def test_failure_of_the_post_rebuild_serve_is_also_refresh_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    err = _drive(
        tmp_path, monkeypatch, first_attempt_raises=KeyError("orig"),
        second_attempt_raises=RuntimeError("serve again failed"),
    )["error"]
    assert err["code"] == "refresh_failed"
    assert "serve again failed" in err["rebuild_error"]


def test_without_refresh_on_stale_error_code_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    err = _drive(tmp_path, monkeypatch, first_attempt_raises=KeyError("orig"), refresh_on_stale=False)["error"]
    assert err["code"] == "invalid_request"
```

(`_drive` here is the file-local extended helper defined above in the same file.)

- [ ] **Step 2: Run to verify RED** -> first two fail `assert 'invalid_request' == 'refresh_failed'`.
- [ ] **Step 3: Minimal implementation** — module level:

```python
class _DaemonRefreshFailed(Exception):
    def __init__(self, trigger: str, original_error: str, rebuild_error: str) -> None:
        super().__init__(f"rebuild after {trigger} failed: {rebuild_error}")
        self.trigger = trigger
        self.original_error = original_error
        self.rebuild_error = rebuild_error
```

In the inner `except Exception as exc:` of `handle`, record `original_error = str(exc)` beside `refresh_trigger`. Wrap from `refresh_session(...)` through `served_at = monotonic()` in `try:` ... `except Exception as rebuild_exc: raise _DaemonRefreshFailed(refresh_trigger, original_error, str(rebuild_exc)) from rebuild_exc`. Before the outer `except Exception as exc:` add:

```python
        except _DaemonRefreshFailed as failed:
            response = {
                "version": _SESSION_VERSION,
                "session_id": request_session_id,
                "error": {
                    "code": "refresh_failed",
                    "message": str(failed),
                    "refresh_trigger": failed.trigger,
                    "original_error": failed.original_error,
                    "rebuild_error": failed.rebuild_error,
                },
            }
```

Extra keys are inside `error` -> additive. The `finally` still decrements `inflight_requests`.

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_session_daemon_refresh_failed.py tests/unit/test_session_daemon_refresh_disclosure.py tests/unit/test_session_serve.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(session-daemon): report refresh_failed with the original trigger when a stale rebuild fails`

### Part B review focus (pinned)

- Pre-upgrade daemons return no `proof` -> probe returns None -> existing respawn path (pinned by `test_forged_ok_reply_is_rejected`, a proof-less reply).
- Secret file symlinked or group-readable -> untrusted (pinned: `test_symlinked_secret_is_not_trusted`, `test_group_readable_secret_is_not_trusted`).
- Concurrent first start: two daemons may both create the secret; every proof reads the file fresh, so they converge; a lost race costs one cold fallback.
- Trust root: an attacker running as the same uid defeats the secret — explicitly out of the repo-planted-file threat model.

### Part B verification gates

```
uv run --no-sync ruff check src/tensor_grep/cli/session_daemon.py src/tensor_grep/cli/session_store.py tests/conftest.py tests/unit/test_session_daemon_endpoint_trust.py tests/unit/test_session_store_empty_snapshot.py tests/unit/test_session_daemon_refresh_failed.py
uv run --no-sync ruff format --check --preview <same files>
uv run --no-sync mypy src/tensor_grep
uv run --no-sync pytest <each task's list> tests/unit/test_handler_dispositions.py -q
```

### Part B out of scope

Moving daemon metadata out of the repo (follow-up; discovery redesign); F-07 pid-kill trust; F-04..F-06, F-08..F-12.

---
## Part C: fix(mcp): bound blast-radius depth and search output, confine artifact writes, fail closed on malformed AST patterns

**Findings closed:** E-01, E-02, E-03, E-04. **Dropped:** none. Audit corrections: (1) raising `BackendExecutionError` from `_raise_for_nonzero` would NOT yield `invalid_input` — `mcp_server.py :: tg_ast_search` (3565-3597) catches it per file and downgrades to a generic `result_incomplete`; (2) `tg run -p 'def ('` on current main prints "No AST matches found" and exits 1 (`ast_workflows.py :: run_command` 413-433), so a backend-level raise would change CLI exit codes -> MCP-only preflight instead; CLI unchanged.
**Risk class:** security (E-02 arbitrary in-root overwrite) -> adversarial gate required; E-01/E-03/E-04 robustness/correctness.

**Files:** Modify `src/tensor_grep/cli/repo_map.py`, `src/tensor_grep/cli/mcp_server.py`, `src/tensor_grep/cli/mcp_audit_tools.py`, `src/tensor_grep/backends/ast_wrapper_backend.py`. Version pins: `tests/integration/test_mcp_stdio_protocol.py` (60, 142), `tests/unit/test_mcp_passthrough_wire_surface.py` (50), `tests/unit/test_mcp_server_meta_dispatch.py` (64), `docs/architecture.md` (193). Tests: `tests/unit/test_cli_modes_blast_radius.py`, `tests/unit/test_mcp_server_path_confinement.py`, `tests/unit/test_mcp_server_search.py`, `tests/unit/test_ast_wrapper_backend.py`.

**Seams verified:**
- `repo_map.py :: build_symbol_blast_radius_from_map` (14431): `normalized_depth = max(0, int(max_depth))` (14541); loop `for depth in range(0, normalized_depth + 1):` (14742) re-filters `ranked_files` each iteration; echoes `payload["max_depth"] = normalized_depth` (14797). The CLI path reaches the same function (`main.py` blast-radius options 8294/8469/8585/9222/9344/9449 have `min=0`, no max -> `build_symbol_blast_radius` (14367)), so one fix covers `tg blast-radius --max-depth` and every MCP caller (`tg_symbol_blast_radius`, `tg_session_blast_radius*`, `tg_impact`, `tg_query`; mcp_server.py 2373-2798, 4361-4441).
- `mcp_server.py :: _confine_write_path` (1366) only checks containment; `PathConfinementError` (908). E-02 sinks: `mcp_audit_tools.py :: tg_ruleset_scan` (369-376, `write_baseline`/`write_suppressions`), `tg_review_bundle_create` (983-987, `output_path`), writing via `ast_scan.py :: _write_json_atomic_refuse_symlink` (453, `replace=True`) and `audit_manifest.py :: create_review_bundle_json` (~250). Artifacts carry `"kind": "ruleset-scan-baseline"` / `"ruleset-scan-suppressions"` (ast_scan.py 516, 642) and `routing_reason == "review-bundle-create"` (audit_manifest.py 228). Existing handlers 394-396 / 987-997 map `PathConfinementError` -> `invalid_input`.
- `ast_wrapper_backend.py :: AstGrepWrapperBackend._raise_for_nonzero` (187) returns on exit 0. Real binary: `printf '' | ast-grep run --json -p 'def (' --lang python --stdin` -> exit 0, `[]`, stderr "Warning: Pattern contains an ERROR node..."; valid zero-match `zzz($A)` -> exit 1, no warning (clean signal).
- `mcp_server.py :: tg_search` (2938): rows at 3330-3334 `match.text.strip()`, no width cap; `tg_ast_search` same builder 3685-3693. `MatchLine.submatches` (result.py 91) holds rg byte offsets.

**Contracts/governance touched:** MCP contract version — AGENTS.md: bump `_TG_MCP_SERVER_CONTRACT_VERSION` whenever a request/response shape changes, including truncated-path-only fields (see the 1.6.0-1.8.0 comment block, mcp_server.py ~140-173). E-04 adds `text_truncated`, `text_chars`, `output_truncated` -> bump **1.8.0 -> 1.9.0** with a comment entry; move the 4 pins + docs/architecture.md (`_CONTRACT_AT_LAST_WIRE_REVIEW` moves with it). E-01 byte-identical; E-02/E-03 reuse the `invalid_input` envelope. **Wave-1 owner of the contract bump: Part C only** (no other wave-1 Part changes MCP shapes). Confinement ratchet `test_mcp_primary_path_confinement_ratchet`: an in-root value must never produce "must stay within" — the new refusal message avoids that phrase. `test_ruleset_scan_write_baseline_overwrites_on_rerun` still passes (second run sees a real baseline artifact).

### Task C.1: E-01 iterate only realised depths (CLI + MCP)

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_cli_modes_blast_radius.py`:

```python
def test_blast_radius_huge_max_depth_iterates_only_realised_depths(tmp_path, monkeypatch):
    from tensor_grep.cli import repo_map

    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from a import foo\n\ndef bar():\n    return foo()\n", encoding="utf-8")
    baseline = repo_map.build_symbol_blast_radius("foo", tmp_path, max_depth=3)

    real_range = range

    def guarded_range(*args):
        candidate = real_range(*args)
        assert len(candidate) <= 10_000, f"unbounded range({args}) in blast-radius"
        return candidate

    monkeypatch.setattr(repo_map, "range", guarded_range, raising=False)
    huge = repo_map.build_symbol_blast_radius("foo", tmp_path, max_depth=10**9)
    assert huge["caller_tree"] == baseline["caller_tree"]
    assert huge["files"] == baseline["files"]
    assert huge["max_depth"] == 10**9
```

(Shadowing module-level `range` makes the pre-fix code fail immediately instead of hanging.)

Add a multi-hop equality pin (council round 3: proves precomputing the depth set cannot truncate the walk). Verified by the orchestrator: `ranked_files` is built ONCE (repo_map.py:14692) and never mutated inside the loop (14742-14748, whose body `continue`s on an empty depth), and 14742 is the only depth loop in the function.

```python
def test_blast_radius_realised_depths_match_range_on_a_four_hop_chain(tmp_path):
    from tensor_grep.cli import repo_map

    (tmp_path / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from a import a\n\ndef b():\n    return a()\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("from b import b\n\ndef c():\n    return b()\n", encoding="utf-8")
    (tmp_path / "d.py").write_text("from c import c\n\ndef d():\n    return c()\n", encoding="utf-8")
    (tmp_path / "e.py").write_text("from d import d\n\ndef e():\n    return d()\n", encoding="utf-8")
    shallow = repo_map.build_symbol_blast_radius("a", tmp_path, max_depth=3)
    deep = repo_map.build_symbol_blast_radius("a", tmp_path, max_depth=10**6)
    assert len({entry.get("depth") for entry in shallow["caller_tree"]}) >= 2  # really multi-hop
    assert [e for e in deep["caller_tree"] if e.get("depth", 0) <= 3] == shallow["caller_tree"]
```

(If `caller_tree` entries carry no `depth` key, compare the rendered depth sections instead; the implementer reads one payload first and adapts the KEY, not the equality.)

- [ ] **Step 2: Run to verify RED** `uv run --no-sync pytest tests/unit/test_cli_modes_blast_radius.py::test_blast_radius_huge_max_depth_iterates_only_realised_depths -q` -> `AssertionError: unbounded range((0, 1000000001)) in blast-radius`.
- [ ] **Step 3: Minimal implementation** — `repo_map.py` ~14742, replace the loop header (body unchanged; echoed `max_depth` stays unclamped -> byte-identical output):

```python
    realised_depths = sorted({
        int(item.get("depth", normalized_depth + 1))
        for item in ranked_files
        if 0 <= int(item.get("depth", normalized_depth + 1)) <= normalized_depth
    })
    for depth in realised_depths:
```

If the loop body emits a row for depths with zero files (e.g. empty tree levels), the implementer must check the baseline equality in the test still holds; if it does not, keep `range` but cap the upper bound at `max(realised_depths, default=0)` instead.

- [ ] **Step 4: Run** the new test, then `uv run --no-sync pytest tests/unit/test_cli_modes_blast_radius.py tests/unit/test_cli_modes_navigation.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(blast-radius): iterate only realised depths so huge --max-depth is O(files)`

### Task C.2: E-02 refuse overwriting non-artifact files

**Interfaces:** Produces in `mcp_server.py`: `class ArtifactWriteRefused(PathConfinementError)`, `_confine_artifact_write_path(candidate: str, anchor: Path, *, label: str, allowed_kinds: frozenset[str] = frozenset(), allowed_routing_reasons: frozenset[str] = frozenset()) -> Path`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_mcp_server_path_confinement.py`:

```python
@pytest.mark.parametrize("target", ["a.py", ".git/config", ".GIT/config", "notes.txt", "other.json"])
def test_ruleset_scan_write_baseline_refuses_non_artifact_target(tmp_path, monkeypatch, target):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    victim = tmp_path / target
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_bytes(b"{}" if target == "other.json" else b"ORIGINAL\n")
    before = victim.read_bytes()
    out = json.loads(mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline=target))
    assert out["error"]["code"] == "invalid_input"
    assert "must stay within" not in out["error"]["message"]
    assert victim.read_bytes() == before


def test_ruleset_scan_new_json_baseline_is_still_allowed(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    out = json.loads(mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline="base.json"))
    assert "error" not in out
    assert json.loads((tmp_path / "base.json").read_text(encoding="utf-8"))["kind"] == "ruleset-scan-baseline"


def test_ruleset_scan_write_suppressions_and_bundle_refuse_source_overwrite(tmp_path, monkeypatch):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    before = (tmp_path / "a.py").read_bytes()
    sup = json.loads(mcp_server.tg_ruleset_scan(
        "secrets-basic", path=".", write_suppressions="a.py", justification="x"))
    assert sup["error"]["code"] == "invalid_input"
    bundle = json.loads(mcp_server.tg_review_bundle_create(manifest_path=str(manifest), output_path="a.py"))
    assert bundle["error"]["code"] == "invalid_input"
    # Non-vacuous: the refusal must come from the artifact gate, not unrelated manifest validation.
    assert "must be a new .json file or an existing tensor-grep artifact" in bundle["error"]["message"]
    assert (tmp_path / "a.py").read_bytes() == before


@pytest.mark.parametrize("body", ['{"kind": []}', '{"routing_reason": {"x": 1}}', "[]", '"s"'])
def test_existing_json_with_non_string_discriminator_is_refused_not_crash(tmp_path, monkeypatch, body):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    victim = tmp_path / "weird.json"
    victim.write_text(body, encoding="utf-8")
    out = json.loads(mcp_server.tg_ruleset_scan("secrets-basic", path=".", write_baseline="weird.json"))
    assert out["error"]["code"] == "invalid_input"
    assert victim.read_text(encoding="utf-8") == body
```

The manifest in the bundle test is `{}`; the message assertion guarantees the refusal is the new artifact gate. The gate runs BEFORE manifest validation (it is the output-path confinement at ~986), so ordering is deterministic; the implementer confirms by reading `tg_review_bundle_create`, and if manifest validation runs first, builds a valid manifest the way the existing `tg_review_bundle_create` tests do (`tg search "tg_review_bundle_create" tests/unit`).

- [ ] **Step 2: Run to verify RED** -> the `a.py` baseline case fails `KeyError: 'error'` (tool succeeded) with changed bytes.
- [ ] **Step 3: Minimal implementation** — `mcp_server.py`, after `_confine_write_path` (~1405):

```python
class ArtifactWriteRefused(PathConfinementError):
    def __init__(self, label: str):
        ValueError.__init__(
            self, f"{label} must be a new .json file or an existing tensor-grep artifact (refused)"
        )


_MCP_ARTIFACT_PROBE_MAX_BYTES = 256 * 1024 * 1024  # council round 12: full-parse cap, far above real artifacts


def _confine_artifact_write_path(
    candidate: str,
    anchor: Path,
    *,
    label: str,
    allowed_kinds: frozenset[str] = frozenset(),
    allowed_routing_reasons: frozenset[str] = frozenset(),
) -> Path:
    """Confine like _confine_write_path, then refuse any target that is not a .json file outside
    .git, or that already exists and is not a prior tg artifact (kind / routing_reason match)."""
    resolved = _confine_write_path(candidate, anchor, label=label)
    rel_parts = resolved.relative_to(anchor.expanduser().resolve()).parts
    if resolved.suffix.lower() != ".json" or any(p.casefold() == ".git" for p in rel_parts):
        raise ArtifactWriteRefused(label)
    if resolved.exists():
        try:
            if not resolved.is_file():
                raise ArtifactWriteRefused(label)
            if resolved.stat().st_size > _MCP_ARTIFACT_PROBE_MAX_BYTES:
                # council rounds 11-12: no prefix shortcut (a prefix check accepts duplicate or
                # malformed documents). The cap is set far above any realistic artifact so reruns
                # keep working, while still bounding the memory one probe may use.
                raise ArtifactWriteRefused(label)
            doc = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ArtifactWriteRefused(label) from None
        kind = doc.get("kind") if isinstance(doc, dict) else None
        reason = doc.get("routing_reason") if isinstance(doc, dict) else None
        # isinstance(str) BEFORE membership: {"kind": []} would raise TypeError (unhashable).
        if not (
            (isinstance(kind, str) and kind in allowed_kinds)
            or (isinstance(reason, str) and reason in allowed_routing_reasons)
        ):
            raise ArtifactWriteRefused(label)
    return resolved
```

(If `PathConfinementError.__init__` takes arguments other than a message, call `super().__init__(<message>)` in the form it expects instead of `ValueError.__init__`; the implementer checks `PathConfinementError` at mcp_server.py ~908.)

`mcp_audit_tools.py`: import `_confine_artifact_write_path` alongside the existing confinement imports (~121); replace the three write confinements: write_baseline (~371) with `_confine_artifact_write_path(write_baseline, scan_root, label="write_baseline", allowed_kinds=frozenset({"ruleset-scan-baseline"}))`; write_suppressions (~375) the same with `"ruleset-scan-suppressions"`; output_path (~986) with `_confine_artifact_write_path(output_path, _mcp_root(), label="output_path", allowed_routing_reasons=frozenset({"review-bundle-create"}))`. The final write still goes through the anchored symlink-refusing atomic helper (same TOCTOU posture as today).

**Large artifacts (council rounds 11-12):** every existing target is validated by a FULL `json.loads` (whole document, effective discriminator after duplicate-key resolution, position-independent), so no producer changes are needed and Part C stays within its own files. The cap is 256 MiB: rerunning an artifact up to that size works, and a larger existing file is refused (recorded residual — main today overwrites anything, so any cap is a new refusal; 256 MiB is far beyond a real baseline/bundle). Tests (append to the C.2 file): (a) a 9 MiB baseline whose `kind` is the LAST key (padded with a large `"findings"` array first) is ACCEPTED for rerun and overwritten; (b) a 9 MiB `.json` with duplicate `kind` keys whose effective (last) value is not allowed is refused, bytes unchanged; (c) a 9 MiB malformed JSON is refused, bytes unchanged. The over-cap refusal is covered by monkeypatching `_MCP_ARTIFACT_PROBE_MAX_BYTES` to 1024 and offering a 2 KiB valid artifact -> refused (no 256 MiB fixture).

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_mcp_server_path_confinement.py tests/unit/test_mcp_server_ruleset_scan.py tests/unit/test_mcp_server_shared.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(mcp): refuse baseline/suppressions/bundle writes that would overwrite non-artifact files`

### Task C.3: E-03 MCP-only malformed-pattern preflight (CLI untouched)

**Interfaces:** Produces `AstGrepWrapperBackend.pattern_warning(pattern: str, config: SearchConfig | None = None) -> str | None` (never raises).

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_ast_wrapper_backend.py`:

```python
def test_pattern_warning_detects_error_node_on_exit_zero():
    backend = AstGrepWrapperBackend()
    ok = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="[]",
        stderr="Warning: Pattern contains an ERROR node and may cause unexpected results.\n",
    )
    with patch.object(backend, "is_available", return_value=True), \
         patch.object(backend, "_run_ast_grep_command", return_value=ok):
        assert "ERROR node" in backend.pattern_warning("def (", SearchConfig(ast=True, lang="python"))


def test_pattern_warning_ignores_legit_zero_match_pattern():
    backend = AstGrepWrapperBackend()
    zero = subprocess.CompletedProcess(args=[], returncode=1, stdout="[]", stderr="")
    with patch.object(backend, "is_available", return_value=True), \
         patch.object(backend, "_run_ast_grep_command", return_value=zero):
        assert backend.pattern_warning("zzz($A)", SearchConfig(ast=True, lang="python")) is None
```

`tests/unit/test_mcp_server_search.py`:

```python
def _ast_search_with(tmp_path, monkeypatch, *, matches, warning):
    from tensor_grep.cli import mcp_server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    result = SearchResult(
        matches=matches, matched_file_paths=["a.py"] if matches else [],
        total_files=1 if matches else 0, total_matches=len(matches),
        routing_backend="AstGrepWrapperBackend", routing_reason="ast",
    )
    fake = type("AstGrepWrapperBackend", (), {
        "search": MagicMock(return_value=result),
        "pattern_warning": MagicMock(return_value=warning),
    })()
    with patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline, \
         patch("tensor_grep.cli.mcp_server.DirectoryScanner") as ms:
        mock_pipeline.return_value.get_backend.return_value = fake
        ms.return_value.walk.return_value = ["a.py"]
        return json.loads(mcp_server.tg_ast_search("def (", "python", ".", structured_json=True))


def test_tg_ast_search_malformed_pattern_with_zero_matches_is_invalid_input(tmp_path, monkeypatch):
    out = _ast_search_with(tmp_path, monkeypatch, matches=[], warning="Warning: Pattern contains an ERROR node")
    assert out["error"]["code"] == "invalid_input"
    assert "ERROR node" in out["error"]["message"]


def test_tg_ast_search_warned_pattern_that_matches_is_returned_normally(tmp_path, monkeypatch):
    hit = MatchLine(line_number=1, text="def f():", file="a.py")
    out = _ast_search_with(tmp_path, monkeypatch, matches=[hit], warning="Warning: Pattern contains an ERROR node")
    assert "error" not in out
    assert out["total_matches"] == 1


def test_tg_ast_search_zero_matches_without_warning_is_a_normal_empty_result(tmp_path, monkeypatch):
    out = _ast_search_with(tmp_path, monkeypatch, matches=[], warning=None)
    assert "error" not in out
    assert out["total_matches"] == 0
```

(If `tg_ast_search` builds its `SearchResult` by merging per-file `backend.search` results, the fake returning the same `result` per file is sufficient because the scanner yields one file.)

- [ ] **Step 2: Run to verify RED** -> RED (behavioural): MCP test `KeyError: 'error'`. The direct `pattern_warning` helper test's `AttributeError` is helper coverage, not RED (council round 13).
- [ ] **Step 3: Minimal implementation** — `ast_wrapper_backend.py`, method after `_build_command`:

```python
    def pattern_warning(self, pattern: str, config: SearchConfig | None = None) -> str | None:
        """Return ast-grep's 'Pattern contains an ERROR node' warning, or None. Empty-stdin run:
        the warning fires only when the pattern itself is malformed, never for a valid pattern
        that merely matches nothing. Single-line patterns only; never raises."""
        try:
            lang = normalize_ast_language(config.lang) if config and config.lang else None
        except ValueError:
            return None
        if not lang or "\n" in pattern or "\r" in pattern or not self.is_available():
            return None
        cmd = [self._get_binary_name(), "run", "--json", "-p", pattern, "--lang", lang, "--stdin"]
        try:
            result = self._run_ast_grep_command(cmd, input_text="")
        except BackendExecutionError:
            return None
        if getattr(result, "returncode", 1) != 0:
            return None
        for line in (result.stderr or "").splitlines():
            if "pattern contains an error node" in line.lower():
                return line.strip()
        return None
```

(`--stdin` means no path positional. Whether ast-grep's clap parser accepts a dash-led `-p` VALUE is UNVERIFIED (council round 2); it is harmless either way — no shell, and a rejected pattern makes `pattern_warning` return None so behaviour equals today's.)

**Council round 1 refinement:** ast-grep says an ERROR-node pattern "may cause unexpected results" — it can still match usefully. So the warning is consulted ONLY when the search returned zero matches; a warned pattern that matches is returned normally. `mcp_server.py :: tg_ast_search`, after the search loop has produced `all_results` and BEFORE the zero-match payload is rendered:

```python
        if all_results.total_matches == 0:
            warn = getattr(backend, "pattern_warning", None)
            pattern_problem = warn(pattern, config) if callable(warn) else None
            if pattern_problem:
                message = f"Invalid AST pattern for language {lang!r}: {pattern_problem}"
                if structured_json:
                    return _self._inject_mcp_contract_fields(json.dumps({
                        "pattern": pattern, "lang": lang, "path": path,
                        "error": {"code": "invalid_input", "message": message},
                    }, indent=2))
                return f"AST search failed: {message}"
```

(`_self` is the module alias defined at mcp_server.py:127, `_self = sys.modules[__name__]` — not a typo.) The three MCP tests in Step 1 pin all three branches (warned+zero -> invalid_input; warned+match -> normal; unwarned+zero -> normal empty).

`_raise_for_nonzero` and `ast_workflows.py :: run_command` unchanged -> `tg run` keeps its exit codes/messages.

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_ast_wrapper_backend.py tests/unit/test_mcp_server_search.py -q` -> PASS. Real-binary dogfood: `tg_ast_search("def (", "python")` over a scratch dir -> `invalid_input`; `tg run -p 'def (' --lang python <dir>` still "No AST matches found", exit 1.
- [ ] **Step 5: Commit** `fix(mcp): tg_ast_search returns invalid_input for malformed AST patterns instead of 0 matches`

### Task C.4: E-04 bound match text width and total bytes; bump contract to 1.9.0

**Interfaces:** Produces in `mcp_server.py`: `_bounded_match_row(filepath: str, match: Any) -> dict`, `_cap_match_rows(rows: list[dict]) -> tuple[list[dict], bool]`, constants `_MCP_MATCH_TEXT_MAX_CHARS = 400`, `_MCP_MATCH_WINDOW_LEAD_CHARS = 100`, `_MCP_MATCHES_MAX_BYTES = 256 * 1024`.

- [ ] **Step 1: Write the failing tests** — `tests/unit/test_mcp_server_search.py` (reuse the `RipgrepBackend` mock scaffold of `test_tg_search_can_return_bounded_structured_json`):

```python
def _run_tg_search_with_line(line, start_byte):
    from tensor_grep.backends.ripgrep_backend import RipgrepBackend
    from tensor_grep.cli import mcp_server

    backend = RipgrepBackend()
    backend.search = MagicMock(return_value=SearchResult(
        matches=[MatchLine(line_number=1, text=line, file="min.js",
                           submatches=({"match": {"text": "NEEDLE"}, "start": start_byte, "end": start_byte + 6},))],
        matched_file_paths=["min.js"], total_files=1, total_matches=1,
        routing_backend="RipgrepBackend", routing_reason="rg_json"))
    with patch("tensor_grep.cli.mcp_server.Pipeline") as mp, \
         patch("tensor_grep.cli.mcp_server.DirectoryScanner") as ms:
        p = mp.return_value
        p.get_backend.return_value = backend
        p.selected_backend_name = "RipgrepBackend"
        p.selected_backend_reason = "rg_json"
        p.selected_gpu_device_ids = []
        p.selected_gpu_chunk_plan_mb = []
        ms.return_value.walk.return_value = ["min.js"]
        return mcp_server.tg_search("NEEDLE", ".")


def test_tg_search_bounds_minified_line_width_and_keeps_the_match():
    line = ("a" * 1_500_000) + "NEEDLE" + ("b" * 1_500_000)
    out = _run_tg_search_with_line(line, 1_500_000)
    assert len(out) < 20_000
    row = json.loads(out)["matches"][0]
    assert row["text_truncated"] is True and row["text_chars"] == len(line)
    assert "NEEDLE" in row["text"] and len(row["text"]) <= 400


def test_tg_search_window_centres_on_match_after_multibyte_prefix():
    prefix = "é" * 2000  # 4000 bytes, 2000 chars
    line = prefix + "NEEDLE" + ("b" * 2000)
    row = json.loads(_run_tg_search_with_line(line, len(prefix.encode("utf-8"))))["matches"][0]
    assert "NEEDLE" in row["text"]


def test_tg_search_total_match_bytes_cap_sets_truncated():
    from tensor_grep.cli import mcp_server

    rows = [{"file": "f", "line_number": i, "text": "x" * 400} for i in range(2000)]
    kept, capped = mcp_server._cap_match_rows(rows)
    assert capped is True and len(kept) < len(rows)
    short = [{"file": "f", "line_number": 1, "text": "ok"}]
    assert mcp_server._cap_match_rows(short) == (short, False)
```

- [ ] **Step 2: Run to verify RED** -> RED (behavioural): `KeyError: 'text_truncated'` and the tool-level cap tests (no `output_truncated` / no notice, output over the bound). The direct `_cap_match_rows` helper test's `AttributeError` is helper coverage, not RED (council round 13).
- [ ] **Step 3: Minimal implementation** — `mcp_server.py`, near `_DEFAULT_MCP_FIND_MAX_TOKENS` (~254):

```python
_MCP_MATCH_TEXT_MAX_CHARS = 400
_MCP_MATCH_WINDOW_LEAD_CHARS = 100
_MCP_MATCHES_MAX_BYTES = 256 * 1024


def _bounded_match_row(filepath: str, match: Any) -> dict[str, Any]:
    raw = match.text
    stripped = raw.strip()
    row: dict[str, Any] = {"file": filepath, "line_number": match.line_number, "text": stripped}
    if len(stripped) <= _MCP_MATCH_TEXT_MAX_CHARS:
        return row
    start_char = 0
    subs = getattr(match, "submatches", None)
    if subs:
        try:
            byte_start = int(subs[0].get("start", 0))
            start_char = len(raw.encode("utf-8")[:byte_start].decode("utf-8", "ignore"))
        except (TypeError, ValueError, AttributeError):
            start_char = 0
    lo = max(0, start_char - _MCP_MATCH_WINDOW_LEAD_CHARS)
    row["text"] = raw[lo : lo + _MCP_MATCH_TEXT_MAX_CHARS].strip()
    row["text_truncated"] = True
    row["text_chars"] = len(raw)
    return row


def _rendered_row_bytes(row: dict[str, Any]) -> int:
    """UTF-8 bytes this row occupies in the tool's FINAL output (council round 7: measure as
    rendered, not compact). The tools serialize with ``json.dumps(payload, indent=2)`` and the
    rows sit at nesting depth 2 (payload -> "matches" -> row), so every line of the row's own
    indent=2 rendering gains 4 leading spaces, plus a ",\\n" separator."""
    rendered = json.dumps(row, indent=2)
    return len(rendered.encode("utf-8")) + 4 * (rendered.count("\n") + 1) + 2


def _cap_match_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    kept: list[dict[str, Any]] = []
    used = 0
    for row in rows:
        used += _rendered_row_bytes(row)
        if used > _MCP_MATCHES_MAX_BYTES:
            return kept, True
        kept.append(row)
    return kept, False
```

(Confirm both tools serialize with `indent=2` — `tg search -n "json.dumps\(payload" src/tensor_grep/cli/mcp_server.py`; if either uses a different indent, parameterise `_rendered_row_bytes(row, depth_spaces=...)` for that tool. `ensure_ascii` stays at the tools' current setting: with the default `ensure_ascii=True` a multibyte char renders as a 6-byte `\uXXXX` escape, which `json.dumps` already counts.)

In `tg_search` (~3330) build rows with `_bounded_match_row(filepath, match)`; then:

```python
                payload_matches, byte_capped = _cap_match_rows(payload_matches)
                if byte_capped:
                    omitted_matches = max(0, all_results.total_matches - len(payload_matches))
                    rendered_file_count = len({row["file"] for row in payload_matches})
                    omitted_files = max(0, all_results.total_files - rendered_file_count)
                    truncated = True
```

and set `payload["output_truncated"] = True` only when `byte_capped`. Same builder + cap in `tg_ast_search` (~3685-3693). Plain-text branches (~3370, ~3732) emit `m.text.strip()[:_MCP_MATCH_TEXT_MAX_CHARS]` AND enforce the same cumulative budget (council round 6: per-line truncation alone leaves the total unbounded): count each line's `len(line.encode("utf-8")) + 1` (the newline) BEFORE appending it, stop when the next line would push the accumulated bytes past `_MCP_MATCHES_MAX_BYTES`, and append the ASCII notice `"... output truncated at 262144 bytes; narrow the search or lower max_results"`. Tool-level tests (council round 8, verified: `tg_ast_search` has NO `max_results`/`max_files` parameter — its JSON branch hard-codes `rendered_file_limit = 15` / `rendered_result_limit = 150` at mcp_server.py:3669-3670 and its plain-text branch renders `[:15]` files x `[:10]` matches at ~3729-3731, so its rendered output is at most 150 rows): **`tg_ast_search` JSON** — 150 rows of `"é" * 400` (400 chars, so no per-row truncation, but ~2,400 rendered bytes each under `ensure_ascii` -> ~360 KB > cap) MUST fire the cap: `output_truncated` true and `len(matches) < 150`; **`tg_ast_search` plain text** — assert the byte bound, per-line truncation and the positive control only; its cumulative cap is defence in depth that the current 150-line limit cannot reach (state this in the test docstring, do not assert it fired). **`tg_search`, both modes** — the cap must fire: pass `max_results=5000` (and any other rendering limit the tool exposes, set high enough — confirm the parameter names first) over a stubbed backend returning 2,000 matches of 400 chars each; assert `len(out.encode("utf-8")) <= _MCP_MATCHES_MAX_BYTES + 8192` (8 KiB envelope allowance: contract fields, counts, notice) AND that the cap fired — (plain text) the notice is present / (JSON) `output_truncated` is true and `len(matches) < 2000`. Add one plain-text case with multibyte text (`"é" * 400` per line) so byte, not char, accounting is exercised. Positive control: 10 short matches -> no notice, `output_truncated` absent/false, all rows present. Bump `_TG_MCP_SERVER_CONTRACT_VERSION = "1.9.0"`, add the 1.8.0 -> 1.9.0 comment entry (text_truncated/text_chars per row, output_truncated top-level), update the 4 pins + docs/architecture.md.

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_mcp_server_search.py tests/unit/test_mcp_caps.py tests/unit/test_mcp_passthrough_wire_surface.py tests/unit/test_mcp_server_meta_dispatch.py tests/integration/test_mcp_stdio_protocol.py tests/unit/test_mcp_contract_stamp_ratchet.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(mcp): bound tg_search match width and total bytes; bump MCP contract to 1.9.0`

### Part C verification gates

```
uv run --no-sync ruff check src/tensor_grep/cli/repo_map.py src/tensor_grep/cli/mcp_server.py src/tensor_grep/cli/mcp_audit_tools.py src/tensor_grep/backends/ast_wrapper_backend.py <test files>
uv run --no-sync ruff format --check --preview <same files>
uv run --no-sync mypy src/tensor_grep
uv run --no-sync pytest <each task's list> tests/unit/test_cli_modes_ast_backend.py -q
```

### Part C out of scope

E-05..E-12 (incl. bad-arg handling E-06, unsupported lang E-09, `max_results`/`max_files` clamps — the byte cap bounds output instead); changing `tg run` malformed-pattern exit codes; multiline-pattern preflight; closing E-02's check-then-write window beyond the anchored atomic writer.

---
## Part D: fix(search): stop Python-side re-derivation of rg/Rust match semantics

**Findings closed:** I-01, I-02, I-03, I-04. **Dropped:** none. Corrections to the audit: `ripgrep_fmt._binary_notice` (L14) runs no regex (only `read_bytes().find(b"\0")`); the user-pattern `re` call in `ripgrep_fmt.py` is `RipgrepFormatter._column_for_match` (L42-74) — the same defect as I-01 on the `--column`/`--vimgrep` surface, folded into D.1. Both `_binary_notice` and `rust_backend._binary_notice_text` read the whole file -> replaced by a bounded chunked NUL scan.
**Risk class:** security (ReDoS on user pattern: 57s / 15.6s measured) + correctness -> adversarial gate required.

**Files:** Modify `src/tensor_grep/cli/formatters/json_fmt.py`, `src/tensor_grep/cli/formatters/ripgrep_fmt.py`, `src/tensor_grep/backends/ripgrep_backend.py`, `src/tensor_grep/backends/rust_backend.py`, `src/tensor_grep/core/pipeline.py`. Tests: modify `tests/unit/test_medlow_json_columns.py`, `tests/unit/test_rust_core.py`; create `tests/unit/test_ripgrep_backend_field_coverage.py`, `tests/unit/test_pipeline_count_semantics.py`.

**Seams verified:**
- `json_fmt.py :: _column_for_match` (L12-52) runs `re.search(pattern, match.text)` on the user pattern; `_match_payload` (L73) calls it and ignores `match.submatches` (copied out only at ~L95).
- `ripgrep_fmt.py :: RipgrepFormatter._column_for_match` (L42) same `re.search`; `_submatch_columns` (L76) already returns `start+1` from rg submatches (used L152-179); the regex is only the `or [self._column_for_match(match)]` fallback.
- `rust_backend.py :: RustCoreBackend._binary_file_matches_pattern` (L105-126): `re.search(pattern_bytes, haystack)` over `read_bytes()` of the whole file; called at L180. `_binary_notice_text` (L92) `read_bytes().find(b"\0")`.
- `ripgrep_backend.py :: RipgrepBackend._build_cmd` (L531-876): AST diff of `config.<attr>` reads vs `SearchConfig` fields — never read: `smart_case`, `stop_on_nonmatch`, `null_data`, `engine`, `dfa_size_limit`, `regex_size_limit` (remaining unread fields are inert; listed in KNOWN_GAP below). `--engine pcre2` is honoured only via `config.pcre2` (main.py ~1961).
- `ripgrep_backend.py :: search` (L80) routes count / files-with-matches / normal through `_build_cmd` (covers `--stats`, `--format csv|table`, the MCP python pipeline).
- `pipeline.py :: Pipeline` count arm (L342-345) `elif config and config.count and rust_available:` -> `rust_backend`, reason `count_rust_fast_path`; `RustCoreBackend.search` (L224) calls `inner.count_matches(pattern, path, ignore_case, fixed_strings)` — the native signature (rust_core lib.rs L178) has no word/line/smart-case/max-count.
- `pipeline.py :: _needs_python_cpu` (L88) = context | before/after_context | line_regexp | word_regexp | ltl.
- Ratchet model: `tests/unit/test_native_delegation_field_coverage.py :: _forwarded_config_fields` + `TestFieldCoverageRatchet.test_every_field_classified` (AST over `config.<attr>`, KNOWN_GAP frozenset, stale-entry test).

**Contracts/governance touched:** `tests/unit/test_medlow_json_columns.py::test_config_regex_find` (`\bbar\b` expects 6) changes deliberately: without rg submatches a REGEX has no safe column -> `None` (omitted, never wrong); replaced by a submatch-based test. Other pins (range priority, fixed find, literal `-i`, empty pattern, `regexp[0]`, omit-when-None) stay green. JSON stays additive except the removal of the regex-guess column for non-rg backends (deliberate fail-honest; noted in the commit). `test_rust_core.py::test_rust_backend_invalid_regex_in_binary_notice_does_not_become_literal` (L311) must still raise `InvalidRegexError` (rg-based check keeps that; gets an rg-absent skip). `test_pipeline.py` L722 (`count_rust_fast_path` for plain `-c` with rg missing) and `test_cli_modes_search_guards.py` L1219 stay green (gate refuses only on semantic flags). No docs pin these (searched `count_rust_fast_path|_column_for_match` in docs/ tests/).

### Task D.1: column from rg submatches; never run the user regex in Python (I-01)

**Interfaces:** Produces in `json_fmt.py`: `_literal_column_index(text: str, pattern: str, *, ignore_case: bool) -> int`, `_REGEX_META: frozenset[str]` (imported by `ripgrep_fmt.py`).

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_medlow_json_columns.py` and delete `test_config_regex_find`:

```python
import re as _re

from tensor_grep.cli.formatters import json_fmt
from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter


def _forbid_re(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("user pattern must not be evaluated by Python re")

    monkeypatch.setattr(_re, "search", boom)
    monkeypatch.setattr(_re, "compile", boom)


def test_column_prefers_rg_submatch_byte_offset_over_pattern_guess():
    match = MatchLine(
        line_number=1, text="foobar foo", file="f.py",
        submatches=[{"match": {"text": "foo"}, "start": 7, "end": 10}],
    )
    assert json_fmt._column_for_match(match, SearchConfig(query_pattern="foo", word_regexp=True)) == 8


def test_column_is_byte_offset_for_multibyte_line():
    match = MatchLine(
        line_number=1, text="é foo", file="f.py",
        submatches=[{"match": {"text": "foo"}, "start": 3, "end": 6}],
    )
    assert json_fmt._column_for_match(match, SearchConfig(query_pattern="foo")) == 4


def test_regex_without_submatches_is_omitted_and_never_evaluated(monkeypatch):
    _forbid_re(monkeypatch)
    match = _match_no_range("a" * 40)
    assert json_fmt._column_for_match(match, SearchConfig(query_pattern="(a+)+b|a$")) is None
    assert json_fmt._column_for_match(_match_no_range("foo: bar baz"), SearchConfig(query_pattern=r"\bbar\b")) is None


def test_explicit_case_sensitive_overrides_smart_case_for_column():
    # council round 10 regression control (passes on main's behaviour class; guards the new code)
    m = _match_no_range("FOO foo")
    cfg = SearchConfig(query_pattern="foo", smart_case=True, case_sensitive=True)
    assert json_fmt._column_for_match(m, cfg) == 5
    fmt = RipgrepFormatter(SearchConfig(query_pattern="foo", smart_case=True, case_sensitive=True, column=True))
    assert fmt._column_for_match(m) == 5


def test_non_ascii_case_insensitive_literal_omits_column():
    # council round 9: "ς σ" -i σ matches at col 1 under Unicode caseless rules; lower().find says 4
    m = _match_no_range("ς σ")
    assert json_fmt._column_for_match(m, SearchConfig(query_pattern="σ", ignore_case=True)) is None
    fmt = RipgrepFormatter(SearchConfig(query_pattern="σ", ignore_case=True, column=True))
    assert fmt._column_for_match(m) == 1  # the formatter's documented no-column fallback
    assert json_fmt._column_for_match(_match_no_range("xx FOO"), SearchConfig(query_pattern="foo", ignore_case=True)) == 4  # ASCII control


def test_word_or_line_regexp_without_submatches_omits_column():
    # council round 8: "foobar foo" -w foo matches at col 8; find() would say 1 -- omit instead
    m = _match_no_range("foobar foo")
    assert json_fmt._column_for_match(m, SearchConfig(query_pattern="foo", word_regexp=True)) is None
    assert json_fmt._column_for_match(m, SearchConfig(query_pattern="foo", line_regexp=True)) is None


def test_literal_pattern_still_gets_byte_column(monkeypatch):
    _forbid_re(monkeypatch)
    assert json_fmt._column_for_match(_match_no_range("xx foo"), SearchConfig(query_pattern="foo")) == 4


def test_ripgrep_formatter_column_does_not_run_user_regex(monkeypatch):
    _forbid_re(monkeypatch)
    fmt = RipgrepFormatter(SearchConfig(query_pattern="(a+)+b|a$", column=True))
    assert fmt._column_for_match(_match_no_range("a" * 40)) == 1
```

(No wall-clock assertion: the "never evaluated" assertion is deterministic and proves the ReDoS path is gone.)

- [ ] **Step 2: Run to verify RED** `uv run --no-sync pytest tests/unit/test_medlow_json_columns.py -q` -> submatch test `1 != 8`; the `_forbid_re` tests fail with `AssertionError: user pattern must not be evaluated by Python re`.
- [ ] **Step 3: Minimal implementation** — `json_fmt.py`: remove the regex fallback and `import re` (if otherwise unused); after the unchanged `if match.range is not None:` block:

```python
_REGEX_META = frozenset("\\.^$|?*+()[]{}")


def _literal_column_index(text: str, pattern: str, *, ignore_case: bool) -> int:
    """Linear (no backtracking) first-occurrence index for a LITERAL pattern, else -1."""
    if not pattern:
        return -1
    if not ignore_case:
        return text.find(pattern)
    if not (text.isascii() and pattern.isascii()):
        return -1  # council round 9: lower() is not Unicode caseless matching (`ς` vs `σ`) -- never guess
    return text.lower().find(pattern.lower())


def _column_for_match(match, config=None):
    if match.range is not None:
        ...  # unchanged
    for sub in match.submatches or ():
        start = sub.get("start") if isinstance(sub, dict) else None
        if isinstance(start, int) and not isinstance(start, bool):
            return start + 1  # rg's authoritative 0-based BYTE offset
    if config is None:
        return None
    pattern = config.query_pattern or ""
    if not pattern and config.regexp:
        pattern = config.regexp[0]
    is_literal = bool(config.fixed_strings) or not (_REGEX_META & set(pattern))
    if not pattern or not is_literal:
        return None  # a real regex is never evaluated in Python
    if config.word_regexp or config.line_regexp:
        return None  # council round 8: first-occurrence find() ignores -w/-x boundaries -- omit, never guess
    # council round 10: explicit -s (case_sensitive) overrides smart case, as in D.2 / _build_cmd
    ignore_case = bool(config.ignore_case or (config.smart_case and not config.case_sensitive and pattern.islower()))
    index = _literal_column_index(match.text, pattern, ignore_case=ignore_case)
    if index < 0:
        return None
    return len(match.text[:index].encode("utf-8")) + 1
```

`ripgrep_fmt.py`: `from tensor_grep.cli.formatters.json_fmt import _REGEX_META, _literal_column_index`; replace the `re`-based `else:` block (L55-70) with the same literal-only logic INCLUDING the `word_regexp`/`line_regexp` guard and the same effective-case rule (`case_sensitive` overrides `smart_case`, council round 10) (council round 8; there the guarded case takes the existing `return 1` fallback, the formatter's pre-existing no-column value); keep the existing `if index < 0: return 1` fallback and the `match.range` branch. Add the formatter twin: `RipgrepFormatter(SearchConfig(query_pattern="foo", word_regexp=True, column=True))._column_for_match(_match_no_range("foobar foo")) == 1` (never the guessed-and-wrong value from a boundary-blind find, and Python `re` still forbidden).

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_medlow_json_columns.py tests/unit/test_formatters.py tests/unit/test_cli_modes_cli_json.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(json): derive --json/--column column from rg submatches, never run the user regex in Python`

### Task D.2: forward every rg-owned flag + SearchConfig forwarding ratchet (I-02)

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_ripgrep_backend_field_coverage.py`:

```python
"""RipgrepBackend._build_cmd must forward (or explicitly gap) every SearchConfig field.
Modelled on tests/unit/test_native_delegation_field_coverage.py."""

import ast
import dataclasses
import inspect
import textwrap
from unittest.mock import patch

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.core.config import SearchConfig

_RG_BACKEND_KNOWN_GAP_FIELDS = frozenset({
    # non-rg engines
    "ast", "ast_prefer_native", "ast_selector", "ast_stdin", "ast_stdin_input",
    "ast_strictness", "lang", "ltl", "nlp_threshold", "use_jit",
    # routing / telemetry
    "force_cpu", "format_type", "gpu_device_ids", "input_total_bytes",
    "json_mode", "query_pattern", "rank_bm25", "semantic_rank",
    # handled by the CLI layer before the backend
    "generate", "type_list", "pcre2_version", "quiet",
    # irrelevant to rg --json parsing
    "pretty", "hostname_bin", "hyperlink_format", "line_number_explicit",
})


def _forwarded() -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(RipgrepBackend._build_cmd)))
    return {
        n.attr for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "config"
    }


def _cmd(**kw) -> list[str]:
    backend = RipgrepBackend()
    with patch.object(backend, "_get_binary_name", return_value="rg"):
        return backend._build_cmd(file_path="a.txt", pattern="foo", config=SearchConfig(**kw), json_mode=True)


def test_every_searchconfig_field_forwarded_or_gapped():
    fields = {f.name for f in dataclasses.fields(SearchConfig)}
    uncovered = sorted(fields - _forwarded() - _RG_BACKEND_KNOWN_GAP_FIELDS)
    assert not uncovered, f"classify {uncovered}: forward to rg in _build_cmd or add to KNOWN_GAP"


def test_known_gap_has_no_stale_entries():
    fields = {f.name for f in dataclasses.fields(SearchConfig)}
    assert not (_RG_BACKEND_KNOWN_GAP_FIELDS - fields)
    assert not (_RG_BACKEND_KNOWN_GAP_FIELDS & _forwarded())


@pytest.mark.parametrize("kw, expected", [
    ({"smart_case": True}, ["-S"]),
    ({"stop_on_nonmatch": True}, ["--stop-on-nonmatch"]),
    ({"null_data": True}, ["--null-data"]),
    ({"engine": "pcre2"}, ["--engine", "pcre2"]),
    ({"engine": "auto"}, ["--engine", "auto"]),
    ({"dfa_size_limit": "10M"}, ["--dfa-size-limit", "10M"]),
    ({"regex_size_limit": "20M"}, ["--regex-size-limit", "20M"]),
])
def test_flag_forwarded(kw, expected):
    cmd = _cmd(**kw)
    i = cmd.index(expected[0])
    assert cmd[i : i + len(expected)] == expected


def test_default_engine_and_explicit_case_flags_unchanged():
    assert "--engine" not in _cmd()
    assert "-S" not in _cmd(smart_case=True, ignore_case=True)    # explicit -i wins
    assert "-S" not in _cmd(smart_case=True, case_sensitive=True)  # explicit -s wins


def test_unknown_engine_fails_closed():
    with pytest.raises(BackendExecutionError):
        _cmd(engine="bogus")
```

If the KNOWN_GAP list above names a field that does not exist on `SearchConfig` (the stale-entry test will say which), remove it; if the ratchet names further unread fields, classify each explicitly (forward if rg owns it, else gap with a comment) — never silently.

- [ ] **Step 2: Run to verify RED** -> ratchet fails `classify ['dfa_size_limit', 'engine', 'null_data', 'regex_size_limit', 'smart_case', 'stop_on_nonmatch']`; flag tests `ValueError: '-S' is not in list`.
- [ ] **Step 3: Minimal implementation** — in `_build_cmd`, right after the `config.no_invert_match` block (L563-564):

```python
            if config.smart_case and not (config.ignore_case or config.case_sensitive):
                cmd.append("-S")  # explicit -i/-s win; rg is last-flag-wins
            if config.stop_on_nonmatch:
                cmd.append("--stop-on-nonmatch")
            if config.null_data:
                cmd.append("--null-data")
            engine = str(config.engine or "default").lower()
            if engine in {"pcre2", "auto"}:
                cmd.extend(["--engine", engine])
            elif engine != "default":
                raise BackendExecutionError(f"unsupported --engine value: {config.engine!r}")
            if config.dfa_size_limit:
                cmd.extend(["--dfa-size-limit", str(config.dfa_size_limit)])
            if config.regex_size_limit:
                cmd.extend(["--regex-size-limit", str(config.regex_size_limit)])
```

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_ripgrep_backend_field_coverage.py tests/unit/test_ripgrep_backend.py tests/unit/test_rg_root_ignore.py tests/unit/test_ripgrep_timeout_partial.py tests/unit/test_quiet_survives_rg_passthrough.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(rg-backend): forward smart-case, stop-on-nonmatch, null-data, engine and size limits to rg; add forwarding ratchet`

### Task D.3: binary-file match check via rg, not Python re; bounded NUL scan (I-03)

**Interfaces:** Produces `tensor_grep.backends.rust_backend._first_nul_offset(path: str, *, chunk_size: int = 65536, max_bytes: int | None = None) -> int` (returns -1 when none), reused by `ripgrep_fmt._binary_notice`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_rust_core.py` (add `_rg_or_skip()` as the first line of the existing `test_binary_file_matches_pattern(... "(")` test too):

```python
def _rg_or_skip():
    from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary

    if resolve_ripgrep_binary() is None:
        pytest.skip("rg not installed")


def test_binary_notice_check_does_not_run_python_re(monkeypatch, tmp_path):
    _rg_or_skip()
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    def boom(*a, **k):
        raise AssertionError("user pattern must not be evaluated by Python re")

    monkeypatch.setattr(rb.re, "search", boom)
    f = tmp_path / "bin.bin"
    f.write_bytes(b"\x00" + b"a" * 60 + b"\nxyz\n")
    assert rb.RustCoreBackend._binary_file_matches_pattern(str(f), "(a+)+$", SearchConfig()) is True


def test_binary_notice_check_accepts_unicode_class(tmp_path):
    _rg_or_skip()
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    f = tmp_path / "bin.bin"
    f.write_bytes(b"\x00abc\n")
    assert rb.RustCoreBackend._binary_file_matches_pattern(str(f), r"\p{L}+", SearchConfig()) is True


def test_binary_notice_check_honours_word_and_smart_case(tmp_path):
    _rg_or_skip()
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.core.config import SearchConfig

    f = tmp_path / "bin.bin"
    f.write_bytes(b"\x00foobar\n")
    check = rb.RustCoreBackend._binary_file_matches_pattern
    assert check(str(f), "foo", SearchConfig(word_regexp=True)) is False
    assert check(str(f), "FOO", SearchConfig(smart_case=True)) is False
    assert check(str(f), "foo", SearchConfig(smart_case=True)) is True
    # fixed-string branch must honour -w / -x too (was raw byte containment)
    assert check(str(f), "foo", SearchConfig(fixed_strings=True, word_regexp=True)) is False
    assert check(str(f), "foo", SearchConfig(fixed_strings=True, line_regexp=True)) is False
    assert check(str(f), "foobar", SearchConfig(fixed_strings=True)) is True
    assert check(str(f), "a.b", SearchConfig(fixed_strings=True)) is False  # literal, not regex


def test_plain_fixed_string_binary_check_works_without_rg(monkeypatch, tmp_path):
    # council round 11: rg-free positive control incl. a match spanning a chunk boundary
    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.cli import runtime_paths
    from tensor_grep.core.config import SearchConfig

    monkeypatch.setattr(runtime_paths, "resolve_ripgrep_binary", lambda: None)
    f = tmp_path / "bin.bin"
    f.write_bytes(b"\x00" + b"x" * 65530 + b"NEEDLE" + b"\n")  # straddles the 65536 boundary
    assert rb.RustCoreBackend._binary_file_matches_pattern(str(f), "NEEDLE", SearchConfig(fixed_strings=True)) is True
    assert rb.RustCoreBackend._binary_file_matches_pattern(str(f), "ABSENT", SearchConfig(fixed_strings=True)) is False
    assert rb._file_contains_literal(str(f), b"NEEDLE", chunk_size=4096) is True
    with pytest.raises(rb.BackendExecutionError):  # fail-closed control: an unsupported flag still needs rg
        rb.RustCoreBackend._binary_file_matches_pattern(str(f), "needle", SearchConfig(fixed_strings=True, ignore_case=True))


def test_first_nul_offset_is_chunked(tmp_path):
    from tensor_grep.backends.rust_backend import _first_nul_offset

    f = tmp_path / "late.bin"
    f.write_bytes(b"a" * 70000 + b"\x00")
    assert _first_nul_offset(str(f), chunk_size=4096) == 70000
    g = tmp_path / "none.txt"
    g.write_bytes(b"abc")
    assert _first_nul_offset(str(g)) == -1


def test_first_nul_offset_never_reads_unbounded(tmp_path, monkeypatch):
    # council round 13: offset correctness alone would pass with read_bytes().find(); record reads
    import builtins

    from tensor_grep.backends import rust_backend as rb

    sizes: list[int | None] = []
    real_open = builtins.open

    class _Rec:
        def __init__(self, handle):
            self._h = handle

        def read(self, n=-1):
            sizes.append(n)
            return self._h.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._h.close()

    monkeypatch.setattr(rb, "open", lambda p, mode="r", *a, **k: _Rec(real_open(p, mode, *a, **k)), raising=False)
    f = tmp_path / "late.bin"
    f.write_bytes(b"a" * 70000 + b"\x00")
    assert rb._first_nul_offset(str(f), chunk_size=4096) == 70000  # crosses many chunk boundaries
    # every read is bounded by chunk_size (a module-global `open` shadow intercepts the call)
    assert sizes and all(isinstance(n, int) and 0 < n <= 4096 for n in sizes), sizes


def test_binary_notice_entry_points_never_read_whole_file(tmp_path, monkeypatch):
    # council round 14: BEHAVIOURAL -- through both existing notice consumers. On main they call
    # Path.read_bytes() (whole file), which this test forbids; after the fix they use the chunked scan.
    from pathlib import Path as _P

    from tensor_grep.backends import rust_backend as rb
    from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter
    from tensor_grep.core.config import SearchConfig

    f = tmp_path / "late.bin"
    f.write_bytes(b"a" * 200_000 + b"\x00" + b"tail\n")
    monkeypatch.setattr(_P, "read_bytes", lambda self: pytest.fail(f"whole-file read of {self}"))
    assert rb.RustCoreBackend._binary_notice_text(str(f)) is not None  # notice still produced
    assert RipgrepFormatter(SearchConfig())._binary_notice(str(f)) is not None
```

(Confirm both entry points' real names/signatures with `tg defs src/tensor_grep/backends/rust_backend.py _binary_notice_text` and `tg defs src/tensor_grep/cli/formatters/ripgrep_fmt.py _binary_notice` and adapt the calls; if either reads via `open(...).read()` rather than `read_bytes`, forbid that read instead — the assertion is "no whole-file read", whichever API main uses.)

- [ ] **Step 2: Run to verify RED** `uv run --no-sync pytest tests/unit/test_rust_core.py -k "binary_notice or first_nul" -q` -> `AssertionError: user pattern must not be evaluated by Python re`; `test_binary_notice_entry_points_never_read_whole_file` fails `whole-file read of ...` (behavioural, council round 14); `\p{L}` -> `InvalidRegexError`. The `_first_nul_offset` / bounded-read tests' `ImportError` is helper coverage, not RED (council round 13).
- [ ] **Step 3: Minimal implementation** (`rust_backend.py`):

```python
def _first_nul_offset(path: str, *, chunk_size: int = 65536, max_bytes: int | None = None) -> int:
    offset = 0
    with open(path, "rb") as handle:
        while max_bytes is None or offset < max_bytes:
            chunk = handle.read(chunk_size)
            if not chunk:
                return -1
            idx = chunk.find(b"\0")
            if idx >= 0:
                return offset + idx
            offset += len(chunk)
    return -1


def _file_contains_literal(path: str, needle: bytes, *, chunk_size: int = 65536) -> bool:
    """Bounded-memory exact byte search; carries len(needle)-1 bytes across chunk boundaries."""
    if not needle:
        return True
    keep = len(needle) - 1
    tail = b""
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            window = tail + chunk
            if needle in window:
                return True
            tail = window[-keep:] if keep else b""
    return False
```

Use it in `_binary_notice_text` and `ripgrep_fmt.RipgrepFormatter._binary_notice` in place of `read_bytes().find(b"\0")` (keep their existing `OSError` handling). In `_binary_file_matches_pattern`, route BOTH branches through rg (council round 1: raw byte containment cannot honour `-w`/`-x`, so the fixed-string branch is replaced too; fixed strings add `-F`). Replace the whole body after the binary check:

```python
        from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary
        from tensor_grep.cli.subprocess_policy import configured_ripgrep_timeout_seconds, run_subprocess

        plain_literal = bool(config and config.fixed_strings) and not (
            config.ignore_case or config.smart_case or config.word_regexp or config.line_regexp
        )
        if plain_literal:
            # council round 11: an exact case-sensitive literal has no regex semantics and no
            # ReDoS surface -- keep the rg-free path (bounded chunks with overlap), as main has.
            return _file_contains_literal(file_path, pattern.encode("utf-8"))
        rg = resolve_ripgrep_binary()
        if rg is None:
            raise BackendExecutionError(
                "binary-file match check for a regex pattern requires the 'rg' binary; "
                "refusing to evaluate the pattern with Python re (semantics and ReDoS differ)."
            )
        cmd = [str(rg), "-a", "-q", "--no-config"]
        if config and config.fixed_strings:
            cmd.append("-F")
        if config and config.ignore_case:
            cmd.append("-i")
        elif config and config.smart_case and not config.case_sensitive:
            cmd.append("-S")
        if config and config.word_regexp:
            cmd.append("-w")
        if config and config.line_regexp:
            cmd.append("-x")
        cmd += ["-e", pattern, "--", file_path]
        proc = run_subprocess(
            cmd, capture_output=True, text=True, check=False,
            timeout_seconds=configured_ripgrep_timeout_seconds(),
        )
        if proc.returncode == 0:
            return True
        if proc.returncode == 1:
            return False
        raise InvalidRegexError(f"invalid regex pattern: {(proc.stderr or '').strip()[:300]}")
```

(`-e` + `--` per the argv-sentinel rule; no `--no-messages` so rg's parse error reaches `InvalidRegexError`. Remove the now-unused non-fixed `ignore_case` computation; keep `import re` if used elsewhere.) If `configured_ripgrep_timeout_seconds` does not exist under that name, use the timeout helper `RipgrepBackend` already uses (find with `tg search "timeout_seconds" src/tensor_grep/backends/ripgrep_backend.py`).

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_rust_core.py tests/unit/test_formatters.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(rust-backend): decide binary-file regex matches with rg, never Python re; bounded NUL scan`

### Task D.4: route `-c` with semantic flags away from the Rust count fast path (I-04)

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_pipeline_count_semantics.py`, copying the four `@patch(...)` decorators and mock parameter order verbatim from the existing count test in `tests/unit/test_pipeline.py` (~L702-722):

```python
@pytest.mark.parametrize("kw", [
    {"word_regexp": True}, {"line_regexp": True}, {"smart_case": True},
    {"max_count": 1}, {"null_data": True}, {"stop_on_nonmatch": True},
])
def test_count_with_semantic_flag_uses_rg_not_rust_count(mock_cudf, mock_mem, mock_rust, mock_rg, kw):
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True
    p = Pipeline(force_cpu=False, config=SearchConfig(query_pattern="foo", count=True, **kw))
    assert p.backend == mock_rg.return_value
    assert p.selected_backend_reason == "count_rg_semantics"


def test_plain_count_still_uses_rust_fast_path(mock_cudf, mock_mem, mock_rust, mock_rg):
    mock_rg.return_value.is_available.return_value = True
    mock_rust.return_value.is_available.return_value = True
    p = Pipeline(force_cpu=False, config=SearchConfig(query_pattern="foo", count=True))
    assert p.selected_backend_reason == "count_rust_fast_path"


def test_count_unforwardable_flag_without_rg_fails_closed(mock_cudf, mock_mem, mock_rust, mock_rg):
    mock_rg.return_value.is_available.return_value = False
    mock_rust.return_value.is_available.return_value = True
    with pytest.raises(BackendExecutionError):
        Pipeline(force_cpu=False, config=SearchConfig(query_pattern="foo", count=True, null_data=True))


def test_count_word_regexp_without_rg_uses_python_cpu(mock_cudf, mock_mem, mock_rust, mock_rg):
    mock_rg.return_value.is_available.return_value = False
    mock_rust.return_value.is_available.return_value = True
    p = Pipeline(force_cpu=False, config=SearchConfig(query_pattern="foo", count=True, word_regexp=True))
    assert p.selected_backend_reason == "count_python_cpu_semantics"
```

- [ ] **Step 2: Run to verify RED** -> `'count_rust_fast_path' != 'count_rg_semantics'`.
- [ ] **Step 3: Minimal implementation** (`pipeline.py`; import `BackendExecutionError` next to `ComputeBackend`, L7):

```python
    @staticmethod
    def _count_needs_rg_semantics(config: SearchConfig | None) -> bool:
        """Flags RustCoreBackend.count_matches(pattern, path, ignore_case, fixed) cannot honour."""
        return bool(config) and bool(
            config.word_regexp or config.line_regexp or config.smart_case
            or config.max_count is not None or config.null_data or config.stop_on_nonmatch
        )
```

Count arm (L342) becomes `elif config and config.count and rust_available and not self._count_needs_rg_semantics(config):`; insert after it:

```python
            elif config and config.count and self._count_needs_rg_semantics(config):
                if rg_available:
                    self.backend = rg_backend
                    selected_backend_reason = "count_rg_semantics"
                elif config.null_data or config.stop_on_nonmatch:
                    raise BackendExecutionError(
                        "count (-c) with --null-data/--stop-on-nonmatch requires the 'rg' backend, "
                        "which is unavailable; refusing to return a count that ignores the flag."
                    )
                else:
                    self.backend = CPUBackend()
                    selected_backend_reason = "count_python_cpu_semantics"
```

This arm sits after the count+gpu raise arm (L336-340), so the explicit-GPU guard keeps precedence; plain `-c` without semantic flags is unchanged.

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_pipeline_count_semantics.py tests/unit/test_pipeline.py tests/unit/test_cli_modes_search_guards.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(pipeline): -c with -w/-x/-S/-m/--null-data/--stop-on-nonmatch routes to rg, not the Rust count fast path`

### Part D review focus (pinned)

- Multibyte line column = byte offset (pinned `test_column_is_byte_offset_for_multibyte_line`).
- `-S` with explicit `-i`/`-s` precedence (pinned `test_default_engine_and_explicit_case_flags_unchanged`).
- `--engine` typo now raises instead of being ignored (pinned `test_unknown_engine_fails_closed`); implementer confirms main.py's Typer option does not already reject it (`tg search "engine" src/tensor_grep/cli/main.py`).
- Without rg, regex binary-file matching errors (`BackendExecutionError`) instead of silently using Python `re` — new fail-closed path.
- Real-binary dogfood after merge (mocks prove nothing for routing): `tg search --stats --json '(a+)+b|a$' <29 a's>`, `--stats -S foo`, `--stats -c -w foo`, `--stats -c -x foo`, each compared to `rg`.

### Part D verification gates

```
uv run --no-sync ruff check <the 5 src files + 4 test files>
uv run --no-sync ruff format --check --preview <same files>
uv run --no-sync mypy src/tensor_grep
uv run --no-sync pytest tests/unit/test_medlow_json_columns.py tests/unit/test_formatters.py tests/unit/test_ripgrep_backend_field_coverage.py tests/unit/test_ripgrep_backend.py tests/unit/test_rust_core.py tests/unit/test_pipeline.py tests/unit/test_pipeline_count_semantics.py tests/unit/test_native_delegation_field_coverage.py -q
```

### Part D out of scope

CPUBackend populating `submatches` (its regex column is now omitted, not guessed) and CPUBackend's own Python-`re` evaluation (separate finding); `RustCoreBackend.search` non-count paths ignoring smart_case/word_regexp (`force_cpu_rust` arm); NDJSON column (I-11); I-05..I-12.

---
## Part E: fix(edit-ticket,installer,sarif,scan): close the edit-ticket build/ blind spot; harden installer, SARIF and scan inputs

**Findings closed:** G-03, G-08, H-01, H-03, H-04, H-08, H-09. **Dropped:** none (all confirmed by code read on main; the RED tests below are the behavioural proof).
**Risk class:** security for Task E.1 (the edit-ticket tamper gate) -> adversarial gate required; E.2-E.4 correctness/robustness.

**Files:** Modify `src/tensor_grep/cli/edit_ticket_service.py`, `src/tensor_grep/cli/agent_installer.py`, `src/tensor_grep/cli/sarif.py`, `src/tensor_grep/cli/ast_scan.py`, `docs/design/2026-09-10-agt04-symlink-junction-confinement.md` (one "implemented" note). Tests: extend `tests/unit/test_edit_ticket_population.py`, `tests/unit/test_agent_installer.py`, `tests/unit/test_sarif_output.py`; create `tests/unit/test_ast_scan_input_hardening.py`.

**Seams verified:**
- `edit_ticket_service.py :: _walk_tracked_files_bounded` (87): line 110 `dirnames[:] = [d for d in dirnames if d not in _IGNORED_DEPENDENCY_DIRS]` prunes by NAME at every depth (G-03); line 121 `item.stat()` follows symlinks.
- `edit_ticket_service.py :: compute_file_fingerprint` (76): `p.is_file()` then unguarded `open(p, "rb")` (line 81; caller line 140 unguarded) (G-08).
- Consumers: `compute_working_tree_fingerprint` (167), `build_edit_ready_ticket` (174/187), `verify_edit_ticket` (238/288). `tests/unit/test_edit_verify_root_binding.py` monkeypatches the walker -> signature stays `(root, ...) -> (files, population)`.
- `docs/design/2026-09-10-agt04-symlink-junction-confinement.md` item 4 prescribed "git ls-files primary, filesystem walk fallback". Council rounds 1-3 showed that design keeps producing adjacent holes, so this Part SUPERSEDES item 4 with a git-free walk that prunes only unambiguous dependency/cache trees (threat model below); the design doc gets a one-line supersession note. The Cache Directory Tagging signature `Signature: 8a477f597d28d172789f06886806bc55` is the published spec value (bford.info/cachedir) that cargo writes into `target/CACHEDIR.TAG`.
- `agent_installer.py :: _update_toml_codex` (105): `pattern.sub(block.strip(), content)` treats the Windows-doubled `\\` as a replacement template -> collapses to `\` -> invalid TOML on re-install (H-01).
- `agent_installer.py :: _strip_json_comments_and_trailing_commas` (53): regexes ignore string context (`"https://x"` loses everything after `//`); `_update_json_mcp` (68): `data.setdefault` with no type check (H-08).
- `sarif.py :: scan_payload_to_sarif` (247): emits a result for every finding incl. `matches == 0` / `status == "clear"` (set by `ast_scan.py :: _apply_ruleset_baseline` ~470) (H-03).
- `ast_scan.py :: _occurrence_has_inline_suppression` (403): line 416 strict UTF-8 `read_text`, catches `OSError` only (H-04). `_load_ruleset_baseline` (262) / `_load_ruleset_suppressions` (278): bare `read_text` -> `FileNotFoundError` traceback (H-09). CLI handler `main.py` ~10296-10312 catches `(ValueError, RuntimeError)` -> `Error:` exit 1; MCP `mcp_audit_tools.py` ~430-446 maps `ValueError` -> `invalid_input`.

**Contracts/governance touched:** `test_edit_ticket_population.py` pins root-level pruning of every `_IGNORED_DEPENDENCY_DIRS` name (`test_all_known_dependency_dirs_are_pruned`, `test_dependency_tree_is_pruned_before_descent`, `test_tracked_dotfile_survives_pruning`, budget tests). **Council round 3 design change:** those pins encode the G-03 bug for the AMBIGUOUS names (`build`, `dist`, `target`, `venv`, `.venv`) — under the new rule a plain `build/`/`dist/` is COVERED. Deliberately update `test_all_known_dependency_dirs_are_pruned` so it parametrises only over the unambiguous names in `_ALWAYS_PRUNED_DIRS` plus a `pyvenv.cfg` venv and a `CACHEDIR.TAG` dir, and add the inverse pins below (commit-message reason: "G-03: build/dist/target are build outputs that may hold edited source; only unambiguous dependency/cache trees are pruned"). `population_policy` `agt04-v1` -> `agt04-v2` (no test pin found); tickets minted before this change lack fingerprints for files under `build/`/`dist/` and FAIL closed on verify — intentional, noted in the PR body. `test_sarif_output.py` has no zero-match fixture (only `matches: 2` or key absent) -> stays green. MCP: a missing baseline now reaches the `ValueError` branch (generic sanitized `invalid_input` message instead of "unreadable scan path"; same code) — noted, `mcp_rewrite_tools.py` not touched.

### Threat model (E.1 — goes in the module docstring and PR body)

- **Defends against:** a cooperative-but-fallible agent that edits outside its declared scope or reports edits it did not make (pre-edit fingerprints vs current tree). **Not** a sandbox against a hostile agent (`.git/hooks`, `$HOME`, edits inside a vendored `node_modules` are out of scope; residual risk documented).
- **Design (council round 3 — replaces the round-1/2 git-listing design):** rounds 1-3 showed that a git-identity population keeps producing adjacent holes (ignored non-dependency dirs, nested repos inside ignored dirs, unbounded git output capture, `safe.directory` refusals, repo-local `.git/config` such as `core.fsmonitor`). The population is therefore a plain bounded filesystem walk with NO git dependency and a SMALL, UNAMBIGUOUS prune set:
  - pruned at any depth by NAME: `_ALWAYS_PRUNED_DIRS = {"node_modules", ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox", "site-packages"}` — names that are never source;
  - pruned at any depth by CONTENT, where the marker must be a REGULAR, non-symlink file directly inside the directory (council round 4: a symlinked marker is never followed and never counts): `pyvenv.cfg` (a Python venv, whatever its name); a `CACHEDIR.TAG` whose first bytes are the standard signature `Signature: 8a477f597d28d172789f06886806bc55` (Cache Directory Tagging spec); `.rustc_info.json` (cargo writes it unconditionally at a target-dir root — council round 4, MEASURED: this repo's maturin-created `rust_core/target` (19,682 files, ~13.1 GB, 221 files > 10 MB) has `.rustc_info.json` and NO `CACHEDIR.TAG`); `CMakeCache.txt` (a CMake build tree root);
  - EVERYTHING else is covered, at any depth: `build/`, `dist/`, `target/` without CACHEDIR.TAG, `venv/` without `pyvenv.cfg`, git-ignored files (`.env`, `secrets/`), nested repositories' working files (their `.git` is pruned by name).
- **Availability cost (decided, disclosed in the PR body):** an untagged large build output (e.g. a Gradle `build/` or Electron `dist/` with files > 10 MB and no marker) is now walked and hits the per-file/aggregate byte budget -> `population_incomplete` -> verify FAILs closed (unusable, never wrong). Main previously pruned those by name — which is exactly the G-03 hole. The availability half is restored in wave 3 by G-09 (fingerprint oversize files by size + mtime + head/tail instead of failing the population). Dogfood gate before merge: mint and verify a ticket on THIS repo by calling `edit_ticket_service.build_edit_ready_ticket(repo_root=<repo>, ...)` from a `uv run --no-sync python` one-off (the service has NO CLI/MCP front door today — verified 2026-10-03: only its own module and its unit tests reference it; `tg edit-ready` is a reserved, unimplemented command) and require `population_status.status == "complete"` with `rust_core/target` in `pruned_dirs`. Consequence for contracts: E.1's new population fields reach no CLI JSON and no MCP response, so they need no MCP contract bump (council round 5, codex question — resolved by inspection).
- **Unreadable / unbounded traversal (council round 4):** `os.walk(..., onerror=...)` records any directory-enumeration error and the population becomes `status: incomplete, reason: unreadable_path` (an unreadable subtree is never silently skipped); a directory budget `_MAX_WALK_DIRS = 200_000` stops traversal with `reason: dir_count_limit`; `pruned_dirs` collection is capped at append time.
- **In-place venvs (council round 6, disclosed residual):** a `pyvenv.cfg` in a SUBdirectory that also holds hand-edited source (e.g. `python -m venv .` run inside `tools/`) prunes that subdirectory. Rare, outside the cooperative threat model, documented in the docstring; the walk root itself is never pruned.
- **Planted markers:** a marker created inside a NEW directory together with new files hides those new files (same class as creating a new `node_modules/` today) — documented residual, outside the cooperative threat model. Creating a `CACHEDIR.TAG`/`pyvenv.cfg` inside an EXISTING source directory AFTER the ticket was minted removes that directory's files from the verify-time population; verify compares against the pre-edit fingerprints, so those files show as drift (`edit_contract_violated`) — the plant is caught. A marker planted BEFORE minting is outside the cooperative threat model (documented). Pin: add a parametrised case to `test_undeclared_edit_outside_dependency_trees_fails_verify` that writes `CACHEDIR.TAG` with the signature into `src/` after minting and asserts FAIL.
- **Bounded:** the walk is a lazy generator consumed by the existing `max_files` / per-file / aggregate byte budgets; hitting a budget yields `status: incomplete` (fails closed) without walking the rest.
- **Disclosure:** `population_status` gains `population_source: "filesystem-walk"` and `pruned_dirs` (root-relative, capped at 200 entries).
- **Symlink leaves (G-08):** fingerprint the link text (`symlink:<target>`), never follow it (no out-of-root reads, no byte-cap bypass). Directory symlinks: `os.walk(followlinks=False)` (unchanged); junction/symlinked-dir confinement remains agt04 items 1-3.

### Task E.1: edit-ticket population by unambiguous pruning; symlink-safe fingerprints (G-03, G-08)

**Interfaces:** Produces in `edit_ticket_service.py`: `_ALWAYS_PRUNED_DIRS: frozenset[str]`, `_CACHEDIR_TAG_SIGNATURE: bytes`, `_is_pruned_dir(path: Path) -> bool`, `_population_paths(root: Path, pruned: list[str]) -> Iterator[str]`. `_walk_tracked_files_bounded` signature unchanged; its population dict gains `population_source`, `pruned_dirs`; `population_policy = "agt04-v2"`.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_edit_ticket_population.py` (add `import hashlib`, `import subprocess`, `import shutil`, `from tensor_grep.cli import edit_ticket_service`; the existing file already imports `_walk_tracked_files_bounded`, `build_edit_ready_ticket`, `verify_edit_ticket`, `os`, `pytest`, `Path` — confirm and add any missing):

```python
_CACHEDIR_SIG = b"Signature: 8a477f597d28d172789f06886806bc55\n"


@pytest.mark.parametrize("rel", ["build/gen.py", "src/build/gen.py", "dist/a.py", "pkg/dist/b.py", "target/x.py", "venv/notes.py"])
def test_ambiguous_build_dirs_are_covered_at_any_depth(tmp_path: Path, rel: str) -> None:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x = 1\n", encoding="utf-8")
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert rel in files
    assert population["status"] == "complete"
    assert population["population_source"] == "filesystem-walk"


@pytest.mark.parametrize("dep", ["node_modules/m/i.js", "pkg/node_modules/m/i.js", "a/__pycache__/x.pyc", ".git/HEAD", "x/.pytest_cache/v"])
def test_unambiguous_dependency_dirs_are_pruned_at_any_depth(tmp_path: Path, dep: str) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    f = tmp_path / dep
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("1\n", encoding="utf-8")
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert set(files) == {"app.py"}
    assert population["status"] == "complete"


def test_content_marked_dirs_are_pruned_whatever_their_name(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    venv = tmp_path / "myenv"
    (venv / "lib").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    (venv / "lib" / "m.py").write_text("1\n", encoding="utf-8")
    cache = tmp_path / "target"
    (cache / "debug").mkdir(parents=True)
    (cache / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG)
    (cache / "debug" / "out.o").write_bytes(b"\x00")
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert set(files) == {"app.py"}
    assert {"myenv", "target"} <= set(population["pruned_dirs"])


def test_in_source_cmake_build_dir_is_covered_and_undeclared_edit_fails(tmp_path: Path) -> None:
    # Council round 6: CMakeCache.txt next to CMakeLists.txt = in-source build; real source lives there.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    (lib / "CMakeCache.txt").write_text("CMAKE_X:STRING=1\n", encoding="utf-8")
    src = lib / "core.c"
    src.write_text("int x;\n", encoding="utf-8")
    ticket = build_edit_ready_ticket(repo_root=str(tmp_path), target_path="app.py", query="a", allowed_files=["app.py"])
    src.write_text("int y;\n", encoding="utf-8")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["violations"] == ["lib/core.c"]


@pytest.mark.parametrize("marker", [".rustc_info.json", "CMakeCache.txt"])
def test_build_root_markers_prune_without_cachedir_tag(tmp_path: Path, marker: str) -> None:
    # Council round 4 (measured): a maturin-created cargo target/ has .rustc_info.json, no CACHEDIR.TAG.
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    target = tmp_path / "rust_core" / "target"
    (target / "debug").mkdir(parents=True)
    (target / marker).write_text("{}\n", encoding="utf-8")
    (target / "debug" / "big.rlib").write_bytes(b"\x00" * 64)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert set(files) == {"app.py"}
    assert "rust_core/target" in population["pruned_dirs"]


def test_symlinked_marker_does_not_prune_and_is_never_opened(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "CACHEDIR.TAG").write_bytes(_CACHEDIR_SIG)
    root = tmp_path / "repo"
    src = root / "src"
    src.mkdir(parents=True)
    (src / "m.py").write_text("x = 1\n", encoding="utf-8")
    try:
        (src / "CACHEDIR.TAG").symlink_to(outside / "CACHEDIR.TAG")
        (src / "pyvenv.cfg").symlink_to(outside / "CACHEDIR.TAG")
    except OSError:
        pytest.skip("symlinks unavailable")
    files, population = _walk_tracked_files_bounded(root)
    assert "src/m.py" in files  # covered: a symlinked marker never counts
    assert "src" not in population["pruned_dirs"]


def test_unreadable_subtree_makes_population_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.py").write_text("1\n", encoding="utf-8")
    (tmp_path / "locked").mkdir()
    real_walk = os.walk

    def walk_with_error(top, *a, onerror=None, **k):
        for entry in real_walk(top, *a, onerror=onerror, **k):
            yield entry
        if onerror is not None:
            onerror(PermissionError(13, "denied", str(Path(top) / "locked")))

    monkeypatch.setattr(edit_ticket_service.os, "walk", walk_with_error)
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"


def test_directory_budget_stops_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(30):
        (tmp_path / f"d{i:02d}").mkdir()  # many EMPTY dirs: file/byte budgets never trip
    monkeypatch.setattr(edit_ticket_service, "_MAX_WALK_DIRS", 10, raising=False)  # council round 8: behavioural RED on main
    _files, population = _walk_tracked_files_bounded(tmp_path)
    assert population["status"] == "incomplete"
    assert population["reason"] == "dir_count_limit"


def test_cachedir_tag_without_signature_does_not_prune(tmp_path: Path) -> None:
    d = tmp_path / "target"
    d.mkdir()
    (d / "CACHEDIR.TAG").write_bytes(b"not the signature\n")
    (d / "x.py").write_text("1\n", encoding="utf-8")
    files, _population = _walk_tracked_files_bounded(tmp_path)
    assert "target/x.py" in files


def _ticket(tmp_path: Path):
    return build_edit_ready_ticket(repo_root=str(tmp_path), target_path="app.py", query="a", allowed_files=["app.py"])


@pytest.mark.parametrize("rel", ["build/gen.py", ".env", "secrets/k.txt", "sub/build/gen.py"])
def test_undeclared_edit_outside_dependency_trees_fails_verify(tmp_path: Path, rel: str) -> None:
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".env\nsecrets/\n", encoding="utf-8")  # git-ignore is irrelevant now
    (tmp_path / "sub" / ".git").mkdir(parents=True)  # a nested repo's .git is pruned, its files are covered
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old\n", encoding="utf-8")
    ticket = _ticket(tmp_path)
    target.write_text("new\n", encoding="utf-8")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=[])
    assert result["verdict"] == "FAIL"
    assert result["reason"] == "edit_contract_violated"
    assert result["violations"] == [rel]


def test_declared_edit_in_build_dir_passes(tmp_path: Path) -> None:  # positive control
    (tmp_path / "app.py").write_text("a = 1\n", encoding="utf-8")
    gen = tmp_path / "build" / "gen.py"
    gen.parent.mkdir()
    gen.write_text("x = 1\n", encoding="utf-8")
    ticket = build_edit_ready_ticket(
        repo_root=str(tmp_path), target_path="app.py", query="a", allowed_files=["app.py", "build/gen.py"]
    )
    gen.write_text("x = 2\n", encoding="utf-8")
    result = verify_edit_ticket(repo_root=str(tmp_path), ticket=ticket, modified_files=["build/gen.py"])
    assert result["verdict"] == "PASS"


def test_enumeration_stops_at_max_files_without_walking_the_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # UNIT COVERAGE of the new lazy generator, NOT RED evidence (council round 9): on main
    # `_population_paths` does not exist, so this fails with AttributeError there. E.1's
    # behavioural RED is the ambiguous-directory, undeclared-edit and unreadable-path tests.
    for i in range(50):
        (tmp_path / f"f{i:02d}.txt").write_text("x\n", encoding="utf-8")
    seen = {"n": 0}
    real = edit_ticket_service._population_paths

    def counting(*a: object, **k: object):
        for p in real(*a, **k):
            seen["n"] += 1
            yield p

    monkeypatch.setattr(edit_ticket_service, "_population_paths", counting)
    _files, population = _walk_tracked_files_bounded(tmp_path, max_files=5)  # real kwarg (edit_ticket_service.py:90)
    assert population["status"] == "incomplete"
    assert population["reason"] == "file_count_limit"
    assert seen["n"] <= 6  # lazy: stopped right after the limit


def test_oversize_symlink_target_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x" * 5000)
    root = tmp_path / "repo"
    root.mkdir()
    try:
        # RELATIVE target (14 chars) so the link-text size stays under the 100-byte cap on Windows,
        # where absolute tmp paths exceed 100 chars (council round 1).
        (root / "link.bin").symlink_to(Path("..") / "outside.bin")
    except OSError:
        pytest.skip("symlinks unavailable")
    files, population = _walk_tracked_files_bounded(root, max_file_bytes=100)
    assert population["status"] == "complete"
    assert files["link.bin"] == hashlib.sha256(b"symlink:" + os.fsencode(os.readlink(root / "link.bin"))).hexdigest()


def test_unreadable_fingerprint_marks_incomplete_not_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.py").write_text("1\n", encoding="utf-8")

    def _boom(_p: object) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(edit_ticket_service, "compute_file_fingerprint", _boom)
    files, population = _walk_tracked_files_bounded(tmp_path)
    assert files == {}
    assert population["status"] == "incomplete"
    assert population["reason"] == "unreadable_path"
```

(`venv/notes.py` is covered because a dir named `venv` WITHOUT `pyvenv.cfg` is not a venv; a real venv is pruned by content. `subprocess`/`shutil` imports are unused after the redesign — omit them.)

- [ ] **Step 2: Run to verify RED** `uv run --no-sync pytest tests/unit/test_edit_ticket_population.py -q` -> the ambiguous-dir tests fail (`'build/gen.py' in {...}` false / `KeyError: 'population_source'`); the content-marked test fails (`myenv/lib/m.py` and `target/debug/out.o` present); `test_undeclared_edit_outside_dependency_trees_fails_verify[build/gen.py]` and `[sub/build/gen.py]` return PASS (the G-03 hole); the symlink test fails `per_file_byte_limit`; the unreadable test raises `PermissionError`. `.env`/`secrets/k.txt` cases already FAIL correctly on main (covered today) — they are regression pins for the redesign. The updated `test_all_known_dependency_dirs_are_pruned` passes on main (it no longer includes the ambiguous names). `test_enumeration_stops_at_max_files_without_walking_the_rest` fails `AttributeError: _population_paths` on main — unit coverage of the new generator, not RED evidence (council round 9).
- [ ] **Step 3: Minimal implementation** (`edit_ticket_service.py`; add `from collections.abc import Iterator`):

```python
_ALWAYS_PRUNED_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".nox", "site-packages",
})
_CACHEDIR_TAG_SIGNATURE = b"Signature: 8a477f597d28d172789f06886806bc55"
_BUILD_ROOT_MARKERS = ("pyvenv.cfg", ".rustc_info.json")
# CMakeCache.txt counts ONLY when the directory has no sibling CMakeLists.txt (council round 6):
# an IN-SOURCE CMake build writes the cache next to the source tree's own CMakeLists.txt, and
# pruning that directory would hide real source from the population.
_CMAKE_CACHE = "CMakeCache.txt"
_CMAKE_SOURCE = "CMakeLists.txt"
_MAX_REPORTED_PRUNED = 200
_MAX_WALK_DIRS = 200_000


class _PopulationWalkError(Exception):
    """Directory enumeration failed or the dir budget was hit: the population is incomplete."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _regular_marker(path: Path) -> bool:
    """A marker counts only as a REGULAR, non-symlink file (never follow a marker link)."""
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _is_pruned_dir(path: Path) -> bool:
    """Unambiguous dependency/cache/build-root trees only (G-03: build/dist/target may hold source)."""
    if path.name in _ALWAYS_PRUNED_DIRS:
        return True
    if any(_regular_marker(path / marker) for marker in _BUILD_ROOT_MARKERS):
        return True
    if _regular_marker(path / _CMAKE_CACHE) and not (path / _CMAKE_SOURCE).exists():
        return True  # out-of-source CMake build tree only
    tag = path / "CACHEDIR.TAG"
    if _regular_marker(tag):
        try:
            with open(tag, "rb") as handle:
                return handle.read(len(_CACHEDIR_TAG_SIGNATURE)) == _CACHEDIR_TAG_SIGNATURE
        except OSError:
            return False  # cannot classify -> walk it (covered, fail-closed by budget)
    return False


def _population_paths(root: Path, pruned: list[str]) -> Iterator[str]:
    """Lazy, sorted-per-directory walk; `pruned` is filled (root-relative, capped) as it proceeds.

    Raises _PopulationWalkError on any directory-enumeration error (never silently skip a
    subtree) or when more than _MAX_WALK_DIRS directories are visited."""
    def _on_error(exc: OSError) -> None:
        raise _PopulationWalkError("unreadable_path") from exc

    visited = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=_on_error):
        visited += 1
        if visited > _MAX_WALK_DIRS:
            raise _PopulationWalkError("dir_count_limit")
        current = Path(dirpath)
        rel_dir = current.relative_to(root)
        keep: list[str] = []
        for d in sorted(dirnames):
            if _is_pruned_dir(current / d):
                if len(pruned) < _MAX_REPORTED_PRUNED:
                    pruned.append((rel_dir / d).as_posix())
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            yield (rel_dir / name).as_posix()
```

(Add `import stat` to `edit_ticket_service.py`. `_MAX_WALK_DIRS` is a module constant so tests can monkeypatch it.)

```python
def compute_file_fingerprint(path: str | Path) -> str:
    p = Path(path)
    if p.is_symlink():
        # Never follow a leaf link: its target may be out-of-root or huge (G-08).
        return hashlib.sha256(b"symlink:" + os.fsencode(os.readlink(p))).hexdigest()
    if not p.is_file():
        return ""
    ...  # unchanged hashing loop; OSError now propagates to the walker, which handles it
```

(Keep the existing hashing algorithm/chunk size — only the symlink branch and the unguarded-open contract change.)

Walker: replace the `os.walk` loop with `pruned: list[str] = []` and a single `for rel in _population_paths(root, pruned):` loop wrapped in `try: ... except _PopulationWalkError as exc: incomplete_reason = incomplete_reason or exc.reason` (the population dict then reports `status: incomplete` with that reason; fingerprints gathered so far are kept, exactly like the existing budget breaks), `item = root / rel`; keep the existing budget logic (its inner `break`s now break this single loop; remove the old outer `if incomplete_reason is not None: break`). Size read:

```python
            try:
                size = len(os.readlink(item)) if item.is_symlink() else item.stat().st_size
            except OSError:
                incomplete_reason = incomplete_reason or "unreadable_path"
                continue
```

Wrap `result[rel] = compute_file_fingerprint(item)` in `try` / `except OSError:` -> `incomplete_reason = incomplete_reason or "unreadable_path"; continue`. After the loop, both population dicts gain `"population_source": "filesystem-walk"`, `"pruned_dirs": sorted(pruned)[:_MAX_REPORTED_PRUNED]`, `"population_policy": "agt04-v2"`. `_IGNORED_DEPENDENCY_DIRS` is no longer used by the walker — keep the constant only if another module imports it (`tg search "_IGNORED_DEPENDENCY_DIRS" src tests`), else delete it. Module docstring gets the threat model above. Add to the agt04 design doc item 4: "Superseded 2026-10 (bug hunt G-03): unambiguous name/content pruning, no git dependency."

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_edit_ticket_population.py tests/unit/test_edit_ticket_service.py tests/unit/test_edit_verify_root_binding.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(edit-ticket): cover build/dist/target and ignored files; prune only unambiguous dependency/cache trees; never follow symlink leaves`

### Task E.2: installer TOML/JSON hardening (H-01, H-08)

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/test_agent_installer.py` (add `import tomllib`):

```python
def test_codex_reinstall_keeps_toml_valid_with_windows_path(fake_home, monkeypatch):
    from tensor_grep.cli import agent_installer

    win = "C:\\Users\\me\\.venv\\Scripts\\tg.exe"
    monkeypatch.setattr(agent_installer, "_resolve_tg_command", lambda: win)
    install_agent_integration("codex", home_dir=fake_home)
    install_agent_integration("codex", home_dir=fake_home)
    data = tomllib.loads((fake_home / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert data["mcp_servers"]["tensor_grep"]["command"] == win


def test_json_config_non_object_is_refused_cleanly(fake_home):
    cfg = fake_home / ".claude.json"
    cfg.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        install_agent_integration("claude", home_dir=fake_home)
    assert cfg.read_text(encoding="utf-8") == "[]"
    cfg.write_text('{"mcpServers": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="mcpServers"):
        install_agent_integration("claude", home_dir=fake_home)


@pytest.mark.parametrize("body", ['{"n": 1/*c*/2}', '{"n": 1} /* never closed'])
def test_malformed_jsonc_is_refused_not_rewritten(fake_home, body):
    # council round 6: a block comment must not glue tokens, and an unterminated one must not vanish
    cfg = fake_home / ".claude.json"
    cfg.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        install_agent_integration("claude", home_dir=fake_home)
    assert cfg.read_text(encoding="utf-8") == body


def test_jsonc_urls_and_escaped_quotes_in_strings_survive(fake_home):
    cfg = fake_home / ".claude.json"
    cfg.write_text(
        '{\n  // keep\n  "$schema": "https://example.com/s.json",\n'
        '  "note": "a,]b /* x */ \\" // y",\n  "mcpServers": {},\n}\n',
        encoding="utf-8",
    )
    install_agent_integration("claude", home_dir=fake_home)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["$schema"] == "https://example.com/s.json"
    assert data["note"] == 'a,]b /* x */ " // y'
    assert "tensor_grep" in data["mcpServers"]
```

(If the existing fixture/target names differ — `fake_home`, `"claude"` -> `~/.claude.json`, `"codex"` -> `~/.codex/config.toml`, `_resolve_tg_command` — the implementer adapts to the names the existing tests in this file use; check with `tg search "def fake_home|_resolve_tg_command|claude.json" tests/unit/test_agent_installer.py src/tensor_grep/cli/agent_installer.py`.)

- [ ] **Step 2: Run to verify RED** `uv run --no-sync pytest tests/unit/test_agent_installer.py -q` -> `TOMLDecodeError` (invalid `\U` escape), `AttributeError` (not `ValueError`), `ValueError: Cannot safely parse` (https stripped).
- [ ] **Step 3: Minimal implementation** — H-01: `new_content = pattern.sub(lambda _m: block.strip(), content)` (also wrap the two `GUIDANCE_BLOCK` `sub()` calls ~139/~147 in lambdas, same class). H-08: replace the stripper with a string-aware two-pass scanner using an explicit escape flag:

```python
def _strip_json_comments_and_trailing_commas(text: str) -> str:
    s = text.lstrip("\ufeff")
    out: list[str] = []
    i, n = 0, len(s)
    in_str = escaped = False
    while i < n:  # pass 1: drop // and /* */ comments outside strings
        c = s[i]
        if in_str:
            out.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif s.startswith("//", i):
            while i < n and s[i] not in "\r\n":
                i += 1
        elif s.startswith("/*", i):
            end = s.find("*/", i + 2)
            if end == -1:
                # council round 6: never silently drop an unterminated comment (and what follows)
                raise ValueError("Cannot safely parse configuration: unterminated /* comment")
            out.append(" ")  # a comment separates tokens: `1/*c*/2` must NOT become `12`
            i = end + 2
        else:
            out.append(c)
            i += 1
    s = "".join(out)
    res: list[str] = []
    in_str = escaped = False
    for k, c in enumerate(s):  # pass 2: drop trailing commas outside strings
        if in_str:
            res.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "," and s[k + 1 :].lstrip()[:1] in ("]", "}"):
            continue
        res.append(c)
    return "".join(res)
```

In `_update_json_mcp`, after parsing:

```python
    if not isinstance(data, dict):
        raise ValueError(f"Configuration file '{path}' must contain a JSON object; refusing to modify it.")
    if "mcpServers" in data and not isinstance(data["mcpServers"], dict):
        raise ValueError(f"'mcpServers' in '{path}' must be a JSON object; refusing to modify it.")
```

(Installer callers already surface `ValueError` cleanly — `install_command` catches `ValueError`/`OSError`.)

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_agent_installer.py tests/unit/test_cli_atomic_writer_ratchet.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(install): keep Codex TOML valid on reinstall; string-aware JSONC; refuse non-object configs`

### Task E.3: SARIF results only for rules that matched (H-03)

- [ ] **Step 1: Write the failing test** — append to `tests/unit/test_sarif_output.py` (uses the file's existing `_payload` / `_VERSION` helpers):

```python
def test_zero_match_rules_are_catalogued_but_emit_no_result() -> None:
    clear = {"rule_id": "py-clear", "language": "python", "severity": "high", "message": "m",
             "matches": 0, "status": "clear", "files": [], "fingerprint": "b" * 64, "evidence": []}
    base = _payload()["findings"][0]
    doc = scan_payload_to_sarif(_payload(findings=[base, clear]), tool_version=_VERSION)
    run = doc["runs"][0]
    assert [r["ruleId"] for r in run["results"]] == [base["rule_id"]]
    assert {r["id"] for r in run["tool"]["driver"]["rules"]} == {base["rule_id"], "py-clear"}
    for result in run["results"]:
        assert run["tool"]["driver"]["rules"][result["ruleIndex"]]["id"] == result["ruleId"]
```

- [ ] **Step 2: Run to verify RED** `uv run --no-sync pytest tests/unit/test_sarif_output.py::test_zero_match_rules_are_catalogued_but_emit_no_result -q` -> results contain `py-clear`.
- [ ] **Step 3: Minimal implementation** — `sarif.py`, after the `rules.append(rule)` block, before building `result`:

```python
        matches_value = finding.get("matches")
        if finding.get("status") == "clear" or (
            isinstance(matches_value, int) and not isinstance(matches_value, bool) and matches_value == 0
        ):
            # Catalogued in driver.rules above; a rule that did not fire is not an alert.
            continue
```

(Findings without a `matches` key stay emitted; `ruleIndex` stays correct because the rule is appended before the skip.)

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_sarif_output.py tests/unit/test_sarif_scan_integration.py tests/unit/test_w1c_sarif_version_disclosure.py -q` -> PASS.
- [ ] **Step 5: Commit** `fix(sarif): emit results only for rules that matched; keep zero-match rules in driver.rules`

### Task E.4: scan input hardening (H-04, H-09)

- [ ] **Step 1: Write the failing tests** — create `tests/unit/test_ast_scan_input_hardening.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import ast_scan


def test_inline_suppression_survives_non_utf8_bytes(tmp_path: Path) -> None:
    (tmp_path / "lat.py").write_bytes(b"# tg-ignore: rule-x\nx = '\xe9'\n")
    cache: dict[str, list[str]] = {}
    assert ast_scan._occurrence_has_inline_suppression(
        occurrence_file="lat.py", occurrence_line=2, rule_id="rule-x",
        language="python", root_dir=tmp_path, source_cache=cache,
    ) is True
    assert ast_scan._occurrence_has_inline_suppression(
        occurrence_file="lat.py", occurrence_line=2, rule_id="other",
        language="python", root_dir=tmp_path, source_cache=cache,
    ) is False


@pytest.mark.parametrize("loader", ["_load_ruleset_baseline", "_load_ruleset_suppressions"])
def test_missing_dir_or_bad_json_ruleset_input_is_clean_value_error(tmp_path: Path, loader: str) -> None:
    fn = getattr(ast_scan, loader)
    with pytest.raises(ValueError, match="could not be read"):
        fn(str(tmp_path / "nope.json"))
    with pytest.raises(ValueError, match="could not be read"):
        fn(str(tmp_path))  # a directory
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        fn(str(bad))
```

(Adapt the `_occurrence_has_inline_suppression` keyword names to its real signature at ast_scan.py:403 if they differ.)

- [ ] **Step 2: Run to verify RED** -> `UnicodeDecodeError`, `FileNotFoundError` / `IsADirectoryError` / `PermissionError` (not `ValueError`), raw `JSONDecodeError` message mismatch.
- [ ] **Step 3: Minimal implementation** — `ast_scan.py` line 416: `read_text(encoding="utf-8", errors="replace").splitlines()`. Helper above `_load_ruleset_baseline`:

```python
def _read_ruleset_json(path: str, label: str) -> tuple[Path, object]:
    resolved = Path(path).expanduser().resolve()
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Ruleset {label} file '{resolved}' could not be read: {exc}") from exc
    try:
        return resolved, json.loads(text)
    except ValueError as exc:
        raise ValueError(f"Ruleset {label} file '{resolved}' is not valid JSON: {exc}") from exc
```

Each loader: `baseline_path, payload = _read_ruleset_json(path, "baseline")` / `suppressions_path, payload = _read_ruleset_json(path, "suppressions")`.

- [ ] **Step 4: Run** `uv run --no-sync pytest tests/unit/test_ast_scan_input_hardening.py tests/unit/test_mcp_server_ruleset_scan.py tests/unit/test_cli_modes_ast_backend.py -q -k "baseline or suppress or hardening"` -> PASS.
- [ ] **Step 5: Commit** `fix(scan): non-UTF-8 sources and missing baseline/suppressions files no longer abort the scan`

### Part E verification gates

```
uv run --no-sync ruff check src/tensor_grep/cli/edit_ticket_service.py src/tensor_grep/cli/agent_installer.py src/tensor_grep/cli/sarif.py src/tensor_grep/cli/ast_scan.py tests/unit/test_edit_ticket_population.py tests/unit/test_agent_installer.py tests/unit/test_sarif_output.py tests/unit/test_ast_scan_input_hardening.py
uv run --no-sync ruff format --check --preview <same files>
uv run --no-sync mypy src/tensor_grep
uv run --no-sync pytest tests/unit/test_edit_ticket_population.py tests/unit/test_edit_ticket_service.py tests/unit/test_edit_verify_root_binding.py tests/unit/test_agent_installer.py tests/unit/test_sarif_output.py tests/unit/test_sarif_scan_integration.py tests/unit/test_ast_scan_input_hardening.py -q
```

Adversarial gate prompt for E.1: "make `verify_edit_ticket` return PASS after an undeclared edit to any file outside the unambiguous prune set — `build/`/`dist/`/`target/` at any depth, git-ignored files and dirs, nested repos, symlinked files, deleted files, a fake `CACHEDIR.TAG` or `pyvenv.cfg` planted to hide a source dir, a directory named like a cache, an unreadable file, a budget hit."

### Part E out of scope

Symlinked-directory/junction confinement (agt04 items 1-3); `.git/hooks`/`.git/config` tamper detection; H-02, H-05..H-07, H-10..H-12; exit-code semantics of `tg scan` input errors; `mcp_rewrite_tools.py` message tuning.

---

## Execution and merge order

- Five PRs, one per Part, each built by a Sonnet subagent in its own git worktree (`isolation: worktree`), TDD per task, commits per task.
- File sets are disjoint across Parts (A: diff_impact; B: session_daemon/session_store/conftest; C: mcp_server/mcp_audit_tools/repo_map/ast_wrapper_backend; D: formatters/ripgrep_backend/rust_backend/pipeline; E: edit_ticket_service/agent_installer/sarif/ast_scan). The only shared-surface item is the MCP contract version, owned by Part C alone.
- After each build: orchestrator re-runs the Part's verification gates in the canonical Windows venv (worktree self-reports are hypotheses), then an independent `codex exec -m gpt-6.1-sol` (reasoning effort low) adversarial audit of the exact diff (mandatory: every Part is security-class or touches a security-class surface). Findings -> fix -> re-audit until SHIP.
- PR titles are `fix(<scope>): ...` (release-bearing patch). Merge discipline: burst-then-hold — when no release-bearing main run is in flight, merge every CI-green, gate-SHIP PR in one burst; then merge nothing until that run's `chore(release)` commit and PyPI publish land.
- After merge: re-run each Part's dogfood probe against the merged main / published wheel; update `docs/audits/2026-10-03-bughunt-tracker.md`.
