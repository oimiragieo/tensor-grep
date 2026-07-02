---
name: tensor-grep-debugging-playbook
description: Use when a tensor-grep (tg) run fails, hangs, returns wrong/empty/silently-degraded results, a CI check goes red, or a release doesn't publish. Symptom-to-triage table, each row giving a discriminating experiment and a fix pointer, for CI red, release not published (push-race), search hangs/slow, silent-empty result (fail-closed contract), argv/flag injection, mock-green-but-real-dead FFI, dependency-cap silent downgrade, and ranking flip. Load BEFORE theorizing from a traceback or re-running a failing gate blind.
---

# tensor-grep Debugging Playbook

A symptom-first runbook for the recurring ways `tg` (or its CI/release pipeline) breaks. Every
row below was a real, previously-diagnosed failure in this repo — not a hypothetical. The single
biggest time-waster on record is **theorizing from a stack trace instead of reading the structured
failure first**: a README rewrite once cost 4 CI cycles because the team guessed at causes from
tracebacks instead of decoding which CI check actually failed (`AGENTS.md`). Do not repeat that.

## When NOT to use this skill

This is a *triage* skill (symptom → cause → experiment → fix pointer), not a how-to or a history
book. Reach for a sibling instead when:

| You need... | Use instead |
|---|---|
| The 4 registration sites for a new command/flag, PR-title→release-intent rules, what you may not edit | `tensor-grep-change-control` |
| The full postmortem of a *settled* battle (PyO3 FFI revert, README-rewrite gate break, fork-bomb binary disable) | `tensor-grep-failure-archaeology` |
| The architecture of the `ComputeBackend` contract / registration system itself, not just "how do I diagnose a violation" | `tensor-grep-architecture-contract` |
| Env var reference (`TG_RG_TIMEOUT_SECONDS`, `TG_SESSION_MAX`, …) beyond the ones a failure mode below needs | `tensor-grep-config-and-flags` |
| Toolchain/build setup (cargo off `PATH`, `maturin develop`, Windows gotchas) unrelated to a live failure | `tensor-grep-build-and-env` |
| `tg doctor` / `tg dogfood` field-by-field reference | `tensor-grep-diagnostics-and-tooling` |
| Local validation gate command reference (ruff/mypy/pytest) as a checklist, not a debug session | `tensor-grep-validation-and-qa` |
| Full release-and-positioning procedure, not "why didn't THIS release publish" | `tensor-grep-release-and-positioning` |

If your symptom isn't in the table below, it's probably not covered here — check
`tensor-grep-failure-archaeology` for a prior occurrence before assuming it's novel.

## Jargon, defined once

- **Front door** — the entry point argv must pass through to be routed correctly. `tg`'s Python
  front door is `tensor_grep.cli.bootstrap:main_entry`; it intercepts plain-text searches and
  forwards them to `rg` *before* the Typer app sees argv. `CliRunner` in tests calls the Typer app
  directly and **bypasses this front door**, so a routing bug can be invisible to green unit tests.
- **Fail-closed** — on a real failure, raise/error instead of silently returning a clean-looking
  empty result or swapping to an engine that can't honor the requested semantics.
- **Push-race** — two `main`-bound merges overlapping so the second `git push origin main` from an
  in-flight semantic-release job is rejected non-fast-forward.
- **Registration site** — one of several places a new command/flag/route must be added; missing
  one makes it silently misroute instead of erroring loudly.
- **argv/flag injection (CWE-88)** — a user- or LLM-controlled value that begins with `-` gets
  parsed by a subprocess's *own* argument parser as a flag instead of as data, even when the
  parent process used list-argv (`shell=False`), which only stops *shell* injection.

## Triage table

| Symptom | Likely cause | Discriminating experiment | Fix pointer |
|---|---|---|---|
| CI check is red, unclear why | Wrong assumption from the traceback instead of the actual failing check (e.g. registration-completeness gate, not the code you touched) | `gh pr checks <PR>` → find the *named* failing job, then `gh run view <run-id> --json jobs` → `gh run view <run-id> --log-failed` | [§1](#1-ci-red-decode-the-structured-check-first) |
| PR merged, `main` CI green, but the version never showed up on PyPI / no `chore(release)` commit | Push-race: another merge landed on `main` while the `Semantic Release` job (~6 min, compiles native assets) was still running, so its final push was rejected | `gh run view <run-id> --log-failed` on the `Semantic Release` job; look for `! [rejected]  main -> main` | [§2](#2-release-did-not-publish-push-race) |
| `tg search` hangs, or errors after a long wait | Whole-repo / unscoped search hit the 60s fail-fast timeout, often because `.tensor-grep/`, `_tg_refs/`, or a vendored `external_repos/` dir got walked | Check the exit code — `124` means the configured timeout fired, not a crash | [§3](#3-search-hangsslow) |
| `tg` returns 0 matches / empty result but you expect matches | A backend swallowed a real failure (native panic, PCRE2 semantics mismatch, OOM'd subprocess) and returned a clean empty `SearchResult` instead of raising | Re-run with `--format rg` or check `routing_reason` / `fallback_reason` in `--json` output; compare against `rg` directly on the same pattern/path | [§4](#4-silent-empty-result-fail-closed-contract) |
| A pattern/path argument starting with `-` is silently interpreted as a flag by `rg`/`tg`/`git` (wrong output, not a crash) | A subprocess argv builder appended a user-controlled value as a bare positional with no `--` end-of-options sentinel | `tg search -- --weird-pattern PATH` vs `tg search --weird-pattern PATH` (should error) — same probe against any MCP tool call path | [§5](#5-argvflag-injection) |
| A test suite is green but the real binary/extension does the wrong thing (dropped flags, dead code path) | Test mocked the boundary (a monkeypatched function, a stubbed PyO3 class) instead of exercising the compiled extension or the published binary | Run the same call through the *installed* `tg` (not `CliRunner`, not a mocked backend) and check `tg doctor --json` / `HAVE_RUST` | [§6](#6-mock-green-real-dead) |
| A fresh Python install resolves `tensor-grep` to an old version with no error | An upper-bound dependency pin (e.g. `typer<0.26`) has no release compatible with the new Python, so the resolver silently downgrades the *whole package* | `pip index versions tensor-grep` vs what actually installed; check `pyproject.toml` for `<` pins on `typer`/`click`/`pydantic` | [§7](#7-dependency-cap-silent-downgrade) |
| Agent-capsule primary target flipped after an unrelated change (wrong file promoted to top) | The agent capsule's flat, no-IDF candidate scorer is corpus-fragile — a small corpus change can flip which candidate wins a tie. (`tg search --rank` and semantic search use a different, IDF-weighted BM25 scorer and are not known to share this bug.) | Re-run `tg agent PATH QUERY --json` before/after the change and diff `primary_target` + `ambiguity`/`ask_reasons` fields | [§8](#8-ranking-flip) |

---

## 1. CI red — decode the structured check first

**Do not read the traceback and start theorizing.** Identify which *named* job/check failed, then
read only that job's failed-step log.

```bash
gh pr checks <PR-number>              # which named check(s) actually failed
gh run view <run-id> --json jobs      # confirm the job name, e.g. "Semantic Release", "test-python"
gh run view <run-id> --log-failed     # only the failed step's log — not the whole 20-minute run
```

Why this matters here specifically: this repo's CI enforces far more than tests — formatting,
typing, cross-platform behavior, release-workflow contracts, package-manager contracts, and
artifact/version parity all block the same pipeline (`docs/CI_PIPELINE.md`). A registration
mismatch (new command/flag missing one of its sites) fails the **blocking registration-completeness
gate**, which is a *different* job than `test-python`, and reading a Python traceback from the
wrong job wastes a cycle. Registration sites and rules live in `tensor-grep-change-control`; the
checker itself is `src/tensor_grep/core/registration_check.py` (`check_group_smart`,
`extract_members`), exercised by `tests/unit/test_registration_check.py`.

Known real incident: a README rewrite broke ~14 governance tests **and** a separate
`agent-readiness` release-blocker gate; 4 CI cycles were wasted because the team theorized from
tracebacks instead of reading which check failed first (root cause was two unrelated layers: a
missing `ast-grep` CLI dependency, and `uv run` re-syncing away the `[dev]` tree-sitter extra).
Decode the check name before touching code.

If the failing check is the `Semantic Release` job specifically, go to §2, not here.

## 2. Release did not publish (push-race)

The real publish step is the **`Semantic Release` job inside `.github/workflows/ci.yml`**, which
compiles native assets before publishing (~6 minutes) — that whole window is a race window where a
second merge to `main` can knock out the first run's final push.

**Discriminating experiment:**

```bash
gh run view <run-id> --json jobs                 # find the "Semantic Release" job's run/conclusion
gh run view <run-id> --log-failed                 # read its failed step only
```

A line reading `! [rejected]  main -> main` is the push-race signature. **Do not panic-rerun** — the
failure self-heals on the next push-to-`main` (version is derived from git tags, not the failed
run's state). Full mechanism, the `v1.17.23`/#318/#319 receipt, and the one-merge-per-tick
discipline to prevent recurrence: `tensor-grep-release-and-positioning` §1.5 /
`tensor-grep-failure-archaeology` Battle 6.

## 3. Search hangs/slow

`tg search` (both the Python bootstrap `rg`-forwarding path and the native ripgrep passthrough)
fails fast rather than hanging: the configured ripgrep timeout defaults to **60 seconds**
(`configured_ripgrep_timeout_seconds()`, `src/tensor_grep/cli/subprocess_policy.py:32-44`), lowered
from 600s specifically because ripgrep does GB/s and a >60s search means something pathological is
being scanned (an unexcluded huge/index directory), not a legitimately slow query. On timeout, the
child is killed and the process exits **124** with a stderr hint to scope the search or raise the
timeout (`src/tensor_grep/cli/bootstrap.py:703-709`, `752-760`).

**Discriminating experiment:** check the exit code. `124` = the configured timeout fired (not a
crash, not a hang you need to `Ctrl-C`). Compare a scoped vs. unscoped run:

```bash
tg search PATTERN                 # unscoped over a large/whole repo — can hit the 60s wall
tg search PATTERN src/            # scoped — typically <1s
```

**Root cause when it fires on a legitimately-sized repo:** `tg`'s own index/state directories
(`.tensor-grep/`, `_tg_refs/`, `.tg_semantic_index/`) and vendored corpora (e.g.
`benchmarks/external_repos/`) are not excluded from an unscoped walk, so searching from the repo
root walks tg's own indices too.

**Fix / workaround:** always scope searches to a path, glob, or file type. Raise
`TG_RG_TIMEOUT_SECONDS` (or `TG_SUBPROCESS_TIMEOUT_SECONDS` for non-search subprocess calls) only
for a genuinely huge monorepo — do not raise it to paper over an unscoped-walk problem. A
trigram-hybrid index is the tracked structural fix; own-dir excludes alone were tried and did not
fully resolve full-tree speed. Full env-var reference: `tensor-grep-config-and-flags`.

## 4. Silent-empty result (fail-closed contract)

Every `ComputeBackend` must raise `BackendExecutionError` on a real failure — never return a clean
`0-match SearchResult`, and never silently swap to an engine that cannot preserve the requested
semantics (`src/tensor_grep/backends/base.py:6-14`). This has been violated repeatedly; the
recurring anti-pattern is a bare `except Exception:` that returns empty or falls through to a
different engine. A context tool reporting a trustworthy-looking "no matches" when the real
answer is "the backend crashed" is the one failure this repo treats as unacceptable
(`AGENTS.md`, "Backend Fail-Closed Contract").

**Discriminating experiment:** run the same pattern/path directly through `rg` and compare. If `rg`
finds matches but `tg` reports zero, suspect a swallowed backend error, not a real no-match. Then
inspect `--json` output for `routing_reason` / `fallback_reason` — a populated `fallback_reason`
means a *visible*, legitimate degraded path (e.g. CyBERT provider unavailable); an *absent* one on
a result you believe is wrong means look for a silent swap.

**Ground-truth example of the correct pattern** (`src/tensor_grep/backends/rust_backend.py:260-278`):
a PCRE2 search that fails inside the native ripgrep bridge raises `BackendExecutionError` and
explicitly refuses to fall back to an engine that doesn't implement PCRE2 semantics — it does NOT
silently re-run the pattern through the Python-regex engine (which would return wrong matches,
not zero matches, but the principle is the same: don't swap engines invisibly for a
semantics-changing flag). Contrast with a legitimate degraded fallback (limit/sort flags the
Python fallback can't honor), which instead sets a visible `bridge_fallback_reason` on the result.

**Fix pointer:** if you find a bare `except Exception: return SearchResult(...)` (or similar) in a
backend, that is the bug class. Fail closed for any flag/contract the fallback cannot preserve
(raise, don't swap); if a degraded fallback is legitimate, set `fallback_reason` +
`routing_reason` so JSON/CLI consumers can tell degraded output from real output. Deep architecture
of this contract: `tensor-grep-architecture-contract`.

## 5. Argv/flag injection

A list-argv subprocess call (`shell=False`) stops *shell* injection but not *flag* injection: a
value beginning with `-` is parsed by the **child's own** option parser as a flag. This is CWE-88 —
the same class behind live MCP-server CVEs (CVE-2026-5058 aws-mcp-server, CVE-2026-23744,
CVE-2026-30623 Anthropic MCP SDK) — and it matters here because MCP tool handlers forward
LLM-controlled parameter values straight into `tg`/`rg`/`git` subprocess argv.

**Discriminating experiment:**

```bash
tg search -- --looks-like-a-flag PATH     # with -- sentinel: treated as pattern data
tg search --looks-like-a-flag PATH        # without: rg/tg's own parser errors on the "flag"
```

Run the same probe through any code path that builds subprocess argv from a
pattern/path/replacement value (MCP tool handlers, rewrite commands) — a value beginning with `-`
should error or be treated as data, never silently change tg's own behavior.

**Fixed reference implementation** (`src/tensor_grep/cli/mcp_server.py:765-782`,
`_build_rewrite_command` / `_build_index_search_command`): a `--` end-of-options sentinel is
inserted before the user-controlled `pattern`/`path` positionals, with an inline comment explaining
why.

**Known OPEN gap (round-4, tracked, not yet fixed):** `rust_core/src/rg_passthrough.rs:395-397`
appends `paths` directly (`for path in &args.paths { command.arg(path); }`) with no `--` sentinel
before them — a directory literally named `-l` would be parsed by `rg` as the `-l` (files-with-
matches) flag instead of as a path. Patterns going through `-e` are not affected (`-e` consumes the
next token as its value regardless of a leading `-`), only bare path positionals are. Do not assume
this is fixed just because the MCP-side builders are; check the specific call site.

**Caveats worth knowing before you conclude a builder is safe:** `--` protects only what comes
*after* it — a positional placed *before* `--` is still injectable; it does not gate
`--flag=VALUE` forms; and not every binary honors `--` the same way, so **dogfood the real binary**
rather than trusting the argv list alone. None of {validate the value, list-argv, `--` sentinel}
alone is complete — they layer.

## 6. Mock-green-real-dead

A test can pass because it mocked the exact boundary that was actually broken — a monkeypatched
function, or a Python-side stub standing in for the compiled PyO3 extension. This has happened for
real: mock-based FFI tests were green while the real Rust bridge was dead (it dropped every
forwarded flag and silently fell back to the Python engine) — the dead-passthrough bug and the
missing-flag bug compounded, because the bridge call itself never got exercised
(`AGENTS.md`, "Local Dev Gotchas").

**Discriminating experiment:** does the test import/patch `tensor_grep.rust_core` (or its Python
wrapper `RustCoreBackend`, `src/tensor_grep/backends/rust_backend.py:9-14`,
`try: from tensor_grep.rust_core import RustBackend as NativeRustBackend`), or does it patch
something *around* that boundary? If a test replaces `bootstrap.run_subprocess` or stubs
`RustCoreBackend.inner`, it is validating call shape, not that the real extension does the right
thing.

```bash
uv run python -c "from tensor_grep.backends.rust_backend import HAVE_RUST; print(HAVE_RUST)"
# then, separately, exercise the REAL installed binary end to end (not CliRunner):
tg search --pcre2 'foo(bar)?' src/            # confirm the flag actually reaches rg with real semantics
```

Same principle one layer up: `CliRunner` invokes the Typer app directly and bypasses the
`tensor_grep.cli.bootstrap:main_entry` front door entirely, so a routing bug in the bootstrap layer
is invisible to `CliRunner`-based tests no matter how many pass. After any change to a search flag,
a command, or the FFI boundary, dogfood the **installed published binary** with the harness at
`scripts/dogfood/` (`Dockerfile` + `dogfood_features.py`) rather than trusting unit tests alone
(`AGENTS.md`, "Dogfood the Real Binary, Not CliRunner"). See `dogfood-the-shipped-artifact` (global
skill) for the full post-release procedure.

## 7. Dependency-cap silent downgrade

An upper-bound pin (e.g. `typer<0.26`) can silently downgrade the **entire package** on a newer
Python if no release in that range is compatible with it — `pip`/`uv` resolve the whole install
down to a stale version with **no error**, because `requires-python>=X` has no upper bound to catch
the mismatch. Receipt: on Python 3.14, `uv tool install tensor-grep` with an unsatisfiable
`typer<0.25` range resolved to a stale `1.13.35` instead of erroring. Current pin, chosen to thread
both constraints (`pyproject.toml:327-333`):

```
typer>=0.12,<0.26
```

The comment there (`pyproject.toml:327-329`) explains why the cap can't simply be dropped: typer
0.26 removed `click.testing.CliRunner` inheritance, breaking `CliRunner.isolated_filesystem()`
which ~49 tests rely on.

**Discriminating experiment:**

```bash
pip index versions tensor-grep                 # what SHOULD be installable
uvx --refresh-package tensor-grep --from tensor-grep==<expected-version> tg --version
```

If a fresh install on a new Python resolves to an old `tg --version`, do not assume
`requires-python` is wrong — grep `pyproject.toml` for `<` upper bounds on `typer`, `click`,
`pydantic`, or other transitive deps first; that is the class of bug this was.

## 8. Ranking flip

The agent capsule's **primary-target candidate selection** (`score_term_overlap`,
`src/tensor_grep/core/retrieval_lexical.py:15`, used by `repo_map.py:4899` to help pick the
capsule's `primary_target`) is a **flat, no-IDF** set-membership scorer plus a hard top-N candidate
cap — an acknowledged, not-yet-fixed weak point. A small, unrelated corpus change can flip which
candidate wins a near-tie, and that flip is invisible to the call graph (nothing "broke" in the
traditional sense — the ranking function just picked a different winner). This produced a real
incident: an unrelated GPU-code change flipped the agent capsule's top pick from "tied, ask the
user" to "confidently pick the wrong marker/no-op function" with zero call-graph signal.

Note: `tg search --rank` (`rerank_by_bm25()`, `src/tensor_grep/core/reranker.py`) and local semantic
search (`src/tensor_grep/core/semantic_index.py`) both route through `Bm25Index`
(`src/tensor_grep/core/retrieval_bm25.py`) — a real Okapi BM25 scorer **with IDF**, term-frequency
saturation, and length normalization. They are a different, IDF-weighted scorer and are not known to
share this specific flat-scorer fragility; don't assume a `--rank` reorder flip has the same root
cause as an agent-capsule primary-target flip.

**Discriminating experiment:** these are two different code paths — run whichever one matches the
surface you're chasing, not both interchangeably:

```bash
# BM25-reranked search order (src/tensor_grep/core/reranker.py) — top-match ordering only,
# no ambiguity/candidate concept:
tg search PATTERN PATH --rank --json > before.json   # on the pre-change commit
tg search PATTERN PATH --rank --json > after.json     # on the post-change commit

# Agent capsule (src/tensor_grep/cli/agent_capsule.py) — primary-target selection, the surface
# that actually emits ambiguity/ask metadata:
tg agent PATH QUERY --json > before.json              # on the pre-change commit
tg agent PATH QUERY --json > after.json                # on the post-change commit
```

For the agent capsule specifically, check the `ambiguity` / `ask_reasons` fields
(`src/tensor_grep/cli/agent_capsule.py`) rather than only the `primary_target` — a **degrade-to-ask
safety floor** (`agent_capsule.py:1399-1405`) forces `ask_user`-style output whenever ranking
buried the real implementation behind an unrequested marker/no-op helper, so a correctly-behaving
flip should surface as `ambiguity`/`ask_user_before_editing` metadata, not a silent wrong answer.
If you see a confident wrong `primary_target` with no ambiguity signal, that is a regression in the
safety floor itself, not just scorer fragility — treat it as higher severity. `tg search --rank`
has no equivalent safety floor to check; it only reorders matches.

**What this is NOT:** a fix for the underlying flat scorer. The safety floor added in response to
the incident above only prevents *silent* wrong picks; it does not make the ranking itself
IDF-aware or less corpus-fragile. That remains a tracked, separate, benchmarked `repo_map`
follow-up. Do not claim ranking is "fixed" — only that a floor exists under it.

---

## Provenance and maintenance

Facts here were verified against the live repo as of **2026-07-02, tensor-grep v1.17.25**
(`pyproject.toml:322`). Re-verify anything below before trusting it on a later version — this
table drifts whenever the cited line numbers, defaults, or contracts change.

Re-verification commands:

```bash
# Version this playbook was verified against
grep -n '^version' pyproject.toml

# Timeout default + env var name (§3)
grep -n "TG_RG_TIMEOUT_SECONDS\|60.0" src/tensor_grep/cli/subprocess_policy.py

# Fail-closed contract text still matches (§4)
grep -n "BackendExecutionError" src/tensor_grep/backends/base.py

# -- sentinel fix still present at both cited sites (§5)
grep -n '"--",' src/tensor_grep/cli/mcp_server.py
grep -n "for path in &args.paths" rust_core/src/rg_passthrough.rs   # confirm the open gap is still open

# typer dependency cap still <0.26 with the same rationale (§7)
grep -n "typer>=" pyproject.toml

# degrade-to-ask safety floor still present (§8)
grep -n "Degrade-to-ask safety floor" src/tensor_grep/cli/agent_capsule.py

# registration-completeness checker location unchanged
grep -n "def check_group_smart" src/tensor_grep/core/registration_check.py

# push-race + --log-failed guidance still current in AGENTS.md
grep -n "log-failed\|push-race\|rejected  main -> main" AGENTS.md
```

If any of these greps come back empty or materially different, the corresponding row above is
stale — update it before relying on it, and check whether the fix pointer's target skill
(`tensor-grep-architecture-contract`, `tensor-grep-change-control`, etc.) needs the same update.
