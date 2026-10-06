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
