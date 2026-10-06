# Checkpoint labels — implementation receipt

DOGFOOD-LABELS is **IN_FLIGHT in PR #1216**, owned by the checkpoint maintainer.
PR history: #1216. Completion requires exact-head CI, independent implementation and Opus
security review, then published Windows wheel/CLI/MCP replay. No release is claimed here.

Implementation `2744a6b3d8de7012b1b40eab4956645291193e83` was harvested from builder
`daae9c755b8dfb9fa707b01af072bc8c616c7c6d` onto base
`99c1ea17e30a57043051673dbfcd9750c4497373`. Labels are optional descriptive annotations:
trimmed, 1–120 printable Unicode characters, validated before path resolution or writes.
Duplicate labels are permitted. Checkpoint IDs and existing CLI `--last` semantics select undo
targets; labels never do. Existing positional store/dataclass callers remain valid.

Metadata, index, create/list output, discovery and recovery preserve labels. Missing or malformed
stored annotations become null without hiding the checkpoint. Storage remains v1; MCP becomes
1.12.0. Legacy/meta create accept labels and meta list/undo refuse them with `invalid_input`.
Older clients may discard labels when rewriting indexes. CLI/MCP docs and the operational skill
describe those compatibility limits and retain undo's deletion behavior disclosure.

## Verification

The canonical Windows venv imported source from the isolated delivery tree. Its existing native
extension was copied for verification, with SHA-256
`4c77228288637aa228ec2594a3a01ac9362819acdc4b3c9e150ff103c69df54e`;
it was not rebuilt. Installed metadata still says 1.123.19 and is not source-version evidence.

- 111 checkpoint tests passed in two bounded batches (73 lifecycle/label and 38 containment/budget).
- 8 checkpoint MCP checks and 14 documentation/version checks passed; total: 133 tests.
- Repository-wide Ruff and preview formatting, full mypy (174 source files), file-size and
  bare-call ratchets passed. No size baseline was raised.
- The unchanged baseline reports exit 2 with the exact `No such option '--label'` reason and no
  checkpoint writes. This is missing-feature evidence, not a negative-validation behavior claim.
- An independent mutation control removes only label normalization in memory. The no-write test
  then fails with `DID NOT RAISE <class 'ValueError'>` and an invalid annotation appears in the
  disposable index. The protected control passes without checkpoint writes.
- Disposable source replay returns raw CLI create/list/undo JSON, actual MCP tool schemas and
  create results, and list/undo label refusals with an unchanged index. Scoped undo preserves an
  unrelated modified file. This source replay is preparation for the separate published replay.

[Raw commands, exits and outputs](evidence/2026-10-05-dogfood/labels.json) distinguish baseline,
candidate and mutation arms. Earlier broad builder test batches timed out and supply no clearance.
Independent Sol review cleared implementation 2744a6b; final metadata review, exact-head CI and
the required Opus review remain separate gates. Token usage for the Sol/Luna seats is unavailable.

Merge follows diagnostics #1214, subprocess hardening and ranking #1215. Rebase and reverify
overlapping checkpoint/CLI changes before landing. Wave 3 and broader capabilities are excluded.

## Opus findings follow-up

The review of `66404251654fd9dfc8480def9833d28906b010f2` found an invalid-label legacy MCP
refusal that expanded an unconfined path, and FastMCP argument pre-parsing that converted the
printable label `"null"` to no label or rejected array/object-shaped text. The refusal now
returns `"[refused]"` before path handling or writes. A helper adapts only the two checkpoint
tools' label fields after registration: it preserves original label values before the existing
nullable argument model validates them, and retains all output-conversion metadata. Other
arguments and tools keep FastMCP's existing parsing. An absent legacy tool is supported; missing
meta registration or unsupported SDK metadata fails closed. No handler extraction was needed.

Actual `mcp.call_tool` controls cover literal `"null"`, numeric, quoted, array/object-shaped and
Unicode text through create, storage and list; non-text protocol refusals and list/undo label
refusals preserve the index and make no store/dispatch call. Schema-invalid wire values raise
the SDK's `ToolError` (the MCP protocol reports `isError`); direct Python-handler validation
still returns structured `invalid_input`. The optional nullable schema/defaults, keyword-only
store API, v1 storage and MCP1.12.0 remain unchanged. Cookbook undo wording now includes CLI
`--last`.

Canonical Windows verification passed 56 focused checks (34 new wire/SDK-boundary controls and
22 prior label checks), repository-wide Ruff/preview formatting, full mypy over 175 source
files, file-size and bare-call gates. MCP server size is 5700 lines against its 5701 baseline;
checkpoint storage is unchanged at 1499 against its 1500 limit. No size baseline was raised.
The behavioral RED had 11 failures and 19 passing controls before the fixes. External raw
gate receipts use the `labels-opus-wire-red`, `labels-opus-wire-green-1`,
`labels-opus-wire-green-2`, `labels-opus-mypy` and `labels-opus-ledger-final` prefixes.

Shortened duplicate registration documentation shifted two advisory ledger locations;
only those two line numbers were corrected to their existing AST identities. The five actual
ledger assertions pass over 340 records and 175 modules with per-module pure AST-read
memoization, and the line1 mutation still fails the exact locatability reason. This is not an
unmodified pytest ledger run. Fresh independent implementation/security review, exact-head CI
and published replay remain required; earlier verdicts do not clear this corrective delta.

This corrective implementation is `7e33d5f43ccb05ef3a0b38d55eaef09404a62c55`.
Root independently repeated all 56 focused checks, full Ruff/preview formatting, mypy (175
source files), and the bounded ledger assertions. A separate source replay passed 15 rows,
including CLI/MCP create/list/scoped undo, invalid-input no-write, literal MCP labels and
redacted invalid-path refusal. These source controls do not identify an installed release.

CI run `37419629849` on `9a5ec248` failed the existing contract-version literal ratchet in
Linux Python job `112127241179`: two new checkpoint tests embedded the live version. Test-only
correction `b7e0776327b89225fcbb76efbb4e422712ece9fe` derives both expectations from the shared
constant. The dedicated wire-review version pin is retained. The combined label/wire tests and
failing ratchet assertion (57 total) pass in the canonical Windows venv, along with focused
Ruff and preview formatting. Production source remains byte-identical to `7e33d5f`; the new
head still requires full CI and final artifact review. No failed CI run is treated as clearance.

Independent review then found the dedicated wire-review pin still at 1.11.0. An immutable
`d955493` run produced 16 passes and one exact version-equality failure after reaching the
capped producer. The reviewer exercised both real `build_repo_map` and actual MCP `tg_repo_map`
on four disposable files with a one-file cap: each returned exactly the five declared keys,
including `budget_remediable=true`, and the wire advertised 1.12.0. Following that re-review,
test-only `b62099c502633a51a3b7a1fa578d2ddf0cc8f4cd` updates the dedicated pin to 1.12.0.
The declared key population, equality assertion and literal allowlist are unchanged. Both
wire controls and the literal ratchet pass (3 tests). Production source is still unchanged.

## Combined integration

Source `d205a8e196eeadac415a4fc76c9ee6cf0f5f4f82` starts from actual main release commit
`911b37c7d1a4c340352439e3b4499b9ba6eeba18` (v1.123.22), incorporates subprocess candidate
`129e3739bd6e0ed088c07c5a67efd9e206c24329` and ranking candidate
`03147141002778460a157c4a10c59458872ee80b`, then applies reviewed labels from
`f662f61e66f272632989d69dafdfd85807e0588b`. The dependencies remain unmerged.
Shared main/checkpoint definitions preserve both reviewed ASTs; 15 dedicated source/test
files match their reviewed blobs after line-ending normalization. The strict subprocess
census retains all identities, policies, options and seven exact exceptions; only advisory
locations changed. Handler records retain identities/classifications, with one additional
out-of-span main location re-anchored.

All 15 bounded canonical Windows batches record clean `d205a8e`: 107 label/CLI/undo checks,
38 checkpoint boundary checks, 44 wire/docs checks, 124 subprocess checks (two platform
skips), 32 filename checks, 97 guard checks, cross-interpreter full inventory comparison,
full Ruff/preview/mypy, size/bare gates, handler assertions, trackers and 47 source replay
rows. Overlapping groups are not summed. Mypy covers 176 source files; the size census
has 1123 files and no regressions; no pin increased. The five real handler assertions and
negative control memoize pure AST reads, so unchanged full pytest remains a CI gate.
All three interpreter inventories match completely. The replay log combines expected
negative-control stderr with its report; the raw bundle preserves both separately as well.

Status conflicts preserve diagnostics as merged but awaiting publication/replay and the
other three items as IN_FLIGHT, with all 44 strategic rows unchanged. Exact final-head
independent/Opus reviews and CI remain required. After diagnostics release completion,
merge the three green candidates in subprocess/ranking/labels order during one window.
Verify actual merged source and final published Windows native/wheel artifacts before
claiming these items shipped. A release tag alone is not publication proof.

## Inherited test-double correction

The combined candidate now includes subprocess `574d18b` through clean merge `2b1aa12`.
Subprocess full CI had exposed stale upgrade mock signatures. Its deterministic census also
found three info-action siblings; fourteen mocks now accept explicit decoding arguments and
three assert the UTF-8/replacement policy. Runtime and scripts remain byte-identical to
independently reviewed `2efe5bb`. Superseded labels CI `37428513653` was cancelled to avoid
continuing with the known inherited test failure; it gives no clearance.

Both complete affected files pass 144 tests in the canonical Windows venv against clean
combined source `2b1aa12`, and focused Ruff/preview plus unchanged size gates pass. The raw
bundle retains exact-source clean-start receipts. Preceding PRs must land first; verify the
remaining labels delta against actual main, refreshing ancestry/metadata if needed. Squash
merges do not automatically refresh this branch's merge base. Fresh final-head independent,
security and CI gates remain required.

## Inherited import-layering correction

Clean combined source `88f405666d6cf6625d2661a0c55abb649f59d6dd` merges subprocess
`733b116ce15789144ae5ab2038012cff258e41a0` into reviewed labels `0d39ea4`. The complete
production/test/script delta is identical to the subprocess correction: three unchanged
decode helpers move into core, the CLI explicitly re-exports them, and two backends import
core. The import baseline, guard and seven exact exceptions remain unchanged. The inventory
adds only the wrapper location adjustment, preserving prior label-related locations.
Superseded labels CI `37429726601` was cancelled for this inherited defect and gives no clearance.

Ten bounded clean-source batches pass: 62 import/policy tests, strict census, complete
inventory and pin equality across Python 3.11/3.12/3.13, full Ruff/preview/mypy (177 source
files), size (1,124 files; zero regressions), bare calls, five actual memoized handler
assertions plus negative control, and 47 source replay rows. The independently reviewed
replay harness now checks parsed CLI lists, full no-write fixture state and two distinct
checkpoint IDs with the same label. Source replay is not published-artifact proof.

This supersedes the earlier three-PR burst proposal: merge green #1217 and #1215 in one
open release window, then integrate labels against their actual squash commits and obtain
new exact-head reviews and CI. A rehearsal found four status/inventory conflicts; it is
not evidence of an actual merge. Final Windows wheel/native replay remains required.

## MCP label-error sanitization correction

CI `37433094569` on `c82e9d9`, Linux Python 3.12 job `112169486515`, reached 6,697
passing tests before SEC-007 rejected the new label `ValueError` arm's `str(exc)` output.
The run was cancelled; it supplies no clearance. The same exact guard failure reproduces
in the canonical Windows venv. The independently reviewed plan has raw-byte SHA-256
`db806f226871471467f3fb4720d426c489f0d3407f2e006bbb33ef5d610b4e80`.

Luna implementation `6232dac` was harvested into clean source
`58b7530e10b7d33e810e204aa2bcdefdcb149881`. The label error arm now returns the fixed
printable-label validation message without binding or formatting the exception. Meta create
delegates to that legacy handler. Direct nontext calls retain `invalid_input` but now receive
the same generic label message; schema-invalid wire input retains its protocol boundary.
An injected private-path/sentinel error must produce the exact refused envelope, never reach
storage, and preserve the full fixture's directories, names and file bytes in both tools.

All nine bounded clean-source batches pass: 103 sanitization/label/wire tests, strict census,
full Ruff/preview/mypy (177 files), size (1,124 files; zero regressions), bare-call checks,
memoized actual handler assertions and 47 source replay rows. The 53 authorized exception
formatting sites and all security ratchets are unchanged. Only one out-of-span advisory
handler line was reanchored; the 340 identities and classifications are retained.

New-source independent review, restored mandatory Opus review, integration against actual
dependency squashes, exact-head CI and published replay remain required. The provider-limited
Opus invocation returned HTTP 429 before work and is FAILED; it does not grant clearance.

## Actual dependency squashes and final source verification

Subprocess #1217 merged as `c0e8449ebad3374d2f9168f691258b9ca899e235`; ranking #1215
merged as `47700556f404c631a58a1b9a272ea6fe37fc38dd`. Labels integration commit
`e4e280a4ab093251560480e0572b964acde56df9` has the exact full Git tree of reviewed
`f20551a`: `48e63c9eb40279da8299b6128588197cf9a7610c`. This is actual integration,
not the earlier rehearsal. Four known status/inventory conflicts retain the exact reviewed
labels blobs. Current dispositions were then refreshed in clean source `09b55be`.

All eleven bounded batches pass on that exact clean source: 103 sanitization/label/wire tests,
strict subprocess census, full Ruff/preview/mypy (177 files), unchanged size and bare-call
gates, actual memoized handler assertions, tracker and documentation checks, and 47 unique
source replay cases. The source/test/script subtrees still equal reviewed `f20551a`.
The new diagnostics publication receipt records its completed 44-job release and eight
actual v1.123.22 replay cases. Subprocess/ranking publication is pending main CI `37437549841`.

Final-head independent review, restored mandatory Opus review and full CI remain gates.
The latest Opus invocation failed with HTTP 429 before review; retry after 10:10 UTC on
2026-10-06. Earlier specialist approval cannot clear the new sanitization amendment.
Labels must wait for completed main publication before merge, then pass published replay.

## Checkpoint discovery metadata correction

Full CI `37439164677` on `b1b9447` failed the existing legacy-description contract after
7,051/7,052 passing tests in Linux Python 3.11/3.12. The label docstring rewrite had removed
the three action bullets consumed by `_annotate_legacy_tools`, so create/list/undo advertised
`action=?`. The same assertion fails locally on clean b1b and passes on actual main `47700556`.
The legacy-set iteration order can name any of the three affected tools. The run was cancelled;
two ordinary cancellation requests returned HTTP 502 before explicit force cancellation succeeded.

The amended plan has raw-worktree SHA-256
`9206b0956279b564d1a14f76f5faefe00bad21360674f03e2d1e38f5fda97409`.
Builder `a9a1673` was harvested into clean source `f2f28475c6370c3550ca249b30d896775e1e12c7`.
Only the eight-line checkpoint meta-tool docstring changes. It restores all three action
mappings while retaining confined paths, optional printable/trimmed 1–120-character labels,
create-only acceptance, opaque undo IDs and deletion of newer scoped files. The entire module
AST is unchanged after normalizing just that docstring. Its 5,700 lines stay below the unchanged
5,701-line cap; no unrelated prose or code was compressed to create room.

Ten clean-source batches pass: actual advertised-description/AST proof, all 240 affected
meta-dispatch/sanitization/label/wire/passthrough tests, strict census, full Ruff/preview/mypy,
size/bare/handler gates and 47 source replay cases. Tests, scripts, inventory, handler ledger
and all allowances are unchanged. A new exact-head independent review, restored mandatory
Opus review, fresh full CI and published artifact replay remain required. The old queued
Opus review timer was stopped before invocation so it cannot review the superseded b1b head.
