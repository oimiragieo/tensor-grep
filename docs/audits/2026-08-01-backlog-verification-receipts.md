# 2026-08-01 — backlog verification receipts (pre-campaign ground truth)

Orchestrator-run verification of every open item on `docs/TASK_BOARD.md` and the 2026-07-31 deep-dive
table, against the real code at `main` (tip `0126cb3`, live PyPI **v1.101.27**).

**Headline: the board is badly stale.** Of the items listed open, the large majority are already
fixed, already refuted, or deliberate-by-design. This is the FOURTH time this board has gone stale in
the same way (its own header documents the previous three). The staleness is itself the finding.

Method note: every "0" below is paired with a positive control proving the instrument can return
non-zero. An unresolved zero is labelled as such.

---

## ALREADY FIXED / REFUTED — do not build

| item | verdict | evidence |
|---|---|---|
| `--quiet` dropped by rg-passthrough | **FIXED** by `cfc3264` | `-q` moved out of shared `_build_cmd` into streaming-only `search_passthrough` (`backends/ripgrep_backend.py:491-511`); `test_quiet_survives_rg_passthrough.py` covers both arms |
| `AGENTS.md` stale on the argv sweep | **REFUTED** | `AGENTS.md:1796-1811` already records the sweep-is-now-a-test conversion and the `_agent_gpu_evidence` hole #872 found |
| #115 / #125 | **BOARD IS THE STALE ONE** | `docs/BACKLOG.md:887` ("KILLED / mark CLOSED") is correct; `docs/TASK_BOARD.md:199-200` still lists them open |
| #15 MaxSim doc-honesty | **FIXED** | docstring now states MaxSim is "NOT REACHABLE BY A DOCUMENTED PATH" (`cli/main.py:4758-4771`); test now asserts real order inversion (`tests/unit/test_find_command.py:490-498`) |
| #858 codemap symlink write | **FIXED** | `codemap.py:820` delegates to guarded `atomic_write_bytes` |
| #859 Form-1 writer ratchet | **EXISTS** | `tests/unit/test_codemap_write_refuses_symlink.py:51-57` |
| #862 GPU-evidence argv sentinel | **FIXED** | `agent_capsule.py:1740-1741` appends `--` unconditionally |
| #860b tip-stamp lag | **FIXED** | `AGENTS.md:251` reads `v1.101.27`, matching live |
| `--ndjson` zero-match silence | **BY DESIGN** | Python emits nothing on a complete zero-match to avoid training readers to expect a record every run; divergence from Rust is documented at `core/json_fmt.py:265-271` |
| 3 × `main.rs` envelope literals untested | **2 of 3 TESTED** | `SearchSummaryNdjson` and `SearchResultJson` have serialization tests; `GpuNativeSearchResultJson` is `#[cfg(feature="cuda")]` and its exclusion is justified in-code (`cuda-feature-check` runs `cargo check`, not `cargo test`) |

---

## CONFIRMED OPEN

### 1. `--ltl` invalid query escapes as a raw traceback — MED, user-facing

The only item with genuine user-facing harm. A user following `tg search --help` (which documents
`--ltl` as "Interpret PATTERN as a temporal query (supports: 'A -> eventually B')") and mistyping the
query gets ~25 lines of Python traceback instead of the CLI's normal clean error.

Three arms, one variable each, all run against the real installed entry point
(`tensor_grep.__file__` asserted as the repo `src/` tree first):

```
ARM A  tg search "def -> eventually return" --ltl src/tensor_grep/sidecar.py
       -> matches printed, exit 0                          (feature WORKS)

ARM B  tg search "def " --ltl src/tensor_grep/sidecar.py
       -> Traceback ... ValueError: Unsupported LTL query. Use: 'A -> eventually B'
       -> exit 1                                            (THE DEFECT)

ARM C  tg search "def " --rank /nonexistent-path-xyz        (convention control)
       -> Error: search path does not exist: ...
       -> exit 2                                            (the house convention)
```

Raise site `backends/cpu_backend.py::_compile_ltl`, reached via `_search_ltl` <- `search`.
ARM C establishes that a clean `Error:` + exit 2 IS the established convention for user-input errors;
`_compile_ltl` bypasses it.

**A prior agent misdiagnosed this as a clap-reject caused by the missing Rust registration (item 2).
That is wrong** — ARM B's traceback shows `bootstrap.py::_run_full_cli` -> `cli/main.py` ->
`backends/cpu_backend.py`, i.e. bootstrap routed to Python exactly as designed. Fixing Rust would not
have touched this. Recorded because the misdiagnosis would have sent an implementer to the wrong file.

### 2. `--ltl` missing from the Rust front door — LOW, latent

`SEARCH_PYTHON_PASSTHROUGH_FLAGS` in `rust_core/src/main.rs` does not list `--ltl`. Positive control:
`--rank` IS in that list at `:314`; the `--ltl` grep returns zero against the same file. `--ltl` IS
registered at the other front door (`cli/bootstrap.py:67` and `:525`).

Latent, not live — bootstrap intercepts before the Rust binary sees it (proven by item 1's traceback
path). Still a divergence between the two front doors the "2 front doors for a search flag" law says
must agree. Fix-or-retire is a judgement call for the plan.

### 3. Disclosure docstring lie — LOW, docs

`cli/main.py::_completeness_caveat_lines` (~:11651-11653) claims `map` / `context` /
`context-render` / `edit-plan` / `blast-radius-render` / `blast-radius-plan` "exit 2 while saying
nothing in text at all". All now call `_emit_scan_incompleteness_banner` (map ~:8813, context ~:9378,
edit-plan ~:10163). The docstring documents a gap that was closed.

### 4. DD-003 — `docs/CONTRACTS.md` contradicts itself — LOW, docs

`:253` says `record`/`find` do NOT canonicalize PATH to the nearest `.git` ancestor. `:257` says they
canonicalize on the SAME terms as Slice 1. Code settles it — both DO canonicalize via
`_ledger_physical_root` (`ledger_store.py:48-57`, call sites `:1198`, `:1335`). `:253` is the lie.

This is the "documents contradict themselves" class: one file holding a claim AND its refutation, with
no gate able to compare a doc to itself.

### 5. Daemon tokenless `is_authorized` fail-open — LOW, security

`cli/session_daemon.py:1765-1766`:

```python
def is_authorized(self, request: dict[str, Any]) -> bool:
    if not self.token:
        return True
```

Production sets a token, so this is not a live exposure. Needs a decision: fail closed, or pin the
current behaviour as intentional with a test that states why. Undecided is the worst of the three.

### 6. `apply_policy.py:707` argv without a `--` sentinel — LOW

`argv = [str(resolved_path), *argv[1:]]`; Rust sibling `rust_core/src/main.rs:11045-11047` likewise.
Paths are absolute in both, so the flag-injection shape is not currently reachable.

### 7. Dead code `sidecar.py::_classify_lines` — LOW, CONFIRMED by three methods

`sidecar.py:157-159`, a thin wrapper around `_classify_lines_with_metadata` that discards the metadata.

| method | result | control |
|---|---|---|
| `tg callers . _classify_lines` | `callers=0 files=0 import_consumers=0` | `_classify_lines_with_metadata` -> `callers=4 files=3` — instrument works |
| `tg refs . _classify_lines` | `references=0 files=0` | — |
| grep over `git ls-files` only | only the DEFINITION at `sidecar.py:157` + 3 doc mentions | grep returned other rows, proving it functions |

`tg` itself emitted the correct caveat unprompted: *"0 callers in the static call graph does not mean
this symbol is dead code... Cross-check with `tg refs` or grep."* The product being honest about its
own blind spot is why this was cross-checked rather than trusted.

**Unresolved zero, labelled:** a grep for the name as a STRING literal (dynamic dispatch) returned
empty for the target AND for the control, so that particular probe discriminates nothing. The three
methods above are the basis for the deadness claim, not that one.

---

## Instrument failures encountered while producing this document

Recorded because in this repo the probe is wrong more often than the subject.

1. **The first `--ltl` probe measured my own bad path, not the flag.** Both arms failed identically
   with `search path does not exist` because I guessed `src/tensor_grep/cli/sidecar.py`; the file is
   at `src/tensor_grep/sidecar.py`. The control arm failing the SAME way is what exposed it — a
   single-arm run would have been written down as "`--ltl` is broken".
2. **I BLAMED A TOOL FOR MY OWN LOG-FILE COLLISION, AND KILLED A HEALTHY RUN OVER IT.**
   *(This entry originally read "the codex dispatch ran against the wrong repository ... the WRAPPER
   substituted the arguments". That was WRONG. The correction is the entry.)*

   I dispatched `codex-dispatch.ps1` with explicit `-SpecPath`/`-WorkDir` under
   `C:/dev/projects/tensor-grep`, redirecting to `/tmp/codex_logs/backlog_plan.log`. Tailing that log
   showed `spec:`/`work:` pointing at `C:/dev/projects/omega-jarvis`, and I concluded the wrapper had
   substituted my arguments, killed the run, and re-dispatched.

   **Six lines below the header I stopped reading, the same log said:**

   ```
   OpenAI Codex v0.144.5
   workdir: C:\dev\projects\tensor-grep      <- codex's OWN resolved workdir
   ```

   Codex was running in the correct repository the entire time. What actually happened:
   a CONCURRENT session (WSL-side) was running its own codex job for omega-jarvis and, following the
   `use-codex` skill's documented pattern, wrote `/tmp/codex_logs/backlog_plan.log` **and**
   `backlog_plan.pid`. I chose the same generic basename in the same shared directory. Proof it was
   not mine: `backlog_plan.pid` exists, contains PID `1035986` (a WSL-range pid), and **my dispatch
   never wrote a `.pid` file at all** — my command was a bare `> ...log 2>&1`.

   Three compounding errors, in order of cost:
   - **Read a shared, colliding log as if it were exclusively mine.** Generic filenames in a shared
     `/tmp` on a box with concurrent agents is the collision; the skill's own example uses
     `<name>.log`, which invites it.
   - **Stopped reading at the line that confirmed my hypothesis.** The refuting line was six rows
     down, in the same output, already on screen.
   - **`Get-Process codex | Stop-Process -Force` is not scoped to my run.** It kills every codex
     process on the box. Having misdiagnosed a healthy run, I then killed it — and possibly the
     concurrent session's run with it.

   **Rules taken from this:** name dispatch logs with a run-unique suffix, never a bare topic name,
   whenever a shared temp dir may host another agent; when a tool appears to have ignored explicit
   arguments, read the tool's OWN echo of its resolved state before accusing it; and never
   `Stop-Process` by image name on a shared box — kill by the PID you captured.

   The genuine, unchanged lesson: verify a report's provenance before consuming it. It just applied
   to my reading of the log, not to the wrapper's behaviour.
3. **Local lint disagrees with CI and must not be "fixed".** `uv run --no-sync ruff check .` reports
   2 errors and `ruff format --check --preview .` reports 4 files, on a tree CI is green on. Cause:
   the venv carries ruff **0.16.0** while `pyproject.toml:632` pins **`ruff==0.15.20`** (confirmed in
   `uv.lock`). CI's `uv run` syncs to the lock. Reformatting to satisfy the local binary would redden
   main. Second occurrence of this trap in this workspace.

## APPENDED CORRECTION — #859 (2026-08-02)

The codemap-only test did not satisfy the class-level population contract. The original receipt above
proved one concrete output site, not every site that builds the secure-writer record class. Treat that
receipt as historical evidence for the tested site only. Backlog #859 is `READY`: Task 3 must census and
pin all constructors, aliases, generated-source sites, shadow implementations, and mutation paths before
changing production code.
