# Beyond Route A: costing the three levers out of the residual split floor

**Item:** `W3-a` of `docs/plans/2026-08-20-worldclass-closeout-plan.md` (r4, 7/7 APPROVE).
**Date:** 2026-08-20. **Status:** DESIGN ONLY — this document ships no production code.
**Base:** `origin/main` at `0b9d33f` (`docs: world-class closeout plan — council-approved r4 (#1055)`).
Every number below carries the command that produced it. Nothing here is recalled.

---

## 0. Plan discrepancy, stated before anything else

Plan §W3.2 ends: *"W3-a is **blocked until W1-c merges**, because option 1's costing reads the same
modules W1 is editing, and a cone measured mid-edit is a cone measured on a tree nobody will ship."*

**W1-c has not merged. Nothing in W1 has merged.** Derive with:

    git log origin/main --oneline -8

At `0b9d33f` the newest commits are the plan itself (#1055), the tri-split retention doc (#1054) and
the three splits (#1051/#1053/#1052). No `W1-*` slice exists.

This document was nonetheless produced, and the reader is owed the reason and the residual risk:

- The gate's stated hazard is a **mid-edit tree**. There is no mid-edit tree — `cli/main.py` is at its
  merged post-split state and no W1 writer is open against it.
- Every number here is pinned to the exact SHA `0b9d33f`, and the re-derivation command is printed
  beside it, so a post-W1-c re-run is a mechanical diff rather than a re-investigation.
- **What can still move it:** W1-c edits broad `except` handlers *inside* `cli/main.py` functions.
  That changes function line spans, so `cone_lines`, `expected_residual_floor` and the floor totals
  can shift by tens of lines. It does not change *which* symbols tests patch, so the
  `candidate_seams`, `affected_tests` and `affected_callers` columns — the ones the decision rule
  actually turns on — are insensitive to W1.
- **Obligation:** re-run §5's two acceptance commands after W1-c merges. If `measure_split_floor.py`
  no longer reproduces 7,416 / 6,715 / 2,506, the affected rows are re-derived before this document
  is cited in any decision.

---

## 1. The floor, re-measured (not quoted from the plan)

    python scripts/measure_split_floor.py

Reproduced at `0b9d33f`, byte-identical to the block in plan §W3.1 for the three giants:

| module | total | symbols tests patch | functions LOCKED | lines LOCKED to facade | verdict |
|---|---:|---:|---:|---:|---|
| `src/tensor_grep/cli/main.py` | 13,523 | 49 | 62 | **7,416** | SPLIT CANNOT REACH THE LIMIT |
| `src/tensor_grep/cli/repo_map.py` | 15,243 | 66 | 106 | **6,715** | SPLIT CANNOT REACH THE LIMIT |
| `src/tensor_grep/cli/mcp_server.py` | 5,341 | 66 | 28 | **2,506** | SPLIT CANNOT REACH THE LIMIT |

The tool's own docstring calls this a **lower bound**: it does not model class methods, closures,
`global` rebinding, or `spec_from_file_location`. Section 3 records a correction that runs the *other*
way for `main.py` — one instrument artefact that makes the printed floor too HIGH — so the true floor
is bracketed, not bounded on one side only.

---

## 2. The three probes

Every derivation command in section 4 extracts one of these blocks out of this document and runs it.
The document therefore carries its own instruments; there is no uncommitted script behind any number.
The blocks are fenced as `text` deliberately — a `python` fence here would be reformatted by the
repo-wide `ruff format` markdown pass and the extraction would drift from what was run.

Extraction idiom (Git Bash, from the repo root):

    python -c "$(sed -n '/BEGIN <name>/,/END <name>/p' docs/design/2026-08-20-beyond-route-a.md | grep -v '^<!--' | grep -v '^```')" <args>

<!-- BEGIN cone-probe -->
```text
import ast
import importlib.util as ilu
import sys

spec = ilu.spec_from_file_location("msf", "scripts/measure_split_floor.py")
msf = ilu.module_from_spec(spec)
spec.loader.exec_module(msf)
rel, dotted = sys.argv[1], sys.argv[2]
tree = msf.parse(msf.ROOT / rel)
funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
bare = {k: {x.id for x in ast.walk(v) if isinstance(x, ast.Name)} for k, v in funcs.items()}
patched = msf.patched_symbols(dotted)


def locked(active):
    cone = {n for n in funcs if n in active} | {n for n, r in bare.items() if r & active}
    changed = True
    while changed:
        changed = False
        for name, refs in bare.items():
            if name not in cone and refs & cone:
                cone.add(name)
                changed = True
    return len(cone), sum(funcs[n].end_lineno - funcs[n].lineno + 1 for n in cone)


base = locked(patched)[1]
print("floor", base, "over", len(funcs), "top-level functions")
marg = sorted(((base - locked(patched - {s})[1], s) for s in patched), reverse=True)
print("top marginal cones", marg[:6])
freed = set()
while locked(patched - freed)[1] > 1500:
    best = min((locked(patched - freed - {s})[1], s) for s in patched - freed)
    if best[0] >= locked(patched - freed)[1]:
        print("GREEDY STALLS at", locked(patched - freed)[1])
        break
    freed.add(best[1])
print("free", len(freed), "symbols ->", locked(patched - freed)[1], sorted(freed))
```
<!-- END cone-probe -->

<!-- BEGIN patch-site-probe -->
```text
import ast
import importlib.util as ilu
import sys

spec = ilu.spec_from_file_location("msf", "scripts/measure_split_floor.py")
msf = ilu.module_from_spec(spec)
spec.loader.exec_module(msf)
dotted, symbols = sys.argv[1], set(sys.argv[2].split(","))
sites, files = 0, set()
for path in msf.tracked("tests/**/*.py"):
    tree = msf.parse(path)
    if tree is None:
        continue
    alias = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                alias[a.asname or a.name] = f"{node.module}.{a.name}"
        elif isinstance(node, ast.Import):
            for a in node.names:
                alias[a.asname or a.name] = a.name
    for node in ast.walk(tree):
        hit = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "setattr" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    mod, _, attr = first.value.rpartition(".")
                    if mod == dotted:
                        hit = attr
                elif len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    if isinstance(first, ast.Name) and alias.get(first.id) == dotted:
                        hit = node.args[1].value
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name):
                    if alias.get(tgt.value.id) == dotted:
                        hit = tgt.attr
        if hit in symbols:
            sites += 1
            files.add(path.name)
print("patch sites", sites, "in", len(files), "test files")
```
<!-- END patch-site-probe -->
<!-- BEGIN seam-reference-probe -->
```text
import ast
import importlib.util as ilu
import sys

spec = ilu.spec_from_file_location("msf", "scripts/measure_split_floor.py")
msf = ilu.module_from_spec(spec)
spec.loader.exec_module(msf)
rel, modname, symbols = sys.argv[1], sys.argv[2], set(sys.argv[3].split(","))
tree = msf.parse(msf.ROOT / rel)
inside = sum(
    1
    for x in ast.walk(tree)
    if isinstance(x, ast.Name) and x.id in symbols and isinstance(x.ctx, ast.Load)
)
outside, files = 0, set()
for path in msf.tracked("src/**/*.py"):
    if path == msf.ROOT / rel:
        continue
    other = msf.parse(path)
    if other is None:
        continue
    for x in ast.walk(other):
        if isinstance(x, ast.ImportFrom) and x.module and x.module.endswith(modname):
            for a in x.names:
                if a.name in symbols:
                    outside += 1
                    files.add(path.name)
        if isinstance(x, ast.Attribute) and x.attr in symbols:
            if isinstance(x.value, ast.Name) and x.value.id.endswith(modname):
                outside += 1
                files.add(path.name)
print("in-module name loads", inside, "| external src references", outside, "in", len(files), "files")
```
<!-- END seam-reference-probe -->

The `repo_map.py` seam set is 31 symbols; it is held here once so no command line has to repeat it.

<!-- BEGIN repo-map-seam-set -->
```text
os,build_context_render_from_map,CALLER_SCAN_FILE_CEILING,_SYMBOL_LITERAL_SEED_MAX_BYTES,
_relevant_tests_for_symbol,_attach_edit_plan_metadata,build_repo_map,
_render_context_string_and_sections,CALLER_SCAN_ORDER_PROBE_CEILING,_iter_repo_files,
_context_tests,build_context_render,_python_references_and_calls,
_personalized_reverse_import_pagerank,_reverse_import_distances,
_precomputed_validation_files_for_root,build_symbol_blast_radius,_parse_rust_workspace_members,
_imports_and_symbols_for_path,build_context_pack,_rust_classify_ref_kind,build_symbol_defs,
_reverse_importers,_js_ts_classify_ref_kind,_string_literal_references,build_context_pack_from_map,
build_context_edit_plan,_EXTERNAL_LSP_PROVIDER_MANAGER,build_symbol_blast_radius_from_map,
_CONTEXT_TESTS_SOURCE_FILE_CEILING,build_symbol_callers_from_map
```
<!-- END repo-map-seam-set -->

Read it into `$SEAMS` with the same extraction idiom. That command is deliberately not written out
here: a literal copy of the marker names inside the prose extends `sed`'s range and silently appends
the paragraph to the symbol list. It did exactly that on the first run, and the probe then reported
**178** patch sites instead of 179 -- a believable number, one short, produced by the document
measuring itself.

A fourth probe separates a decorator reference from a body reference. Section 3 needs it.

<!-- BEGIN decorator-probe -->
```text
import ast
import sys

path, sym = sys.argv[1], sys.argv[2]
tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
dec_only, body_ref = [], []
for n in tree.body:
    if not isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef):
        continue
    span = n.end_lineno - n.lineno + 1
    in_dec = any(
        isinstance(x, ast.Name) and x.id == sym for d in n.decorator_list for x in ast.walk(d)
    )
    in_body = any(
        isinstance(x, ast.Name) and x.id == sym for st in n.body for x in ast.walk(st)
    ) or any(isinstance(x, ast.Name) and x.id == sym for x in ast.walk(n.args))
    if in_body:
        body_ref.append((n.name, span))
    elif in_dec:
        dec_only.append((n.name, span))
print("decorator-ONLY", len(dec_only), "fns /", sum(s for _, s in dec_only), "lines")
print("body-or-signature", len(body_ref), "fns /", sum(s for _, s in body_ref), "lines")
```
<!-- END decorator-probe -->

---

## 3. Two instrument corrections, before any costing is believed

### 3.1 `main.py`'s largest cone is a measurement artefact (the floor is too HIGH by 3,044)

`app` is by far the biggest marginal cone in `main.py`: freeing that one symbol drops the floor from
**7,416 to 4,372**, 35 functions and 3,044 lines. Tests really do patch it -- twelve `setattr` sites in
two files (`grep -rn 'setattr(cli_main, "app"' tests/`). But the reference shape matters:

    python -c "$(sed -n '/BEGIN decorator-probe/,/END decorator-probe/p' docs/design/2026-08-20-beyond-route-a.md | grep -v '^<!--' | grep -v '^```')" src/tensor_grep/cli/main.py app

    decorator-ONLY 46 fns / 6399 lines
    body-or-signature 0 fns / 0 lines

**Zero** functions in `main.py` reference `app` in a body or a signature. All 46 references are
`@app.command(...)` decorators, and a decorator runs at **import** time -- strictly before any
`monkeypatch.setattr(cli_main, "app", ...)` in any test. Rebinding the module attribute afterwards
cannot reach a command that was already registered; the only thing those twelve patches intercept is
the later `app(...)` invocation. So `measure_split_floor.py` locks 35 functions to the facade on a
dependency that does not exist at patch time.

The negative control is in the same probe: run it against `repo_map.py` / `os` and it reports
`decorator-ONLY 0 / body-or-signature 10 fns / 271 lines`. The probe can distinguish the two shapes;
`app` is genuinely one-sided, not an artefact of a detector that only ever says "decorator".

This is the FIRST recorded over-count from this tool. Its docstring records the opposite direction --
an under-count that read as permission to split -- and states that it is a lower bound. Both are now
true at once: **7,416 is neither a floor nor a ceiling.** For `main.py` the defensible bracket is
**4,372 (excluding the decorator artefact) to 7,416 (as printed)**, and both ends are far above 1,500,
so the plan's verdict survives the correction intact. No number in section 4 is quietly reduced by
this: the rows report the tool's printed values, and this correction is stated beside them.

### 3.2 The seam-reference probe, cross-checked against `tg`

For `mcp_server.py` the seam-reference probe reports `in-module name loads 2 | external src
references 0`. Independent cross-check with the product this repo dogfoods:

    tg callers . tg_search --json

Returns exactly one caller -- `src/tensor_grep/cli/mcp_server.py:4226`, `return _self.tg_search(`,
provenance `python-ast`, with `result_incomplete: false` and `not_found: false`. Two instruments that
do not share an implementation agree that the MCP tool functions have essentially no in-repo callers:
they are invoked through the MCP registry, not called. That is load-bearing for option 2 -- you cannot
dependency-inject through a caller that does not exist.

**A zero here is labelled.** `tg`'s own `resolution_gaps` names six suffixes with no registered
extractor (`md` 149 files, `json` 70, `txt` 49, `toml` 18, `yml` 15, `yaml` 7). None of them can
contain a Python call site, so the zero is an absence, not a blind spot.

---

## 4. The costing grid: 3 modules x 3 options

**Counting convention, stated before the numbers so it cannot be chosen to fit them.** An *edit* is a
hand-authored change site: a test patch site that must be rewritten, or a production signature or
reference that must be threaded. It **excludes** the mechanical relocation of a function body into a
sibling module, on the precedent that this campaign has already banked three such relocations at one
PR and one CI round each (#1052 moved 4,460 lines; #1053 moved 4,519; #1051 split `mcp_server`).
Section 6's counter-argument attacks exactly this convention and reports what the decision rule
returns under the strict alternative.

*CI rounds* are estimated as one round per serialized slice (a slice capped at about ten touched test
files, so standing constraint 4's union-merge rule stays satisfiable), plus one integration round,
plus one more where production signatures change.

`accept-the-pin` rows carry `0` in the cost fields by construction; that is the option's content, not
a missing measurement.

#### ROW 1: src/tensor_grep/cli/main.py / shrink-patched-set
- module: src/tensor_grep/cli/main.py
- option: shrink-patched-set
- cone_lines: 7190 freed (floor 7416 -> 226); largest single cone `app` at 3044, then `_FIND_CORPUS_CHUNK_CAP` 290, `subprocess` 246
- candidate_seams: 6 of 49 patched symbols (`app`, `subprocess`, `upgrade`, `_FIND_CORPUS_CHUNK_CAP`, `_SEMANTIC_CORPUS_CHUNK_CAP`, `_LARGE_ROOT_SCAN_FILE_CEILING`); only 11 of the 49 have any marginal cone at all
- affected_tests: 23 patch sites in 5 test files (test_cli_modes, test_find_command, test_native_delegation_timeout, test_semantic_search_flag, test_trust_parity)
- affected_callers: 80 (79 in-module name loads, of which 46 are `@app.command` decorators; 1 external src reference)
- estimated_edits: 23 (test-side only; no production change in this option)
- estimated_ci_rounds: 2 (1 slice + 1 integration)
- risk: HIGH -- `subprocess` is the native-delegation execution seam in the CLI front door, a surface W1 names as security-adjacent. Dropping it from the set stalls the floor at 3795, so this option cannot reach 1500 without touching it
- expected_residual_floor: 226 (3795 if `subprocess` is excluded, i.e. does not reach the limit)
- derivation_command: `python -c "$(sed -n '/BEGIN cone-probe/,/END cone-probe/p' docs/design/2026-08-20-beyond-route-a.md | grep -v '^<!--' | grep -v '^```')" src/tensor_grep/cli/main.py tensor_grep.cli.main`

#### ROW 2: src/tensor_grep/cli/main.py / dependency-injection
- module: src/tensor_grep/cli/main.py
- option: dependency-injection
- cone_lines: 7190 freed (same seam set; DI is a different mechanism for the same cone)
- candidate_seams: 6, of which 2 are true collaborators (`subprocess`, `app`), 3 are tunable constants and 1 is a command function (`upgrade`)
- affected_tests: 23 patch sites in 5 test files, each rewritten to pass a fake rather than patch a global
- affected_callers: 80 in-repo references that must be threaded or defaulted (79 in-module, 1 external)
- estimated_edits: 103 (23 test + 79 in-module + 1 external)
- estimated_ci_rounds: 3 (2 slices + 1 integration; production signatures change)
- risk: HIGH -- injecting `subprocess` rewrites the native-delegation trust seam that `test_trust_parity` and `test_native_delegation_timeout` exist to pin; injecting `app` changes Typer command registration for 46 commands
- expected_residual_floor: 226
- derivation_command: `python -c "$(sed -n '/BEGIN seam-reference-probe/,/END seam-reference-probe/p' docs/design/2026-08-20-beyond-route-a.md | grep -v '^<!--' | grep -v '^```')" src/tensor_grep/cli/main.py main "app,subprocess,upgrade,_FIND_CORPUS_CHUNK_CAP,_SEMANTIC_CORPUS_CHUNK_CAP,_LARGE_ROOT_SCAN_FILE_CEILING"`

#### ROW 3: src/tensor_grep/cli/main.py / accept-the-pin
- module: src/tensor_grep/cli/main.py
- option: accept-the-pin
- cone_lines: 0 freed
- candidate_seams: 0 (none proposed; the module stays at its allowlist pin)
- affected_tests: 0
- affected_callers: 0
- estimated_edits: 0
- estimated_ci_rounds: 0
- risk: LOW mechanically; the cost is that 13,523 lines stay in one file, and the pin must carry its measured reason so it stays reopenable rather than permanent
- expected_residual_floor: 7416 as printed (bracket 4372-7416 per section 3.1)
- derivation_command: `python scripts/measure_split_floor.py`

#### ROW 4: src/tensor_grep/cli/repo_map.py / shrink-patched-set
- module: src/tensor_grep/cli/repo_map.py
- option: shrink-patched-set
- cone_lines: 5317 freed (floor 6715 -> 1398); largest single cone `os` at 1458, then `_relevant_tests_for_symbol` 165, `build_repo_map` 153
- candidate_seams: 31 of 66 patched symbols (the `repo-map-seam-set` block); 46 of 66 have a nonzero marginal cone, so the tail is long and flat -- no single symbol dominates the way `app` does in main.py
- affected_tests: 179 patch sites in 39 test files
- affected_callers: 77 (36 in-module name loads, 41 external src references in 13 files)
- estimated_edits: 179 (test-side only)
- estimated_ci_rounds: 5 (4 slices of <=10 test files + 1 integration)
- risk: MEDIUM -- no seam here crosses a surface W1 names (`repo_map.py` is one of the eight zero-handler modules in plan W1.1). The risk is breadth: 39 test files is over half of Route B's 75, for a floor that lands at 1398, only 102 lines under the limit
- expected_residual_floor: 1398
- derivation_command: `python -c "$(sed -n '/BEGIN cone-probe/,/END cone-probe/p' docs/design/2026-08-20-beyond-route-a.md | grep -v '^<!--' | grep -v '^```')" src/tensor_grep/cli/repo_map.py tensor_grep.cli.repo_map`

#### ROW 5: src/tensor_grep/cli/repo_map.py / dependency-injection
- module: src/tensor_grep/cli/repo_map.py
- option: dependency-injection
- cone_lines: 5317 freed
- candidate_seams: 31, of which 1 is a stdlib module (`os`), 5 are tunable ceilings/constants and 25 are top-level functions patched as collaborators
- affected_tests: 179 patch sites in 39 test files
- affected_callers: 77 references to thread (36 in-module, 41 external in 13 files)
- estimated_edits: 256 (179 test + 36 in-module + 41 external)
- estimated_ci_rounds: 6 (5 slices + 1 integration; production signatures change)
- risk: MEDIUM-HIGH -- 25 of the 31 seams are public `build_*` entry points consumed by other `cli/` modules, so a signature change is an internal API break across 13 files
- expected_residual_floor: 1398
- derivation_command: `python -c "$(sed -n '/BEGIN patch-site-probe/,/END patch-site-probe/p' docs/design/2026-08-20-beyond-route-a.md | grep -v '^<!--' | grep -v '^```')" tensor_grep.cli.repo_map "$SEAMS"`

#### ROW 6: src/tensor_grep/cli/repo_map.py / accept-the-pin
- module: src/tensor_grep/cli/repo_map.py
- option: accept-the-pin
- cone_lines: 0 freed
- candidate_seams: 0 (none proposed)
- affected_tests: 0
- affected_callers: 0
- estimated_edits: 0
- estimated_ci_rounds: 0
- risk: LOW mechanically; 15,243 lines is the largest Python file in the repo and stays that way
- expected_residual_floor: 6715
- derivation_command: `python scripts/measure_split_floor.py`

#### ROW 7: src/tensor_grep/cli/mcp_server.py / shrink-patched-set
- module: src/tensor_grep/cli/mcp_server.py
- option: shrink-patched-set
- cone_lines: 1055 freed (floor 2506 -> 1451); `tg_search` 446, `tg_ast_search` 344, `tg_find` 133, `_MAX_MCP_STDIO_FIRST_LINE_BYTES` 132
- candidate_seams: 4 of 66 patched symbols; 25 of 66 have a nonzero marginal cone. Unlike the other two modules the locked functions are mostly SELF-locked -- a patched tool function is locked because tests patch it, not because anything references it
- affected_tests: 16 patch sites in 4 test files (test_mcp_server, test_mcp_contract_stamp_ratchet, test_mcp_stdio_content_length_cap, test_mcp_tg_query_fanout_cap)
- affected_callers: 2 in-module name loads, 0 external -- cross-checked by `tg callers . tg_search` returning a single self-dispatch at mcp_server.py:4226
- estimated_edits: 16 (test-side only)
- estimated_ci_rounds: 2 (1 slice + 1 integration)
- risk: HIGH -- all four seams sit on the MCP tool surface, which plan W1 names as security-adjacent and assigns to W1-a, and one of them (`_MAX_MCP_STDIO_FIRST_LINE_BYTES`) is the stdio pre-auth read cap. Retargeting the tests that pin that cap changes how a DoS guard is proven
- expected_residual_floor: 1451 (1583 if the byte cap is left patched, i.e. does not reach the limit)
- derivation_command: `python -c "$(sed -n '/BEGIN cone-probe/,/END cone-probe/p' docs/design/2026-08-20-beyond-route-a.md | grep -v '^<!--' | grep -v '^```')" src/tensor_grep/cli/mcp_server.py tensor_grep.cli.mcp_server`

#### ROW 8: src/tensor_grep/cli/mcp_server.py / dependency-injection
- module: src/tensor_grep/cli/mcp_server.py
- option: dependency-injection
- cone_lines: 1055 freed
- candidate_seams: 4, of which 3 are MCP tool entry points and 1 is a byte-cap constant
- affected_tests: 16 patch sites in 4 test files
- affected_callers: 2 in-module references (one self-dispatch, one registry binding); 0 external
- estimated_edits: 18 (16 test + 2 in-module)
- estimated_ci_rounds: 3 (1 slice + 1 integration + 1 for the MCP contract-version bump a tool-signature change forces)
- risk: HIGH -- an MCP tool's signature is part of the published tool contract (`_TG_MCP_SERVER_CONTRACT_VERSION`); DI at that boundary is a wire-visible change, not an internal one. With 0 external callers there is also nothing to inject THROUGH: the collaborator would have to be threaded from the registry
- expected_residual_floor: 1451
- derivation_command: `python -c "$(sed -n '/BEGIN seam-reference-probe/,/END seam-reference-probe/p' docs/design/2026-08-20-beyond-route-a.md | grep -v '^<!--' | grep -v '^```')" src/tensor_grep/cli/mcp_server.py mcp_server "tg_search,tg_ast_search,tg_find,_MAX_MCP_STDIO_FIRST_LINE_BYTES"`

#### ROW 9: src/tensor_grep/cli/mcp_server.py / accept-the-pin
- module: src/tensor_grep/cli/mcp_server.py
- option: accept-the-pin
- cone_lines: 0 freed
- candidate_seams: 0 (none proposed)
- affected_tests: 0
- affected_callers: 0
- estimated_edits: 0
- estimated_ci_rounds: 0
- risk: LOW mechanically; this is the smallest of the three and its floor (2506) is the closest to the limit, so the pin is the most obviously reopenable
- expected_residual_floor: 2506
- derivation_command: `python scripts/measure_split_floor.py`

---

## 5. Applying the predeclared decision rule

The rule is quoted verbatim from plan §W3.2, and it was written before any number here was seen:

- **Pursue** if the costing shows the module reaching <= 1,500 for <= 150 edits and <= 3 CI
  round-trips, **with no seam crossing a security surface named in W1**.
- **Accept the pin** if the cheapest lever that reaches <= 1,500 costs > 300 edits or > 6 CI
  round-trips, or if no lever reaches <= 1,500 at any cost.
- **Escalate to a council** for anything between those bands. Silence is not acceptance.

The W1-named surfaces are quoted from plan §W1: *"the CLI front door, the MCP tool surface, the
native-front-door installer and the Windows launcher"*.

Arithmetic, per module, on the cheapest lever that reaches <= 1,500:

| module | cheapest lever | edits | CI rounds | reaches <=1500 | crosses a W1 surface | rule branch |
|---|---|---:|---:|---|---|---|
| `cli/main.py` | shrink-patched-set (ROW 1) | 23 | 2 | yes, at 226 | **yes** -- `subprocess`, the native-delegation seam in the CLI front door, and the floor stalls at 3,795 without it | pursue-test fails on the security proviso; 23 edits / 2 rounds is far below the accept thresholds (>300 / >6) -> **neither branch** -> **ESCALATE** |
| `cli/repo_map.py` | shrink-patched-set (ROW 4) | 179 | 5 | yes, at 1,398 | no -- zero broad handlers, not a W1 module | 179 is above the pursue ceiling of 150 and below the accept floor of 301; 5 rounds likewise sits between 3 and 6 -> **ESCALATE** |
| `cli/mcp_server.py` | shrink-patched-set (ROW 7) | 16 | 2 | yes, at 1,451 | **yes** -- all four seams are the MCP tool surface (W1-a), including the stdio pre-auth byte cap | same shape as main.py -> **ESCALATE** |

Two of the three escalate for the same reason and it is not cost: **the cheap levers are cheap because
they run through security seams, and the rule refuses to let cheapness buy that.** `repo_map.py`
escalates for the ordinary reason -- it lands in the middle band, twice over.

No module qualifies for `pursue`. No module qualifies for `accept` either, and writing "accept"
anyway would be exactly the silence the rule forbids.

RECOMMENDATION: ESCALATE all three modules to a council -- `src/tensor_grep/cli/main.py` (cheapest lever 23 edits / 2 CI rounds but its required `subprocess` seam crosses the W1 CLI-front-door surface, and the floor stalls at 3795 without it), `src/tensor_grep/cli/mcp_server.py` (16 edits / 2 rounds, all four seams on the W1-a MCP tool surface including the stdio pre-auth byte cap), and `src/tensor_grep/cli/repo_map.py` (179 edits / 5 CI rounds, squarely in the 150-300 / 3-6 middle band, no W1 surface crossed) -- with the council question being narrow and pre-framed: for the two security-adjacent modules, may a cheap lever run through a W1 seam if it lands AFTER W1's audit of that same seam has merged; and for repo_map, is a 179-edit / 39-test-file lever worth a floor of 1398 that clears the limit by 102 lines.

**What each escalation must carry into the council** (so the council is deciding, not re-deriving):
the row's eleven fields; the exact seam list; the named security surface and the W1 slice that owns
it; and the residual-floor bracket from section 3.1 where it applies. A council seat that cannot
reproduce a number from the derivation command beside it should treat the row as unproven.

**If a council declines to decide within this campaign**, the fallback is `accept-the-pin` per module
(ROWs 3, 6, 9) recorded as *dated and reopenable*, carrying: the measured floor, the cheapest known
lever and its cost, and the reason the lever was not taken. That is the plan's own definition of an
honest grandfather pin, and it is explicitly not a permanent three-file blanket exception.

---

## 6. The strongest argument against the recommendation

COUNTER-ARGUMENT: the recommendation is an artefact of the edit-counting convention, and under the strict convention every module returns ACCEPT instead of ESCALATE -- so the honest reading is that this document escalated three modules that the rule already decided.

Stated in full, because it is a good argument.

Section 4's convention excludes mechanical relocation from the edit count. Reaching <= 1,500 **lines**
is not the same as reaching a floor of 226: `main.py` would still have to move roughly 12,000 lines
out of the file, `repo_map.py` roughly 13,700, `mcp_server.py` roughly 3,900. Count each relocated
top-level function as one edit and the cheapest levers cost about 180, 300 and 50 *relocations* on top
of the hand edits -- `main.py` and `repo_map.py` clear 300 outright. Under that convention the rule
says **accept the pin** for at least two of the three, with no council needed and no campaign time
spent. The convention chosen here is therefore load-bearing on the outcome, which is precisely the
shape a design doc should be suspicious of in itself.

**Why it is rejected.**

1. **It makes the rule vacuous.** A threshold of 150 edits, with relocations counted, cannot be met by
   any file over roughly 1,650 lines -- the relocation count alone exceeds it before a single seam is
   touched. A decision rule that can only ever return one answer for the population it was written
   for is not a decision rule. The plan wrote 150/300 to discriminate *between levers*, and the three
   options in §W3.2 differ only in their seam mechanism; the relocation is common to all of them and
   cancels out of the comparison.
2. **The relocation is measured, not speculative, and it is already priced.** #1052 moved 4,460 lines,
   #1053 moved 4,519 and #1051 split `mcp_server`, each as one PR and one CI round. Charging a fourth
   such move at 300 "edits" contradicts three receipts in this repo's own history.
3. **It would license the wrong conclusion for the right modules.** The blocking finding for `main.py`
   and `mcp_server.py` is not cost at all -- it is that the only levers reaching the limit run through
   a W1 security seam. Recording "accept, too expensive" would file a security-scoped decision under a
   cost heading, and the next session would re-open it on cost grounds and hit the same wall.

**What would change the recommendation.** For `main.py`: a lever reaching <= 1,500 that leaves
`subprocess` patched -- section 4 shows the floor stalls at 3,795 without it, so this needs a seam the
probe does not model (a class method, a closure, or a `global` rebinding, all three named as blind
spots in `measure_split_floor.py`'s docstring). For `mcp_server.py`: W1-a merging first, after which
the four MCP seams are audited rather than unaudited, and the proviso may read differently to a
council. For `repo_map.py`: any partition of the 31 seams that reaches <= 1,500 in under 150 patch
sites -- the greedy order in the cone probe's output is the place to look, and the marginal-cone list
is flat enough (46 of 66 symbols nonzero) that a better subset is plausible but was not found here.

---

## 7. Acceptance commands

`[LOCAL]` -- Git Bash, from the repo root:

    python scripts/check_costing_doc.py docs/design/2026-08-20-beyond-route-a.md

Expected, exit 0:

    9 rows (3 modules x 3 options), 11/11 fields present, 9 derivation commands, 1 RECOMMENDATION, 1 COUNTER-ARGUMENT

`[LOCAL]`:

    python scripts/measure_split_floor.py

Expected: the three giants reporting 7,416 / 6,715 / 2,506, matching section 1. W3-a ships no
production code, so a moved floor means something else moved it and the affected rows are re-derived
before this document is cited.

`[LOCAL]`, the two campaign-wide ratchets that every slice runs:

    python scripts/file_size_budget.py --report
    python scripts/bare_call_ratchet.py

Expected: `violations: 30   grandfathered: 30` and `bare-call ratchet OK: 3 modules, 0 bare calls,
0 regressions`. Both unchanged -- this slice adds one document and one checker script.

### 7.1 The checker's own perturbation arms

A checker nobody has watched fail is a checker that reports green. Three arms were run against a copy
of this document; each result is recorded in the PR body with its exact stderr line.

| arm | mutation | expected |
|---|---|---|
| A | blank the `estimated_edits` value of ROW 4 | exit 1, `FAIL: row 4: field 'estimated_edits' missing or empty` |
| B | delete ROW 9 entirely | exit 1, `FAIL: expected 9 rows...` plus the named missing cell |
| C | delete the `COUNTER-ARGUMENT:` line | exit 1, `FAIL: expected exactly 1 COUNTER-ARGUMENT line, found 0` |

Stating which way the checker errs: it is **permissive**. It verifies that every cell of the 3x3 grid
exists, that no field is empty, that each row names a runnable command, and that the document argues
against itself exactly once. It cannot verify that a number is correct, and a fabricated figure behind
a syntactically-runnable command passes it. That is what section 4's derivation commands are for --
they are re-runnable by hand, and every one of them was run while this document was written.
