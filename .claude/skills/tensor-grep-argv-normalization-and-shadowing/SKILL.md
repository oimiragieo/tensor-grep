---
name: tensor-grep-argv-normalization-and-shadowing
description: >-
  Use when modifying, auditing, or testing an argv normalizer or a CLI front door that REWRITES one
  invocation shape into another (bootstrap `_normalize_search_invocation` / `main_entry`,
  `SEARCH_OPTION_FIRST_FLAGS`→`tg search ...` in rust_core), adding or auditing a
  `--` end-of-options sentinel on a builder that appends a caller-influenced positional, auditing
  root-option shadowing (an option parsed as a subcommand/positional and vice versa), enumerating
  the doors a rewritten argv can reach (A83), or adding a flag to a SHARED argv builder and needing
  to know which consumers parse vs stream the output. Triggers: "argv rewrite", "normalizer",
  "option-first", "flag shadowing", "CWE-88", "end-of-options", "`--` sentinel",
  "shape-monotonic", "the front door rewrote my invocation". Sibling of
  tensor-grep-change-control (the four registration sites) and tensor-grep-config-and-flags (the
  search-flag allowlists); this one is the normalizer/argv-shape discipline itself.
---

# tensor-grep: argv normalization and shadowing

Two failure classes live around argv in this repo, both "quiet" (no crash, wrong behavior):

1. **Rewriting shadowing (A83, #979):** a normalizer that rewrites one CLI shape into another
   (`SEARCH_OPTION_FIRST_FLAGS` → `tg search …`) redirects a "positional" validator's coverage —
   `tg PAT --gpu-device-ids 0 --count-matches` never reaches `run_positional_cli` because it becomes
   the search form, and the search path can silently drop `gpu_device_ids` (RipgrepSearchArgs has no
   gpu field) while rg-passthroughing. A fix is only closed when it guards **EVERY** door the
   rewritten argv can reach.
2. **Flag injection (CWE-88 / MCP-276 class):** a list-argv `subprocess` (`shell=False`) stops SHELL
   injection but NOT flag injection — a value beginning with `-` is parsed by the child's own option
   parser as a flag. Insert a `--` end-of-options sentinel BEFORE user positionals
   (POSIX Guideline 10).

Both are registration-completeness problems wearing argv clothes: enumerate the N places the argv
(or its rewrite) can travel, miss one, and the defect is *silent*.

---

## Part 1 — The front-door normalizer topology (bootstrap)

`main_entry` (`src/tensor_grep/cli/bootstrap.py`, `def main_entry`) is the Python front door that
runs BEFORE the Typer app. Its shape-rewriting joints:

- `_normalize_search_invocation` (`bootstrap.py`, `def _normalize_search_invocation`) — strips a
  leading `search` subcommand, passes through everything else; this is where an option-first
  invocation may become the search form.
- `_requires_full_cli` (`bootstrap.py`, `def _requires_full_cli`) vs
  `_requires_full_cli_ignoring_rg_json` — the routing predicates that decide whether an argv goes to
  the full Typer CLI or to `_run_rg_passthrough`. The `_TG_ONLY_SEARCH_FLAGS` set (`bootstrap.py:50`)
  is the allowlist of what MUST route to the full CLI; the attached-value short-flag walk
  (bundled `-g*.py`/`-tpy`/`-itpy`) is the sibling that a bare-token check misses.
- `_run_rg_passthrough` (`bootstrap.py`, `def _run_rg_passthrough`) — forwards the plain-addressed
  text search straight to ripgrep.

**The invariant to preserve (routing_policy.md, "shape-monotonic" paragraph — grep
`monoton` in docs/routing_policy.md):** the front door's verdict must be a *superset-monotone*
refinement of the clap path's — the front door may be stricter, never looser; attached-value short
spellings (`-eneedle`) are a known deliberate asymmetry in the SAFE direction. When you loosen a
front-door predicate, you widen every path that falls through it.

---

## Part 2 — The A83 census: every door the rewritten argv can reach

Before claiming a front-door/argv fix is closed, enumerate the doors mechanically:

- [ ] List every flag the normalizer REWRITES (e.g. `SEARCH_OPTION_FIRST_FLAGS` includes
      `--count-matches`, so `tg PAT --count-matches` becomes the search subcommand form).
- [ ] List every parser that can receive the REWRITTEN form: the search subcommand handler, the
      positional (`run_positional_cli`) handler, and the rg-passthrough path.
- [ ] For each (rewritten flag, sub-parser) pair, ask: does that parser honor the flag, refuse it,
      or silently drop it? `SEARCH_OPTION_FIRST_FLAGS`'s `--gpu-device-ids` lands on a search path
      whose structured args struct has NO gpu field — the drop is structural, not a bug someone
      typed.
- [ ] Guard EVERY door you enumerate; a guard added only to the door you *thought* you were fixing
      is a no-op against the other doors (the H2 receipt: the rg-passthrough early-return was where
      the explicit request was silently dropped; the guard had to be placed BEFORE that early
      return, first-gate-in-BOTH-environments).
- [ ] A compiled ratchet is the only non-silent form: the H2 fix used a compile-exhaustive field
      destructure (a new field fails COMPILE, cannot be satisfied by a comment).

**The registration-completeness law applies verbatim** (see AGENTS.md "Adding a Command or Flag":
census ALL N sites, never just the one you edited). The front-door rewrite is the same law applied
to argv shapes.

---

## Part 3 — `--` end-of-options hygiene (CWE-88)

The behavioural census: `tests/unit/test_argv_sentinel_covers_every_builder.py` asserts every argv
builder that hands a **CALLER-INFLUENCED** positional to a flag-parsing child places `--` before
it. The scope is stated deliberately: the property is NOT "every `subprocess` list ends its
options" — it is that a value the CALLER can influence never reaches a child parser in flag
position. Measured on the shipped binary:

```
-e NEEDLE "-i"        ->  "path":"",  total_files:0, total_matches:0, exit 0   (false empty)
-e NEEDLE -- "-i"     ->  "path":"-i", total_files:1                           (correct)
```

Checked list for a builder that appends user/LLM-controlled values:

- [ ] Use a list argv (`shell=False`) — this stops shell injection (baseline).
- [ ] Insert a `--` sentinel immediately BEFORE the user positionals; the sentinel is
      UNCONDITIONAL (a guarded "only when the value starts with `-`" form reads as equivalent and
      leaves the silent case open — the `_agent_gpu_evidence` receipt).
- [ ] Know the sentinel's limits: `--` protects only what comes AFTER it (a user positional before
      `--` is still injectable); it does not gate `--flag=VALUE`; not every binary honors it —
      dogfood the real binary (`tg search -- --weird` matches; `tg search --weird` errors).
- [ ] Uniformity is the security property: even a builder whose positionals are ALL tg-generated
      carries the sentinel (the doctor GPU probe), because a sweep whose members each carry a
      private risk assessment is a sweep nobody can check.
- [ ] Assert on POSITION, not presence: a sentinel sitting BETWEEN two positionals protects nothing
      (the census found one exactly like that — present, and useless). Behavioral capture at the
      seam the argv CROSSES (`run_subprocess`), with an `assert captured` guard so an inert capture
      fails rather than returning an empty value that passes everything.

---

## Part 4 — Shared-builder consumers: stream vs parse (`-q` receipt)

When adding a flag to a SHARED argv builder, enumerate its consumers and ask which of them CONSUME
the thing the flag changes (AGENTS.md "The check and the defect AGREED" — #876/#880):

- [ ] `RipgrepBackend._build_cmd`'s `-q` receipt: the builder has FOUR consumers; only ONE streams.
      The other three PARSE rg's stdout — and `-q` makes rg print nothing, so `tg search -q --count`
      on a MATCHING file reported `total_matches=0`, exit 1: a false no-match AND an exit-contract
      violation. Measured: `rg --count-matches needle f.txt -> "2"`, with `-q -> ""`; `rg -l -> f.txt`,
      with `-q -> ""`; `rg --json -> 5 lines`, with `-q -> 1`.
- [ ] A flag that ALTERS OUTPUT belongs to the consumers that stream, not the ones that parse. Put
      it on the streaming path only.
- [ ] When writing the control arm, state what the CONSUMER does with the value, not what the
      callee accepts: "rg accepts `-q`" is true of rg and irrelevant to tg, which consumes the
      stdout `-q` suppresses.

---

## External anchors (Exa research, 2026-08-09)

| Anchor | Their point | This skill's mapping |
|---|---|---|
| **POSIX Utility Syntax Guideline 10** (pubs.opengroup.org) | "The first `--` argument that is not an option-argument should be accepted as a delimiter indicating the end of options" — the canonical `--` contract every child parser implements. | Part 3's sentinel-before-positionals: `--` is how a callee's parser distinguishes a literal value from a flag. |
| **OWASP Command Injection** (owasp.org, command-injection section) | Injection is about the value being interpreted as syntax (shell or flag), not just shell metacharacters | The CWE-88 "flag injection into the CALLEE's own parser" framing in Part 3's scope statement. |
| **CWE-88 "Improper Neutralization of Argument Delimiters in a Command"** (cwe.mitre.org) | Delimiter/argument injection is its own class, distinct from OS command injection | The `-e NEEDLE "-i"` false-empty receipt: the delimiter (`-i`) is swallowed as a flag, not as a path. |
| **SonarSource "Preventing command injection"** (sonarsource.com) | Whitelist/allowlist expected values, prefer explicit allowlists over blacklists | `_TG_ONLY_SEARCH_FLAGS` is an allowlist that forces full-CLI routing; the allowlist-parity invariant (every `--x=` has `--x` registered value-taking) is the model-the-class gate. |
| **CPython gh-90259 / click #2748 / click #2790** (upstream) | Root-option shadowing: an option shared between a top-level and a subcommand parses for the WRONG context, so a value meant for one context is "consumed" by the other | Part 1/2's shadow-class: `SEARCH_OPTION_FIRST_FLAGS`'s flag being consumed by the search-form parser instead of the positional context. |
| **shrpx/secure-argv receipts (repo)** | See Part 4's `-q` table from `tests/... rg_parity` / comment receipts in `RipgrepBackend._build_cmd` | Consumers-parse-vs-stream enumeration. |

**Repo receipts to cite by symbol, not line:** `main_entry`, `_normalize_search_invocation`,
`_requires_full_cli` (all `src/tensor_grep/cli/bootstrap.py`); `SEARCH_OPTION_FIRST_FLAGS` and
`normalize_top_level_search_args` (`rust_core/src/main.rs`); `_build_cmd` and its consumers
(`src/tensor_grep/backends/ripgrep_backend.py`); `tests/unit/test_argv_sentinel_covers_every_builder.py`
(the behavioural census — read its docstring for WHY the source-scan form was retired); AGENTS.md A83.

---

## Quick reference

```
[1] topology   enumerate the normalizer joints + the predicates that route each resulting shape
[2] A83 census  every door the REWRITTEN argv can reach; guard all N, ratchet by compile
[3] sentinel   `--` UNCONDITIONALLY before caller-influenced positionals; assert POSITION
[4] consumers  a flag that alters output belongs on the streaming path, never a parse path
[5] allowlist  `_TG_ONLY_SEARCH_FLAGS` is an allowlist; keep the monotone (stricter-never-looser)
```

The endpoint: a rewritten argv whose every resulting door is either honored or refused loudly, a
sentinel no caller-influenced value can get around, and a shared builder whose consumers are
enumerated before the flag is added.
