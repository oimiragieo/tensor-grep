# Dogfood fixes and selected improvements — 2026-10-05

This is the bounded implementation ledger for the approved diagnostic, subprocess decoding,
filename selection, and checkpoint label plan. Wave 3 and the five next-generation capabilities
are outside this change. Each row remains open until its own published-artifact replay passes.

| ID | Owner | Disposition | Completion trigger |
|---|---|---|---|
| DOGFOOD-DIAGNOSTICS | CLI maintainer | IN_FLIGHT, PR #1214 | Installer failure and GPU route proof regressions; release replay |
| DOGFOOD-DECODING | Runtime maintainer | Census in progress | Classified production subprocess inventory, mutation-tested guard, release replay |
| DOGFOOD-FILENAME | Ranking maintainer | Implementation in progress | Pinned precedence/order, context/edit agreement, cached/session and release replay |
| DOGFOOD-LABELS | Checkpoint maintainer | Ready | CLI/MCP/store round trips, invalid-input no-write proof, recovery and release replay |

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

The Opus health probe returned PONG, but the actual review ended with HTTP 429 / session limit
(reset advertised as 01:10 America/New_York). This is an unavailable seat, not approval. Sol
implementation review and the required Opus security gate remain separate. Nothing has merged.

Independent Sol review of `87cdbd9` found an unchanged missing-boolean proof gap. The reviewed
amendment requires explicit JSON `sidecar_used=false` for native GPU success and preserves unknown
values as null in doctor and agent evidence. Implementation `6e7ec8d` also hardens the agent twin;
110 focused diagnostic, doctor, and agent GPU tests passed in the canonical Windows venv.
The amended source needs a fresh independent verdict and Opus clearance before merge.
