# Published dogfood verification — 2026-10-06

The four scoped changes shipped in 1.124.0. Diagnostics first shipped in
1.123.22. The primary checkout cleanup and historical intake are recorded separately.

| Change | Implementation PR / merge | Published outcome |
|---|---|---|
| Installer diagnostics and GPU proof | #1214 / `d0d9f7e` | UTF-8 and malformed diagnostics survive; absent streams retain return-code fallback; GPU probe omits `-F` and requires explicit native execution proof |
| Subprocess output policy | #1217 / `c0e8449ebad3374d2f9168f691258b9ca899e235` | Diagnostics decode tolerantly; protocol/path consumers preserve their stricter contracts; static guard covers 72 sinks with seven exact exceptions |
| Shared filename selection | #1215 / `47700556f404c631a58a1b9a272ea6fe37fc38dd` | Ordered multi-token filename phrases guide context and edit selection without changing numeric scores or unrelated ordering; exact/bridge symbol evidence still wins |
| Checkpoint labels | #1216 / `7ef470fe1880825c32af8e124104e8a90391d14d` | CLI, store and both MCP create tools preserve optional labels; invalid labels precede writes; stable IDs select restoration |

## Artifact and execution evidence

| Evidence | Exact artifact | Result |
|---|---|---|
| diagnostics pr | [`37418321723`](https://github.com/oimiragieo/tensor-grep/actions/runs/37418321723), `d96bb9edd7420cc2cbde336eff58a398e328fa04` | 38 terminal jobs, zero failed/unfinished |
| subprocess pr | [`37432476137`](https://github.com/oimiragieo/tensor-grep/actions/runs/37432476137), `733b116ce15789144ae5ab2038012cff258e41a0` | 38 terminal jobs, zero failed/unfinished |
| ranking pr | [`37426661907`](https://github.com/oimiragieo/tensor-grep/actions/runs/37426661907), `03147141002778460a157c4a10c59458872ee80b` | 38 terminal jobs, zero failed/unfinished |
| labels pr | [`37443145599`](https://github.com/oimiragieo/tensor-grep/actions/runs/37443145599), `e6f180fe5e5e226979ecbedf281837e8ebc95bdc` | 38 terminal jobs, zero failed/unfinished |
| diagnostics release | [`37422114884`](https://github.com/oimiragieo/tensor-grep/actions/runs/37422114884), `d0d9f7e960622f868a4a41c14c8d21a6e81ac1c8` | 44 terminal jobs, zero failed/unfinished |
| dependencies release | [`37437549841`](https://github.com/oimiragieo/tensor-grep/actions/runs/37437549841), `47700556f404c631a58a1b9a272ea6fe37fc38dd` | 44 terminal jobs, zero failed/unfinished |
| daemon diagnostic pr | [`37464813923`](https://github.com/oimiragieo/tensor-grep/actions/runs/37464813923), `6f7a54a3855958ed928f258449b3f4c7e63c2c29` | 38 terminal jobs, zero failed/unfinished |
| labels release | [`37486528327`](https://github.com/oimiragieo/tensor-grep/actions/runs/37486528327), `bd368355f3dd58906cf8dd3b3bbd21675d4fc377` | 44 terminal jobs, zero failed/unfinished |
| Final release tag | `v1.124.0`, `c19930256cc26a628e061cdad3d5f96df06b9350` | Expected PyPI artifacts present and unyanked |
| Windows wheel | `tensor_grep-1.124.0-cp311-abi3-win_amd64.whl` | SHA-256 `a982a677c3506776282f251cf346a1628e573c5382796942d930fc1376edf736` |
| Windows CPU executable | `tg-windows-amd64-cpu.exe` | SHA-256 `dddc9ce863255d2e7fb8c0c2162914b8ade13359085edb355bf92d2ccde1796d` |
| Installed package bytes | 178 Python/extension members | All equal the published wheel ZIP |

Each CI claim names its actual tested head. Merge commits, release commits and installed
artifact versions are separate identities. The published Windows wheel was installed into an
isolated environment outside all checkouts; its Python package and compiled extension resolve
from site-packages. The downloaded native executable uses that exact interpreter for Python
commands. Raw checksums, import origins, commands, streams and exits are retained in
[the publication evidence](evidence/2026-10-05-dogfood/publication.json).

The final replay has **53 unique passing rows**: 48 consumer/native observations, four
native-to-wheel checkpoint operations and one discovery case covering all three legacy
checkpoint action descriptions. These include duplicate labels on two checkpoints,
restoration of the older checkpoint by ID, preservation of the newer checkpoint and unscoped
file, exact invalid-input refusals, and unchanged full fixture contents after refused MCP
actions. The wrappers independently require the exact population, version, row shape and
passing verdicts, while preserving child exit status separately.

The supplemental discovery case calls the installed server's actual `list_tools` interface
with the legacy surface enabled. It requires exactly one correct meta-tool/action note for
each checkpoint create/list/undo tool and rejects unknown actions, missing or duplicate notes.
Its recorded MCP 1.12.0 value is the producer's declared contract; the existing payload cases
separately verify response contract stamps. All three reports bind to the same installation.

| Replay case | Result |
|---|---|
| `repair_env_utf8_malformed` | PASS |
| `repair_env_absent_output` | PASS |
| `repair_env_timeout` | PASS |
| `gpu_proof_NativeGpuBackend_False` | PASS |
| `gpu_proof_GpuSidecar_True` | PASS |
| `gpu_proof_NativeGpuBackend_None` | PASS |
| `gpu_non_object_json` | PASS |
| `decoding_ast_positive` | PASS |
| `decoding_ast_malformed_stderr` | PASS |
| `decoding_ast_malformed_stdout` | PASS |
| `decoding_ast_utf8_input` | PASS |
| `decoding_git_error` | PASS |
| `decoding_agent_exit_0` | PASS |
| `decoding_doctor_exit_0` | PASS |
| `decoding_agent_exit_17` | PASS |
| `decoding_doctor_exit_17` | PASS |
| `decoding_pip_stdout_valid` | PASS |
| `decoding_pip_stdout_malformed` | PASS |
| `decoding_pip_stderr_valid` | PASS |
| `decoding_pip_stderr_malformed` | PASS |
| `decoding_mcp_index_partial` | PASS |
| `decoding_mcp_diff_content` | PASS |
| `decoding_mcp_index_invalid_protocol` | PASS |
| `decoding_mcp_index_failed_protocol` | PASS |
| `decoding_windows_git_codemap` | PASS |
| `decoding_windows_git_revision` | PASS |
| `decoding_timeout_counts_lf` | PASS |
| `decoding_timeout_counts_nul` | PASS |
| `decoding_ndjson_path_match` | PASS |
| `decoding_ndjson_path_context` | PASS |
| `filename_render` | PASS |
| `filename_edit` | PASS |
| `label_create` | PASS |
| `label_list` | PASS |
| `label_scoped_undo_by_id` | PASS |
| `label_invalid_no_write` | PASS |
| `label_tg_checkpoint_create` | PASS |
| `label_tg_checkpoint` | PASS |
| `label_mcp_reject_list` | PASS |
| `label_mcp_reject_undo` | PASS |
| `label_literal_tg_checkpoint_create_null` | PASS |
| `label_literal_tg_checkpoint_create_["x"]` | PASS |
| `label_literal_tg_checkpoint_create_{"a":1}` | PASS |
| `label_literal_tg_checkpoint_null` | PASS |
| `label_literal_tg_checkpoint_["x"]` | PASS |
| `label_literal_tg_checkpoint_{"a":1}` | PASS |
| `label_invalid_path_redacted` | PASS |
| `real_native_gpu_observation` | PASS |
| `native_cli_label_create` | PASS |
| `native_cli_label_list` | PASS |
| `native_cli_scoped_undo_by_id` | PASS |
| `native_cli_invalid_label_no_write` | PASS |
| `checkpoint_legacy_action_descriptions` | PASS |

Installer and GPU producer controls are explicitly injected; malformed-stream controls run
real bounded child processes against the installed consumers. The real native observation
uses the published CPU executable. Its honest CPU fallback is a passing diagnostic result,
not evidence of GPU hardware execution. No NVIDIA execution or universal ranking advantage
is claimed.

## How the components fit together

The native executable handles eligible text and AST search, then delegates Python-owned
commands through a sidecar. Python coordinates discovery, ranking, edit planning, checkpoints
and MCP. The filename preference is applied in shared ranking before result budgets; context,
edit planning and cached/session paths therefore use the same target-selection rule. It is
a deterministic retrieval correction, with repository-specific precedence and ordering tests.

Sessions retain repository maps for repeated operations. Checkpoints save scoped file state
under stable IDs; labels are descriptive metadata and may repeat. CLI and MCP call the same
checkpoint services, so validation, recovery and retention share the same storage behavior.
Storage stays version 1; MCP contract version is 1.12.0. Older clients may drop labels when
rewriting an index. GPU capability depends on route evidence, including explicit
`sidecar_used=false`, rather than process success alone.

## Verification and preserved scope

Focused Windows verification used the canonical venv with explicit worktree imports and an
externally bounded deadline. Full Ruff, preview formatting, mypy, size and exception ratchets
passed; the required Python, Rust, CUDA-feature, GPU-contract and benchmark CI jobs completed.
GPU-contract CI does not establish execution on NVIDIA hardware. Independent Sol and specialist
Opus reviews cleared exact implementation artifacts. A separate harness review found and
corrected four false-positive oracles before final artifact replay; mutation controls include
a wrong producer that restores the latest checkpoint instead of the requested ID.

Labels CI needed one explicitly recorded failed-job-only retry on unchanged source. The first
Windows Python 3.11 attempt failed an unchanged daemon shutdown test; the same-head Windows
3.12 population, prior-main Windows 3.11 population and bounded local module passed. The
successful retry does not establish a cause or erase the failed attempt. Raw first-attempt and
retry evidence are retained in the publication bundle. `DOGFOOD-CI-DAEMON-STOP` remains an
unresolved intermittent observation owned by the Runtime/CI maintainer: any recurrence requires
full stop-result and request/refusal/thread timing evidence before another retry. That
labels-PR retry changed no timeout, assertion or source.

Labels' first main run 37453425025 on merge `7ef470fe1880825c32af8e124104e8a90391d14d` FAILED and remains failed
historical evidence, with no additional main retry. Test/docs-only diagnostic PR #1218,
reviewed `6f7a54a3855958ed928f258449b3f4c7e63c2c29`, merged as `bd368355f3dd58906cf8dd3b3bbd21675d4fc377` with labels'
merge as its sole parent. The complete recovery release CI and its four Windows observations
(Python 3.11/3.12, natural subject and controlled shutdown hold) passed. These observations
establish capture behavior, not the historical cause. Eight distinct successful CI roles are
retained separately from the failed main. Diagnostic capture landed; cause remains unproved.
Production daemon behavior, the one-second stop deadline and cooperative predicate were unchanged.
Fresh same-head Sol/Opus diagnostic reviews and hash-bound helper review are separate from labels'
earlier clearance; failed 529 and 429 review invocations remain failures.

At this verification checkpoint, the primary checkout was clean on `refs/heads/main` at
`7ef470fe1880825c32af8e124104e8a90391d14d` following user-authorized cleanup.
Initial detached `d60aff0047893db26c1f0592a92ac3c5110dd132`
and the intermediate detached checkout at 7ef with untracked `src/tensor_grep/cli/docs/` remain historical checkpoints.
These external recovery helpers do not change or rewind that checkout. Canonical venv metadata 1.123.19
and intake launcher 1.123.20 are distinct from source pyproject1.123.23; neither claims the release is installed for
the user. Only the isolated published-artifact environment was upgraded for this verification.

All four dogfood items are closed by their published receipts. The canonical 44 strategic
rows are unchanged. DOGFOOD-AGENT-GPU-FLAGS and DOGFOOD-BENCH-GPU-PROOF remain explicitly
research-gated with owners and reopen triggers in the [campaign ledger](2026-10-05-dogfood-fixes.md).
Wave 3, the five next-generation capabilities and unrelated PR #1212 remain outside scope.
Exposed review token usage is retained with the evidence; Sol/Luna usage is unavailable.
Reported provider cost fields use list-price accounting and are not claimed as actual spend.

The final evidence assembler also received an independent review. It now requires eight
distinct successful CI roles in the separate recovery path on their exact heads, verifies the complete raw job population, and binds
all three replay reports to the same installed wheel and interpreter, with the regular and
frontdoor reports also bound to the published native executable. Complete reviewed harness
hashes are mandatory. Twenty-seven assembler mutation controls and 27 supplemental discovery
controls check contradictory receipts and tool descriptions. A further 34 closure controls
refuse missing, failed, wrong-head or mismatched final review and merged-source evidence.
Eighteen CI-history controls reject contradictory retry/job identities and outcomes. These
controls are separate from the 53 real published replay cases and are not counted as product
tests.

Recovery validator and capture/helper controls are recorded separately in the raw bundle. They use controlled fixtures and are not hosted CI or published product cases. The four actual recovery Windows diagnostic observations are also separate from the 53 published replay cases.
