# Dogfood fixes and selected improvements — 2026-10-05

This is the bounded implementation ledger for the approved diagnostic, subprocess decoding,
filename selection, and checkpoint label plan. Wave 3 and the five next-generation capabilities
are outside this change. Each row remains open until its own published-artifact replay passes.

| ID | Owner | Disposition | Completion trigger |
|---|---|---|---|
| DOGFOOD-DIAGNOSTICS | CLI maintainer | SHIPPED v1.123.22, PR #1214 (`d0d9f7e`) | Release CI 44 jobs; eight published replay cases pass; [raw evidence](evidence/2026-10-05-dogfood/diagnostics-publication.json) |
| DOGFOOD-DECODING | Runtime maintainer | SHIPPED v1.123.23, PR #1217 (`c0e8449`) | Main CI `37437549841` completed 44 jobs; 33 combined published replay cases passed |
| DOGFOOD-FILENAME | Ranking maintainer | SHIPPED v1.123.23, PR #1215 (`47700556`) | Same release and combined replay; context/edit and cached/session selection verified |
| DOGFOOD-LABELS | Checkpoint maintainer | MERGED, UNPUBLISHED, PR #1216 (`7ef470fe`) | Recover main CI, then complete release and published replay; [recurrence](2026-10-06-daemon-stop-recurrence.md) |
| DOGFOOD-CI-DAEMON-STOP | Runtime/CI maintainer | Active diagnosis; main CI failed after the earlier diagnostic retry passed | Capture full proof/ACK/refusal/lifecycle outcome before selecting a fix or further retry; separate from the four scoped fixes |

## Baseline and artifact identity

- Original checkout: detached `d60aff0047893db26c1f0592a92ac3c5110dd132`.
  The untracked `src/tensor_grep/cli/docs/` directory is preserved.
- Implementation base: `99c1ea17e30a57043051673dbfcd9750c4497373`, release `1.123.21`.
- Existing CI: run `37402418507`, head `5936c7839fa388383341e6118a903ddd699964c2`,
  completed successfully with 44 jobs and zero failing or unfinished jobs. This predates these fixes.
- At intake, the canonical venv metadata reported `1.123.19`; the managed launcher reported
  `1.123.20`. Neither version is treated as proof of current source behavior.
- Verification uses the canonical Windows venv with explicit worktree source imports. Its existing
  `rust_core.pyd` is copied into the verification tree without rebuilding; both copies have SHA-256
  `4c77228288637aa228ec2594a3a01ac9362819acdc4b3c9e150ff103c69df54e`.
- The only open PR at intake was documentation closeout #1212. It overlaps the task board and
  backlog; it does not own these production changes. Review and CI are recorded against exact SHAs.

## Contracts

Installer diagnostics use explicit UTF-8 replacement decoding and tolerate absent streams.
The GPU sentinel retains argv boundaries and timeouts and cannot count a sidecar success as
native GPU execution. The installed CPU binary's baseline probe accepted `-F` and returned
`NativeCpuBackend`, `gpu-auto-fallback-cpu`, `sidecar_used=false`, status `unsupported` at exit 0;
that observation is not evidence of native GPU availability.

The decoding audit assigns policies by consumer: diagnostic display may replace malformed bytes;
machine protocols and filesystem identities must not silently replace them. Its guard includes
mutation controls for aliases, wrappers, dynamic arguments, and generated Python helpers, with
explicitly bounded static-analysis claims and narrowly identified exceptions.

Filename preference matches all tokens of a multi-token stem as an ordered contiguous phrase.
Only eligible production files qualify. Existing exact/bridge symbol evidence wins; the chosen
file and its symbols move before budgets without changing scores or other candidates' order.
Context and edit planning share that preference, including cached/session use.

Checkpoint labels are optional descriptive text, trimmed to 1–120 printable Unicode characters.
Validation precedes writes. IDs remain restoration authority and duplicate labels are allowed.
Storage remains version 1; old records expose `label=null`. CLI, legacy MCP, and meta MCP create
accept labels; meta list/undo reject them with `invalid_input`. Recovery and discovery retain labels.
Older clients may discard the field when rewriting an index. MCP receives an additive minor bump.

## Architecture

The native front door handles eligible text and structural search and delegates Python-owned
commands through a sidecar. Python orchestrates repository discovery, contextual ranking, edit
planning, checkpoint storage, and MCP. Shared ranking selects files and symbols before context
budgets are applied; sessions reuse repository maps but must preserve the same selection rules.
MCP adapts those services into bounded tool contracts and stamps its own contract version.
GPU capability is established from the execution route's evidence, not from a successful process
exit or the presence of GPU-related options.

## Evidence policy

Targeted tests run with an external process deadline in the canonical Windows venv. Ruff lint,
preview formatting, mypy, file-size and bare-call ratchets precede push. Heavy native matrices and
evaluation run in CI. A fresh independent implementation review and Opus adversarial review gate
security-class changes. Release completion requires terminal exact-SHA CI, expected job population,
published Windows native assets and wheel, followed by disposable-fixture replays.

Model allocation follows the approved plan: Luna for bounded leaves and inventory, Sol for ranking
and independent review, and Opus for the specialist gate. Subagent token usage is unavailable when
the provider does not expose it; exposed headless usage is retained with the review receipt.

## Diagnostics implementation evidence

PR #1214 starts with candidate `87cdbd9` (replayed from builder `6545ac8`). In the verification
worktree, 68 tests passed (new diagnostics plus `test_cli_modes_doctor.py`). An isolated full
baseline tree with the new test file produced 4 failures and 3 passes: missing explicit encoding,
missing JSON on absent streams, and both forbidden-`-F` argv controls. These are exact assertion
failures, not a claim that the host locale reproduced a decoding exception. Ruff, preview format,
mypy (173 source files), file-size and bare-call ratchets passed. Real CPU-native probe before and
after returned exit 0, `unsupported`, `NativeCpuBackend`, `gpu-auto-fallback-cpu`, and no sidecar;
the patched command omits `-F`. [Raw diagnostic receipts](evidence/2026-10-05-dogfood/diagnostics.json) retain commands, exits,
and output; artifact identity is recorded separately from installed version.

The initial Opus review hit HTTP 429. After its reset, Opus cleared exact `7c657aa` with 37
independently run tests. Its misleading rejection-message finding was folded with a non-object
JSON failure guard in `f57bebc`. Sol and Opus cleared the resulting diagnostics head `d96bb9e`;
CI `37418321723` cleared that exact head with 38 terminal jobs and 49 clear PR checks.
PR #1214 merged as `d0d9f7e960622f868a4a41c14c8d21a6e81ac1c8`; its complete tree matches
`d96bb9e`. Seven merged-source replay rows and imported-bytecode/provenance checks pass.
Main CI `37422114884` completed successfully with 44 terminal jobs. Release commit
`911b37c7d1a4c340352439e3b4499b9ba6eeba18` tags v1.123.22. All four expected PyPI artifacts
are present and unyanked; downloaded Windows wheel/native hashes match publication metadata.
Eight actual published diagnostics replay cases pass, with raw producer/consumer output and
installed-source provenance in the [publication receipt](evidence/2026-10-05-dogfood/diagnostics-publication.json).
The physical Windows CPU binary reports GPU unsupported; injected native-success/sidecar
controls verify the proof contract without claiming native GPU hardware execution.

Independent Sol review of `87cdbd9` found an unchanged missing-boolean proof gap. The reviewed
amendment requires explicit JSON `sidecar_used=false` for native GPU success and preserves unknown
values as null in doctor and agent evidence. Implementation `6e7ec8d` also hardens the agent twin;
110 focused diagnostic, doctor, and agent GPU tests passed in the canonical Windows venv.
Sol cleared `7c657aa`; CI `37411308960` completed with 38 terminal jobs and zero failures.
Any subsequent source amendment needs new exact-head review and CI before merge.

## Subprocess implementation evidence

PR #1217 starts with implementation `88f11df70e49442a02476b3c13ed1dc4def784a4`, stacked
on diagnostics `7c657aa`. [Decoding receipt](dogfood-subprocess-receipt.md) and
[raw verification](evidence/2026-10-05-dogfood/subprocess.json) record bounded Windows checks.
The final census has 72 sinks, 16 explicit text-mode calls, and 12 generated-helper calls;
the earlier 32/23 inventory was a candidate list, not a confirmed-defect count.
Independent transport and census reviews found five and eight issues respectively; their
amendments are now undergoing exact-artifact review. No earlier self-gate clears this head.

The Opus pass on `b286e51` returned FIX-FIRST for partial MCP output loss, preview/protocol
decoding boundaries, Windows Git path failures and a latent strict-text guard allowance.
The corrected integrated source `1f3a442026d450a5b19c7ddcc6c0fc0dff633d99` passes 102 MCP/GPU,
28 path, and 69 guard tests plus 30 source replay rows. Reviews and CI are refreshed against
the final artifact; prior Sol clearance is not treated as specialist approval.
Guard-only follow-up `4fbb2c4` also closes implicit text activation through encoding/errors/
universal-newlines flags, with 82 guard tests and exact-prior acceptance/refusal controls.
Guard `1049b0a` additionally rejects concealed positional options; 94 checks pass. Sol and
Opus cleared exact final head `06e7fd4`. After diagnostics merged, its subprocess delta was
replayed onto that squash as `a347e47eeb60e5bdafc8dc4fe1a2aca832d9fba6`, with complete Git
tree equality to `06e7fd4` checked before commit. Canonical Windows re-verification passed
94 guard, 102 consumer and 28 path tests (3 POSIX skips), full Ruff/preview/mypy, size/bare
ratchets and 30 source replay rows. Final-head review and CI still apply after rebase.

## Current integrated state (2026-10-06)

Subprocess final head `733b116` passed CI `37432476137` (38 terminal jobs), independent Sol
review and mandatory Opus review. Ranking final `0314714` passed CI `37426661907` (38 jobs)
and independent Sol review. They merged in one open release window as `c0e8449` and `47700556`.
The obsolete intermediate run was cancelled before any release job started; combined main
CI `37437549841` now owns the publication hold. Thirty-two merged-source replay cases pass.
Whole-tree three-way merge checks preserve both reviewed deltas and main's newer release stamp.

Labels actual integration `e4e280a` has the exact full Git tree of reviewed `f20551a`:
`48e63c9eb40279da8299b6128588197cf9a7610c`. Its four status/inventory conflicts were resolved
from those exact reviewed bytes. Fresh status documentation is recorded separately. The new
MCP label refusal correction has 103 passing sanitization/label/wire tests and 47 source replay
cases on clean `58b7530`; those are local source results, not final CI or publication proof.
The latest mandatory Opus invocation failed with HTTP 429 before review, resetting at
2026-10-06 10:10 UTC. Retry on the final integrated artifact; no substitute clearance is claimed.

## Deferred diagnostic behavior

DOGFOOD-AGENT-GPU-FLAGS is research-gated, owned by the runtime maintainer: the agent's
sentinel and query-evidence commands retain `-F`. The sentinel is plain text, but evidence
terms have a literal-match contract; removing flags without checking pattern semantics is
outside the approved doctor-probe fix. Reopen with a native routing/escaping contract and
positive plus negative controls. Existing native-proof rejection fails closed. This item
does not block the four approved deliveries and is not claimed shipped with them.

DOGFOOD-BENCH-GPU-PROOF is research-gated, owned by the benchmark maintainer: the existing
`benchmarks/gpu_native_bench_support.py` helper treats absent sidecar proof as false. Reopen
before using that helper for a native-GPU promotion claim, with explicit-boolean negative
controls. This campaign's production diagnostic and real artifact observations do not use
that benchmark helper as proof. Its behavior is not claimed fixed here.
