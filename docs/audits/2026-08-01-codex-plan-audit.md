# 2026-08-01 Codex plan audit — backlog campaign

Audit target: `docs/superpowers/plans/2026-08-01-backlog-campaign.md`, checked against
`0126cb3b8dc67cf4e6310dfe65250f93a016c835` (`HEAD == origin/main`). The remote is
`https://github.com/oimiragieo/tensor-grep.git`. No Rust compilation, cargo command, e2e suite, or
benchmark was run.

## Findings

| severity | claim | verdict | file:line | what breaks |
|---|---|---|---|---|
| HIGH | PR-A can validate LTL once at the CLI boundary without migrating existing tests | **FALSE — BLOCKER** | `main.py::search_command` reaches the proposed insertion before `DirectoryScanner` at `src/tensor_grep/cli/main.py:8073-8076`; 15 routing tests pass the invalid LTL pattern `"ERROR"`, beginning at `tests/unit/test_cli_modes.py:3838` and continuing at `:13282-13795` | The proposed validation exits 2 before each fake backend/pipeline is reached, so the plan's own `test_cli_modes.py` and full-suite gates red after the fix. |
| HIGH | PR-B has about 13 tokenless sites: 11 in `test_session_cli.py` plus two imported harness files | **FALSE — BLOCKER** | `_ThreadedSessionDaemon.__init__` defaults `token=""` at `src/tensor_grep/cli/session_daemon.py:1737`; direct tokenless calls are at `tests/unit/test_session_cli.py:2461-3107`, `tests/unit/test_session_serve.py:356,393,457`, and `tests/unit/test_session_daemon_security.py:60,675` | The real direct-call population is 16. The plan misses an existing test that explicitly requires the old fail-open result and two valid direct-handler tests that become `unauthorized`. The two named imported harnesses already use a token. |
| HIGH | The proposed `test_routing_parity.py` LTL case will exercise the compiled native launcher on main CI | **FALSE — BLOCKER** | `_skip_if_native_binary_missing` skips at `tests/e2e/test_routing_parity.py:165-167`; `test-python` runs the file without building a release `tg` at `.github/workflows/ci.yml:442-446`; the job that does build it runs only `tests/e2e/test_native_*.py` at `.github/workflows/ci.yml:658-660,703-726` | The native arm can skip on both PR and main, so the full-path check can pass in both pre-fix and post-fix trees. There is no main-only mechanism that makes this observation achievable. |
| MED | Every proposed Task 3 test has the stated RED/GREEN role and the concrete test file passes lint | **FALSE — BLOCKER** | Invalid regexes are already classified by `_is_invalid_regex_error` and sent through `_exit_invalid_regex` at `src/tensor_grep/cli/main.py:3985-3995,4902-4911,8279-8288`; the plan imports unused `jsonlib` at `docs/superpowers/plans/2026-08-01-backlog-campaign.md:246` | The fourth proposed test is baseline GREEN, not RED. The unused import is an `F`-family violation under `pyproject.toml:94-99`. The text test also says “one-line” without asserting a line count, and the JSON test does not parse the envelope. |
| MED | PR-C fixes every known ledger canonicalization lie | **FALSE — BLOCKER** | Section 9 still says Slice 2 is path-literal at `docs/CONTRACTS.md:240`; `_ledger_physical_root` itself says it is used by claims only at `src/tensor_grep/cli/ledger_store.py:434-438`, while its five calls are at `:658,797,854,1198,1335` | Editing only the planned `CONTRACTS.md:253-263` text leaves two contradictions live, one in the same document and one in the producer's docstring. |
| HIGH | `C + D -> A -> B` is safe as written | **CONDITIONALLY CORRECT, SEQUENCING INCOMPLETE — BLOCKER** | PR-C and PR-A both edit `src/tensor_grep/cli/main.py` (`search_command` at `:6998`, `_completeness_caveat_lines` at `:11632`); PR-D and PR-A both edit `rust_core/src/main.rs` (`SEARCH_PYTHON_PASSTHROUGH_FLAGS` at `:312-319`, validation spawn at `:11045`) | A branch rebased only before its initial parallel push can be stale again after C/D merge. The plan also omits an explicit newest-main-run-completed gate after C/D and does not state `status=completed` as part of the A-to-B release gate. |
| HIGH | PR-D needs no adversarial security gate because its `apply_policy` and Rust changes are comments | **FALSE — BLOCKER** | The mandatory trigger is any security PR touching `apply_policy` or a native asset at `AGENTS.md:48-53`; PR-D edits `apply_policy.py` and the native front door at `docs/superpowers/plans/2026-08-01-backlog-campaign.md:189-207` | The sentinel retirement would merge without the repository-mandated independent Opus break-it verdict. |
| INFO | `BackendExecutionError` is the wrong taxonomy for invalid LTL syntax | **VERIFIED** | `BackendExecutionError` is a runtime engine-failure type at `src/tensor_grep/backends/base.py:7-12`; `search_command` retries every such error through `_search_with_cpu_fallback` at `src/tensor_grep/cli/main.py:8279-8284`, whose presenter labels it “search backend failed” and invokes `CPUBackend` at `:3998-4017` | Nothing breaks in this claim. A user grammar error must go directly to `_exit_search_error`, not enter the engine-retry path. |
| INFO | The Rust unit test can be observed RED in CI without local cargo | **VERIFIED** | `--ltl` is absent while `--rank`/`--bm25`/`--semantic` are present at `rust_core/src/main.rs:312-319`; the router returns `None` after its checks at `:1628-1710`; stable CI executes `cargo test --no-default-features` at `.github/workflows/ci.yml:448-513` | The proposed unit assertion is genuinely RED before the allow-list line and GREEN after it, provided the first CI run is allowed to finish and its test failure is recorded before pushing the fix. |
| INFO | `sidecar.py::_classify_lines` is dead and safe to delete | **VERIFIED** | The only code definition is `src/tensor_grep/sidecar.py:157-159`; live callers instead import/call `_classify_lines_with_metadata` at `src/tensor_grep/cli/main.py:14455-14518`, `src/tensor_grep/cli/mcp_server.py:5500-5532`, and `src/tensor_grep/sidecar.py:270` | Nothing breaks by deleting the private wrapper. Complete `tg callers` output was 0/0/0 with `result_incomplete=false`; the sibling positive control returned 4 callers across 3 files. Exact-symbol scans found no string registry, dynamic lookup, re-export, `__all__`, or test use. |

## 1. RED-arm audit

### PR-C (`docs:`)

The plan does not fabricate a behavioral RED arm. It explicitly uses a fresh call-site derivation,
AST-with-docstrings-stripped neutrality, and governance tests instead
(`docs/superpowers/plans/2026-08-01-backlog-campaign.md:116-153`). That is appropriate for a prose-only
change, but the document population is incomplete; see finding 5.

### PR-D (`chore:`)

The plan likewise says there is no RED test for dead-code removal and uses a complete zero-caller
census plus a positive control (`docs/superpowers/plans/2026-08-01-backlog-campaign.md:164-187`). The
deletion claim held under four independent lenses:

- `tg callers src _classify_lines --json` resolved the definition at
  `src/tensor_grep/sidecar.py:157-159` with zero callers/files/import consumers and
  `result_incomplete=false`.
- The positive control `_classify_lines_with_metadata` resolved four callers across three files,
  including `main.py::classify_command` at `src/tensor_grep/cli/main.py:14518` and
  `mcp_server.py::tg_classify` at `src/tensor_grep/cli/mcp_server.py:5532`.
- Exact-symbol tracked-file and hidden-file scans found only the definition and documentation; the
  code-positive matches were all longer sibling names, such as
  `sidecar.py::_heuristic_classify_lines` at `src/tensor_grep/sidecar.py:40`.
- `sidecar.py` has no `__all__`, no string-key registry, and no `getattr`/`globals` dispatch for the
  target; consumers use explicit sidecar imports such as `src/tensor_grep/cli/main.py:14455-14459`.

PR-D is still blocked by its omitted security gate, not by the deletion.

### PR-A Task 3 (Python clean error)

The two invalid-query tests are genuinely RED on the baseline. `_compile_ltl` raises `ValueError` for
the malformed grammar at `src/tensor_grep/backends/cpu_backend.py:975-981`; `search_command` handles
only `BackendExecutionError` and invalid-regex-shaped generic exceptions at
`src/tensor_grep/cli/main.py:8279-8288`. A direct CliRunner observation returned exit 1 for both text
and JSON invalid-query arms, versus the proposed exit 2.

The valid-query control is intentionally baseline GREEN. The invalid-subexpression-regex test is
also baseline GREEN: `re.error` already satisfies `_is_invalid_regex_error` at
`src/tensor_grep/cli/main.py:3985-3995` and exits 2 through `_exit_invalid_regex` at `:4902-4911`.
The plan must not record that fourth test as a RED receipt.

The proposed insertion also changes the assumptions of 15 existing tests. Each of these CALLS
`search_command` with `--ltl` and the invalid grammar `"ERROR"`; each must be migrated to a valid LTL
expression rather than waved through transitively:

1. `test_cli_should_parse_gpu_device_ids_into_search_config` — `tests/unit/test_cli_modes.py:3838`
2. `test_cli_debug_prints_pipeline_routing_reason` — `tests/unit/test_cli_modes.py:13282`
3. `test_cli_stats_prints_summary_when_matches_found` — `tests/unit/test_cli_modes.py:13322`
4. `test_cli_debug_prints_gpu_routing_details_when_available` — `tests/unit/test_cli_modes.py:13345`
5. `test_cli_stats_prints_gpu_routing_details_when_available` — `tests/unit/test_cli_modes.py:13368`
6. `test_cli_json_output_includes_routing_metadata_fields` — `tests/unit/test_cli_modes.py:13393`
7. `test_cli_json_output_should_surface_distributed_worker_metadata_from_backend` — `tests/unit/test_cli_modes.py:13430`
8. `test_cli_json_output_should_prefer_runtime_backend_metadata_over_pipeline_selection` — `tests/unit/test_cli_modes.py:13544`
9. `test_cli_debug_should_print_runtime_routing_when_backend_falls_back` — `tests/unit/test_cli_modes.py:13578`
10. `test_cli_stats_should_prefer_runtime_backend_metadata_when_backend_falls_back` — `tests/unit/test_cli_modes.py:13610`
11. `test_cli_debug_should_print_gpu_chunk_plan_when_pipeline_selected_fallback_has_no_device_ids` — `tests/unit/test_cli_modes.py:13635`
12. `test_cli_json_output_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan` — `tests/unit/test_cli_modes.py:13699`
13. `test_cli_debug_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan` — `tests/unit/test_cli_modes.py:13733`
14. `test_cli_stats_should_prefer_runtime_single_worker_gpu_metadata_over_selected_plan` — `tests/unit/test_cli_modes.py:13770`
15. `test_cli_stats_prints_summary_when_no_matches` — `tests/unit/test_cli_modes.py:13795`

The sibling `test_cli_disables_ripgrep_passthrough_for_ltl_mode` already uses a valid expression at
`tests/unit/test_cli_modes.py:10453` and is the population positive control.

The JSON test should use its currently-unused `jsonlib` alias to parse and assert the complete
`_search_error_payload` presenter shape (`version`, `schema_version`, `ok`, `error`, `detail`) defined
at `src/tensor_grep/cli/main.py:4838-4847`. That both fixes the lint failure and tests the presenter,
not merely a substring from its producer.

### PR-A Task 4 (Rust registration)

The unit test is a valid CI-only RED arm. Its input has no other condition that returns `Some`:
`search_format_python_passthrough_args` checks the missing allow-list member at
`rust_core/src/main.rs:1653-1655`, then eventually returns `None` at `:1710`. The stable Rust matrix
runs this test on all three operating systems at `.github/workflows/ci.yml:448-513`.

The proposed parity “belt” is not an oracle as sequenced. `test_routing_parity_matrix` skips the whole
parameter when the native launcher is absent (`tests/e2e/test_routing_parity.py:427-447`), and no job
both builds `rust_core/target/release/tg` and runs this file. Also, the claimed `--rank` template does
not exist in this file; the real insertion surface is `COMMAND_CASES` at
`tests/e2e/test_routing_parity.py:377-424`. A grep-zero for `--rank` is unresolved without that
positive insertion anchor.

### PR-B (daemon fail closed)

The new direct-method test is genuinely RED because the current falsy-token branch returns `True` at
`src/tensor_grep/cli/session_daemon.py:1763-1766`. It is not sufficient as the only new behavior test:
the presenter is `_SessionDaemonHandler.handle`, which turns a failed check into the `unauthorized`
envelope at `src/tensor_grep/cli/session_daemon.py:1800-1823`. The plan should also exercise a real
tokenless request through that handler/socket seam.

More importantly, the migration census is wrong. Every direct tokenless constructor was called and
classified:

- `test_session_cli.py` has the stated 11: `:2461,2528,2584,2634,2708,2775,2845,2897,2968,3036,3107`.
  Each starts a server and sends one or more `_daemon_request(..., token="")` calls, e.g.
  `tests/unit/test_session_cli.py:2475-2477` and `:3122-3125`.
- `test_session_serve.py` has three omitted direct constructors at `:356,393,457`. The malformed-JSON
  test at `:354-366` fails before auth and need not inject a request token, but the valid requests at
  `:423-444` and `:461-480` will become `unauthorized` unless their server/request tokens are paired.
- `test_session_daemon_security.py` has two omitted tokenless constructors. The first explicitly pins
  the opposite policy in `test_tokenless_server_stays_backward_compatible` at `:58-65` and must be
  replaced or inverted. The second at `:672-689` never handles a request and is behaviorally
  unaffected, but it still belongs in the census.
- The plan's two supposed extra tokenless harness files are false positives. Both import
  `_real_daemon`; that helper defaults to and forwards `token="test-token"` at
  `tests/unit/test_symbol_daemon_autostart.py:73-75`. Representative calls are
  `tests/unit/test_orient_agent_daemon.py:93` and `tests/unit/test_graph_completeness_oracle.py:443`.

Thus there are 16 direct tokenless constructions, and 14 existing tests visibly need changes: the 11
session CLI tests, the two valid direct-handler tests, and the old backward-compatibility assertion.

## 2. `--ltl` exception taxonomy

The plan's taxonomy is correct, with one wording clarification: the proposed implementation does not
raise `ConfigurationError`; it behaves like the existing configuration-error boundary by calling
`_exit_search_error("invalid_ltl_query", ...)` directly.

`ConfigurationError` represents unsatisfied explicit routing intent
(`src/tensor_grep/core/pipeline.py:20-21`) and is presented cleanly at pipeline construction by
`search_command` (`src/tensor_grep/cli/main.py:8082-8097`). `BackendExecutionError` instead represents
a runtime engine fault (`src/tensor_grep/backends/base.py:7-12`). At the per-file boundary every such
error is announced as “search backend failed” and retried on `CPUBackend`
(`src/tensor_grep/cli/main.py:3998-4017,8279-8284`). Converting `_compile_ltl`'s user grammar rejection
to `BackendExecutionError` would therefore enter the wrong retry/presenter path and then invoke the
same CPU LTL parser again. The CLI-boundary clean user error is the correct fix class.

## 3. PR-C document census

The five-call code ground truth is correct, but the planned file census is not. In addition to the
known contradiction at `docs/CONTRACTS.md:253-263`, section 9 still says Slice 2 remains path-literal at
`docs/CONTRACTS.md:240`. The producer's own `_ledger_physical_root` docstring also says the helper is
used by claims “ONLY” and Slice 2 keeps plain path resolution at
`src/tensor_grep/cli/ledger_store.py:434-438`. Both are disproved by
`record_finding`/`find_findings` calling the helper at `:1198,1335`.

PR-C must include both corrections. Because that adds a Python docstring-only edit in
`ledger_store.py`, apply the same AST-neutrality proof there that the plan already requires for
`main.py` (`docs/superpowers/plans/2026-08-01-backlog-campaign.md:116-135`).

## 4. Merge and release sequencing

The release classification is correct: the repository title contract maps `fix` to patch and
`docs`/`chore` to none at `scripts/validate_pr_title_semver.py:10-25`. C and D do not collide with each
other and may batch before any release is in flight. A and B are releasing and must remain one per
publish.

The plan nevertheless needs two sequencing repairs:

1. PR-A shares `main.py` with PR-C and `rust_core/src/main.rs` with PR-D. The general instruction to
   rebase “before pushing” at `docs/superpowers/plans/2026-08-01-backlog-campaign.md:46` is insufficient
   when all branches may be pushed in parallel before C/D merge. Rebase PR-A onto the merged C+D tip,
   verify its HEAD/branch ref, and rerun the union before it is mergeable.
2. After the C+D batch, wait for the newest main `ci.yml` run to reach `completed` before merging A.
   After A, require both that captured main run to be `status=completed` and the new version to be
   served by PyPI before merging B. The repository explicitly requires both signals at
   `AGENTS.md:44-47,241-247`; merely seeing a release commit/tag or querying a still-running run is not
   the gate.

## 5. PR-D mandatory security review

PR-D records a security retirement in `apply_policy.py` and mirrors it in the native front door
(`docs/superpowers/plans/2026-08-01-backlog-campaign.md:189-207`). `AGENTS.md:48-53` requires an
independent Opus adversarial gate for every security PR touching `apply_policy` or a native asset,
regardless of diff size. Task 2 currently stops at local gates and a draft PR; add the binary
`SHIP | FIX-FIRST(file:line + repro + minimal fix)` gate and post its evidence to the PR artifact.

## Verdict and must-fix list

VERDICT: BLOCK

Must fix before dispatch:

1. Add `tests/unit/test_cli_modes.py` to PR-A and explicitly migrate all 15 invalid `"ERROR" --ltl`
   routing fixtures to valid LTL syntax.
2. Repair Task 3's concrete test plan: parse/assert the JSON envelope with `jsonlib`, assert the
   promised one-line text shape, and relabel the invalid-subexpression case as baseline GREEN rather
   than a RED receipt.
3. Put the LTL cross-launcher case in a CI path that builds and requires the native binary (for example
   a `test_native_*.py` suite run by `native-build-smoke`), with no skip-permitted native arm.
4. Replace PR-B's census with the 16 direct tokenless constructors above; update the existing opposite
   security test, pair server/request tokens for the 13 live valid-request harnesses, classify the two
   no-auth sites explicitly, and add an over-the-wire tokenless `unauthorized` presenter test.
5. Expand PR-C to correct `docs/CONTRACTS.md:240` and
   `ledger_store.py::_ledger_physical_root` as well as the already-planned text, with AST-neutrality
   proof for the Python docstring.
6. Add the mandatory independent adversarial security gate to PR-D and record the verdict on the PR.
7. Make the drain collision-aware: complete C+D and their newest main CI run, rebase/reverify A on that
   merged tip, then gate A-to-B on the captured A main run being completed plus PyPI publication.

