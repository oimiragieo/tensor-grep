# 2026-08-02 Backlog Closeout Campaign Design

**Status:** Task 2 live-truth amendment pending thinktank re-review
**Branch:** `campaign/backlog-closeout-2026-08-02`
**Scope:** every AI-actionable backlog item, open bug, and tracker contradiction; CEO-, hardware-, publication-, and spend-gated items become explicit decision records and are not autonomously executed.

## 1. Objective

Close the live backlog without treating historical prose as current truth and without bundling several independent product programs into one unreviewable change. At campaign completion every discovered item must be in exactly one state:

1. `SHIPPED` with a merged-commit and published-artifact receipt;
2. `RETIRED` with current contract/source evidence and a re-open trigger;
3. `BLOCKED` with a reproducible external prerequisite and an owner;
4. `CEO-GATED` with the decision and financial/publication consequence stated; or
5. `ACTIONABLE` with an approved, dependency-ordered implementation plan.

No item may remain merely “open,” “maybe fixed,” or “investigate later.”

## 2. Ground truth at design time

- `main` is `8fc51f8448cae6261235d30e3164843ee088d460`.
- Main CI run `30765407062` completed successfully.
- GitHub release and PyPI both serve `v1.102.1` / `1.102.1`.
- Draft PR #910 is the only open PR. Its checks are green and it repairs the task-board section structure plus stale #904 state.
- GitHub issue #48 is the only open issue and remains CEO-gated.
- `docs/TASK_BOARD.md` is the operational queue, GitHub is authoritative for PR/issue state, and `docs/BACKLOG.md` is evidence/history plus the August 2 dogfood findings. Dispatch always re-derives all three.

## 3. Approaches considered

### A. One branch containing the whole backlog

This produces one very large review surface across MCP, CLI contracts, evidence, language parsers, Rust routing, and multi-root orchestration. It also makes release attribution and rollback ambiguous and conflicts with the repository's one-release-per-publish discipline.

### B. Evidence-first sequential campaign — selected

First repair tracker truth, then close small contract/security residue, then deliver independent product programs in dependency order. Every release-affecting slice gets its own branch, PR, CI proof, adversarial review, merge, and published-wheel dogfood receipt. Shared-file language changes drain sequentially and union their assertions after each rebase.

### C. Tracker-only closeout

This would accurately retire stale work but leave the actionable MCP disclosure, atomic-writer ratchet, edit verification, language depth, and multi-root work unfinished.

## 4. External research and resulting constraints

Research was run with Exa on 2026-08-02 and restricted to official specifications, project documentation, and source repositories.

### MCP discovery

The current MCP specification makes capability discovery and version negotiation explicit, with server capabilities and supported versions available before ordinary calls. Tensor-grep's own `tg_mcp_capabilities` is therefore the correct additive location for a `tool_surface` disclosure; changing the default advertised set is a separate compatibility decision.

Sources:

- https://modelcontextprotocol.io/specification/2026-07-28/server/discover
- https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
- https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- https://github.com/github/github-mcp-server

Design consequences:

- Add `tool_surface: "full" | "lean"` to `tg_mcp_capabilities`.
- Keep `TG_MCP_LEGACY_TOOLS` default-on.
- Bump tensor-grep's MCP contract from `1.7.0` to `1.8.0`.
- Test both import-time flag states in subprocesses and the real stdio protocol.
- Do not represent `tool_surface` as a core MCP protocol version or imply that clients negotiated it.

### Controlled edit workflows

Current tools separate pre-edit intent/scope from deterministic post-edit verification, compare the actual patch with declared boundaries, and emit a receipt. They also distinguish a graph-risk signal from proof of runtime correctness.

Sources:

- https://orenlab.github.io/codeclone/guides/agent-safe-change/
- https://github.com/orenlab/codeclone
- https://github.com/vk0dev/code-impact-mcp

Design consequences:

- Implement `verify-edit` before `edit-ready` so the immutable comparison contract exists before orchestration.
- `verify-edit` is pure by default: it reads a baseline capsule and repository state but does not execute commands or mutate the ledger.
- `edit-ready` is not an alias for `prepare`. It adds a strict cooperative-readiness ticket with explicit named identity, successful claim, persisted baseline, complete analysis, and zero unresolved conflicts; it is not a security authorization.
- Blast-radius heuristics remain evidence, not a guarantee. Validation results stay a separate evidence family.

### Evidence and attestation

SLSA/in-toto verification binds evidence to a subject digest, verifier identity, policy, and expected source/repository identity. Unknown security-sensitive policy inputs should fail verification rather than be ignored.

Sources:

- https://slsa.dev/spec/v1.1/provenance
- https://slsa.dev/spec/v1.2/verifying-source
- https://slsa.dev/verification_summary

Design consequences:

- Edit receipts bind to `gitCommit` plus dirty-tree digest and canonical repository identity.
- The schema has an explicit major version and additive minor evolution rules.
- Verification records the exact policy/schema digest and rejects unknown required policy fields.
- A locally produced receipt is evidence of deterministic comparison, not an unsupported SLSA compliance claim.

### Parser-backed caller/reference extraction

Tree-sitter's code-navigation vocabulary distinguishes definition and reference roles, including `@reference.call`, and its generated `node-types.json` provides the grammar-specific node shapes needed to prevent guessed queries.

Sources:

- https://tree-sitter.github.io/tree-sitter/4-code-navigation.html
- https://tree-sitter.github.io/tree-sitter/using-parsers/queries/1-syntax.html
- https://github.com/tree-sitter/tree-sitter/blob/1ffd612b/docs/src/using-parsers/6-static-node-types.md

Design consequences:

- Each language wave begins with live AST-shape/node-type fixtures.
- Extract definitions, references, and calls as separate roles.
- Include same-named decoys and unsupported-shape honesty tests.
- Never infer cross-file import resolution from a same-named file alone.

## 5. Campaign architecture

### Program 0 — tracker reconciliation

Drain #910 after its own independent review. Rebase the campaign worktree on the resulting `origin/main`, then regenerate the live inventory. Close stale entries only with source, test, PR, and published-artifact receipts.

Known reconciliation decisions:

- F1/#22: retire as settled by `docs/CONTRACTS.md`; exit 2 means incomplete, not “complete but interpret carefully.”
- F2: retire the claim that outright anonymous refusal was never considered. `ledger_store.resolve_agent_id` records that it was considered and rejected for the legacy surface.
- #90: split the mixed historical item honestly—its doctor exit-127 half shipped in PR #571, while the bounded WSL portability arm was a non-reproducing/non-defect retirement. Close the combined task without calling both halves shipped.
- #109: mark shipped by PR #605 and remove it from the hardware-blocked queue.
- #89: run a bounded current WSL reproduction. An unavailable environment remains `BLOCKED`; only a bounded clean reproduction with raw output may retire the historical 9p condition, while a reproduced failure becomes `READY` or remains environment-blocked with the exact trigger.
- #36 and #37: mark shipped by #903 and #908.
- #859: reopen the original class-level atomic-writer ratchet; the codemap-specific test did not satisfy it.
- F10/MaxSim, DD-004, DD-006, full AST DSL parity, MCP lean-default, and continuous refresh: assign stable canonical IDs and record them, together with #255, as the complete demand-gated population with explicit triggers. The canonical block has a unique machine-parsed handoff-version line and an exact closed-world ID/status set shared with `SESSION_HANDOFF.md`.

### Program 1 — concrete security and MCP residue

#### Atomic CLI writer ratchet

Create an AST-based census test for CLI functions that publish user-facing artifacts. The census detects direct write-to-temporary-then-publish patterns and direct destination writes, proves the detector on the exact unmodified pre-#869 codemap blob, discovers every generated-Python execution root from production spawn callsites, fails closed on dynamic/unparseable payloads, and pins the complete production population through a lexical-scope-aware resolver plus an independent raw-call inventory. Generated source and sanctions use exact source/callsite/operation/destination-provenance fingerprints; a whole-function exemption is forbidden. Approved shared atomic helpers are the normal route. The live deep-dive found three production violations to drive genuine REDs: `_write_json_refuse_symlink` (including caller-side pre-resolution that erases leaf identity), `_write_ast_project_scaffold`, and `new`.

Route their caller-selected JSON/YAML artifacts through shared parent-handle-anchored writers and preserve each site's existing semantics: refresh/ruleset artifacts may overwrite, while `new` and scaffold `sgconfig.yml` remain atomic create-if-absent/no-clobber. POSIX publication is directory-fd-relative; Windows uses an opened no-reparse parent plus handle-relative create/rename operations. A path recheck followed by a path rename is not sufficient. Installer/runtime directory swaps remain separately enumerated because their replacement and rollback contract is not equivalent to a JSON/text artifact writer. Pre-existing and dangling symlinks must be refused. Event-gated late-leaf and parent-directory swap/junction tests must prove no external artifact changes; an overwrite writer may safely replace a leaf symlink entry only when it never follows the link and the contract says so explicitly.

#### MCP tool-surface disclosure

Add `tool_surface` derived from the same import-time `TG_MCP_LEGACY_TOOLS` decision used to build both the live registry and capability map. The field and registry must never disagree. The default remains `full`; recognized off tokens produce `lean`. Contract, docs, unit tests, subprocess flag tests, stdio integration tests, and package version pins move together.

#### Backend surface decision

Rust `CpuBackend.replace_in_place` is already a public API: the public `backend_cpu` module is built into the crate's `rlib`, and both the type and method are public. Retain its exact `fn(&CpuBackend, &str, &str, &str, bool, bool) -> anyhow::Result<()>` signature with a compile-time external assertion, retain streaming traversal, and propagate directory-walk/literal/regex child errors with stable contextual messages. The public method must unconditionally delegate through the same private injectable core used by fault tests, so a disconnected test seam cannot create false evidence. Direct-file errors already propagate; nonexistent-path and direct-leaf-symlink semantics remain unchanged because they require a separate compatibility/security decision. An in-repository caller census still documents observed use, but cannot prove absence of downstream Rust consumers. Removal is outside this campaign unless separately approved as a breaking API change with deprecation and migration plans.

The Python `CPUBackend` has a separate A27 twin defect: two raw PyO3 search adapters retry without `invert_match` after any `TypeError`, unlike `RustCoreBackend`, which already retired that unsafe compatibility pattern. Route the inline simple path through `_rust_match_set`, reduce that helper to one exact-signature native call, and map an internal native `TypeError` to `BackendExecutionError` without a second call, dropped inversion, or fixed-string Python fallback. Genuine native absence retains its explicit import-error fallback. Retain `CPUBackend`, `RustCoreBackend`, and the PyO3 class; their contracts differ and consolidating the public classes would create a dependency cycle.

### Program 2 — deterministic edit verification

Introduce a shared internal module, `tensor_grep.cli.edit_verification`, rather than embedding more policy in `cli/main.py`.

#### `EditBaseline` schema

Version 1 contains:

- canonical repository identity and root;
- `gitCommit`, branch/ref when available, dirty flag, and dirty-tree digest;
- a bounded, canonical `preexisting_changes` manifest for every dirty/staged/untracked path at baseline time: porcelain status, current and original path for renames, object kind (`regular | symlink | deleted | other`), normalized worktree mode, byte size, content SHA-256 for regular files, symlink-target text plus digest for symlinks, and for every tracked path the stage-0 index mode/object ID plus index flag;
- query and selected primary target;
- explicitly editable paths;
- review-only/blast-radius paths;
- validation plan and command descriptors, but no execution result;
- prepare completeness, scan-limit, deadline, confidence, ambiguity, and coordination status;
- creation time, producer CLI version, and schema/policy identifiers.

The wire schema is exact; unknown keys and wrong/null values are invalid rather than ignored:

```text
EditBaselineV1 = {
  version: 1, schema_version: 1, edit_baseline_version: 1,
  producer_version: str, created_at: RFC3339-UTC str,
  policy: {id: "cooperative-edit-v1", sha256: lowercase-64-hex str},
  repository: {root: canonical-absolute str, identity: str,
               object_format: "sha1"|"sha256",
               git_commit: lowercase-hex str of length 40 for sha1 or 64 for sha256,
               git_ref: str|null,
               dirty: bool, dirty_tree_sha256: lowercase-64-hex str},
  request: {query: str, agent_id: nonempty str, primary_target: PrimaryTargetV1,
            editable_paths: list[repo-relative str],
            review_only_paths: list[repo-relative str],
            blast_radius_paths: list[repo-relative str],
            validations: list[ValidationDescriptorV1]},
  prepare: PrepareSnapshotV1,
  manifest: {complete: bool, path_count: nonnegative-int,
             total_hashed_bytes: nonnegative-int,
             incomplete_reasons: list[EditReason]},
  preexisting_changes: list[PathStateV1],
  trust: TrustDisclosureV1
}
ValidationDescriptorV1 = {
  id: nonempty str, argv: nonempty-list[str], cwd: repo-relative str,
  timeout_seconds: finite-float (0,3600],
  expected_exit_codes: nonempty-unique-list[int 0..255]
}
PrimaryTargetV1 = {
  file: repo-relative str, symbol: nonempty str|null,
  kind: nonempty str|null, line: positive-int|null
}
PathStateV1 = {
  path: repo-relative str, original_path: repo-relative str|null,
  porcelain_status: two-character str,
  index_mode: ("100644"|"100755"|"120000"|"160000")|null,
  index_object_id: lowercase-hex str|null whose length matches repository.object_format,
  index_flag: ("ordinary"|"assume-unchanged"|"skip-worktree")|null,
  worktree_kind: "regular"|"symlink"|"deleted"|"other",
  worktree_mode: ("100644"|"100755"|"120000")|null,
  size: nonnegative-int|null, sha256: lowercase-64-hex str|null,
  symlink_target: str|null, symlink_target_sha256: lowercase-64-hex str|null
}
TrustDisclosureV1 = {coverage: "git-visible", authorization: false,
                     ignored_paths_unobserved: true,
                     identity_trust: "self-asserted"}
PrepareSnapshotV1 = {
  result_incomplete: bool, incomplete_reasons: list[str],
  scan_limit_reached: bool, deadline_exceeded: bool,
  confidence: finite-float|null, ambiguous: bool,
  ask_user_before_editing: bool
}
EvidenceEditVerificationComponentV1 = {
  edit_verification_version: 1,
  baseline_sha256: lowercase-64-hex str,
  policy_sha256: lowercase-64-hex str,
  verifier_version: nonempty str,
  verification_result_sha256: lowercase-64-hex str,
  verdict: "PASS"|"WARN"|"BLOCK"|"INCOMPLETE",
  reasons: list[EditReason],
  coverage: "git-visible", authorization: false,
  ignored_paths_unobserved: true, identity_trust: "self-asserted"
}
```

Lists are deduplicated and canonically path/ID sorted. `review_only_paths` stores only caller-declared review scope, while `blast_radius_paths` stores the baseline builder's canonical caller-file set; they are never collapsed, so future widening can distinguish a previously observed caller from a newly observed path the caller predeclared for review. `original_path` is non-null only for a Git rename; copied files retain ordinary modified/untracked semantics because copy detection is configuration-dependent and is not a v1 promise. `index_flag=null` if and only if the path has no tracked index entry; an indexed path must carry one of the three literals. Regular files require mode/size/SHA-256 and null symlink fields; symlinks require mode/target/target digest and null byte SHA-256; deleted entries require all worktree metadata null. Strict baseline creation returns `INCOMPLETE` and writes no baseline when any `assume-unchanged` or `skip-worktree` index flag is present.

`PrepareSnapshotV1` is a pure projection over the shared prepare service's private `(legacy_payload, capsule)` pair; it does not reparse rendered text and does not change legacy `tg prepare` output:

- `confidence` is `capsule.confidence.overall` only when it is a finite number, otherwise null;
- `ambiguous` is exactly `bool(capsule.ambiguity.requires_confirmation)`; `tie_requires_confirmation` is true while `none` and `tie_resolved` are false;
- `ask_user_before_editing` is exactly `bool(capsule.ask_user_before_editing.required)`;
- `scan_limit_reached` is true when any capsule `scan_limit` or `caller_scan_limit` dictionary has `possibly_truncated=true`, or `caller_scan_truncated=true`;
- `deadline_exceeded` is true when either payload has `partial=true` with `partial_reason` in `{deadline, deadline_exceeded}`, or either `deadline_limit.deadline_exceeded=true`;
- `incomplete_reasons` is the ordered subset of `scan_limit`, `deadline`, `partial_other`: add `scan_limit` for any scan signal and `deadline` for any deadline signal. Evaluate `legacy_payload` and `capsule` independently; add `partial_other` when either source has `partial=true`/`result_incomplete=true` and that same source has neither its own scan signal nor its own deadline signal. Thus a capsule scan signal cannot incorrectly explain an unrelated legacy partial (or vice versa);
- `result_incomplete` is exactly `bool(incomplete_reasons)`.

Complete, confirmation-tie, validation-resolved tie, deadline-partial, scan-truncated, unrelated-partial, and mixed scan+deadline+unrelated-source fixtures pin this projection against real prepare/capsule objects. The existing shell-shaped `validation_plan`/`validation_commands` rows are deliberately not converted into `ValidationDescriptorV1`; cross-platform shell parsing would invent policy.

The manifest is capped before reading file content (10,000 paths, 64 MiB total hashed bytes, 8 MiB per file). The canonical encoded baseline has one 5 MiB inclusive writer/reader cap; a generated 5 MiB + 1 payload is rejected before persistence, so every successfully written baseline can read and self-verify. Crossing any cap makes the baseline incomplete; no sampled subset is authorized. Status collection uses exactly `git status --porcelain=v1 -z --untracked-files=all`, so nested untracked files never collapse into an unobservable directory entry. Tracked index identity comes from NUL-delimited `git ls-files --stage -z`; index flags come from a separately bounded NUL-safe `git ls-files -v -z` adapter. Any unmerged stage 1/2/3 entry or assume-unchanged/skip-worktree flag makes the strict baseline incomplete. Verification compares current normalized worktree mode/executable metadata, content identity, and content-addressed stage-0 index metadata, so executable-bit-only mutations and an `MM` path that changes its staged content while restoring the same worktree bytes are detected. This also allows unchanged pre-existing out-of-scope dirt to remain distinct from the agent's edit delta.

The strict baseline output is confined to the tensor-grep-owned `.tensor-grep/edit-baselines/` state directory beneath the canonical repository root. No caller-selected outside-repository path is accepted. Publication is anchored to an already-opened, identity-verified owned-directory handle; path checks followed by path-based publication are forbidden. Unix creates/fsyncs the temporary through `openat(dirfd, ..., O_CREAT|O_EXCL|O_NOFOLLOW)`, publishes with no-replace `linkat(dirfd,temp,dirfd,NAME)`, cleans with `unlinkat`, and fsyncs `dirfd`. Windows opens/verifies the directory handle, creates the temp relative to it through `NtCreateFile`/`OBJECT_ATTRIBUTES.RootDirectory`, and performs a handle-relative `FileRenameInfoEx`/`FILE_RENAME_INFO` rename with `RootDirectory` and replace=false. No path-based Windows fallback is allowed; if the required handle-relative primitive is unavailable, strict readiness returns `INCOMPLETE/baseline_write_failed`. Any existing file, directory, symlink, reparse point, same-NAME race, or Event-gated parent swap returns `baseline_write_failed`; the losing `edit-ready` call removes only its temp and rolls back only its exact claim ID. That exact owned directory is excluded consistently from both baseline and verification dirty-state collection so persisting the baseline cannot invalidate itself. Unix and mandatory Windows tests swap the parent after handle verification but before temp creation/publication and prove no outside file appears.

Manifest hashing uses opened-handle verification rather than check-then-read paths. Unix opens with `O_NOFOLLOW` and verifies the opened descriptor with `fstat`. Windows opens with `CreateFileW(..., FILE_FLAG_OPEN_REPARSE_POINT, ...)`, rejects a reparse-point leaf, obtains the final path plus volume serial/file ID from that same handle, requires the final path to remain inside the canonical repository root, and hashes only through that handle. Each adapter requires a regular file, streams capped chunks up to `limit + 1`, and compares file identity/metadata before and after the read. If a platform cannot provide that no-follow/final-path opened-handle guarantee, the operation returns `INCOMPLETE` with `platform_no_follow_unavailable` before reading content; a final-path escape returns `opened_path_escape`. FIFOs/devices/other file kinds and identity changes also make the baseline incomplete. Both adapters receive deterministic Event-gated swaps. Unix has a FIFO arm; Windows CI has mandatory, non-skipped leaf-reparse and parent-junction fixtures and asserts their test IDs executed.

The baseline is invalid for a strict readiness PASS if any required field is missing, analysis is partial, ambiguity requires user input, the primary target is unresolved, the pre-existing-change manifest is incomplete, or the agent identity is anonymous.

#### `tg verify-edit`

The public Python/native argv is frozen as:

```text
tg verify-edit REPO --baseline NAME --baseline-sha256 DIGEST
  --validation-file FILE [--deadline SECONDS] --json
```

`NAME` is a basename matching `[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json`, resolved only beneath canonical `REPO/.tensor-grep/edit-baselines/`. `DIGEST` is the external trust anchor: exactly 64 lowercase hexadecimal characters. The safe reader hashes the exact opened baseline bytes and compares them to `DIGEST` before JSON/schema/policy use; a mismatch returns `INCOMPLETE/baseline_digest_mismatch`. It accepts only exact `EditBaselineV1`, never an agent capsule or heuristic conversion. `FILE` has the same exact structured-validation, confinement, duplicate, and 256 KiB contract as `edit-ready`; it supplies the current declared descriptors for drift comparison. Deadline defaults to 60 seconds and is finite in `[0.1,300]`. The command is read-only. `edit-ready` self-verification passes the SHA-256 computed from the exact no-clobber bytes it just published.

It computes:

- baseline digest validity;
- repository identity and revision drift;
- actual changed files from Git, including untracked files;
- a per-path comparison against `preexisting_changes`, so unchanged pre-existing dirt is not attributed to the edit while same-path content/status/symlink-target mutations are;
- changed paths outside editable scope;
- current target existence: re-resolve the baseline `PrimaryTargetV1.file`; when `symbol` is non-null, require the same parser-backed symbol/kind still exists in that file (line movement alone is allowed), otherwise require the file itself;
- current blast-radius floor, recomputed under the deadline from the baseline's query/structured primary target through the same prepare blast-radius builder. Paths already in `baseline.request.blast_radius_paths` are unchanged; new paths wholly inside caller-declared `review_only_paths` produce WARN; any other new path produces BLOCK;
- byte-for-byte equality between normalized descriptors in `--validation-file` and `baseline.request.validations`;
- `PASS | WARN | BLOCK | INCOMPLETE`, reasons, and a receipt digest.

`PASS` requires a complete Git-visible comparison and no scope/revision/policy violations. `WARN` is a complete comparison whose only advisory condition is blast-radius widening wholly contained within already-declared review-only paths. `BLOCK` is a complete comparison that found a Git-visible violation. `INCOMPLETE` means the comparison itself could not establish the contract.

The exact result and strict-readiness ticket schemas are:

```text
EditVerificationResultV1 = {
  version: 1, schema_version: 1, edit_verification_version: 1,
  verifier_version: nonempty str,
  verdict: "PASS"|"WARN"|"BLOCK"|"INCOMPLETE",
  reasons: list[EditReason], baseline_sha256: lowercase-64-hex str|null,
  policy_sha256: lowercase-64-hex str|null,
  repository: {root: canonical-absolute str|null, identity: str|null,
               object_format: ("sha1"|"sha256")|null,
               git_commit: format-consistent lowercase-hex str|null,
               dirty_tree_sha256: lowercase-64-hex str|null},
  changed_paths: list[PathDeltaV1], current_primary_target_exists: bool|null,
  current_blast_radius: list[repo-relative str],
  validation_descriptors_match: bool|null,
  receipt_sha256: lowercase-64-hex str|null,
  trust: TrustDisclosureV1
}
PathDeltaV1 = {
  path: repo-relative str,
  change_types: nonempty-list["added"|"deleted"|"renamed"|"content"|
                              "worktree_mode"|"index_mode"|"index_object"|
                              "status"|"kind"|"symlink_target"],
  before: PathStateV1|null, after: PathStateV1|null
}
EditReadyTicketV1 = {
  version: 1, schema_version: 1, edit_ready_version: 1,
  status: "ready"|"blocked"|"incomplete", reasons: list[EditReason],
  agent_id: str|null, claim_id: str|null,
  baseline_path: canonical-absolute str|null,
  baseline_sha256: lowercase-64-hex str|null,
  prepare: PrepareSnapshotV1|null,
  verification: EditVerificationResultV1|null,
  trust: TrustDisclosureV1
}
```

`EditReason` has this complete precedence order; results sort first by this rank and then by canonical path: `invalid_json`, `duplicate_json_key`, `unsupported_schema`, `missing_required_field`, `invalid_field`, `input_limit`, `baseline_byte_limit`, `baseline_digest_mismatch`, `result_byte_limit`, `path_limit`, `total_byte_limit`, `file_byte_limit`, `git_output_limit`, `index_unmerged`, `index_flag_unsafe`, `platform_no_follow_unavailable`, `opened_path_escape`, `unsafe_file_type`, `file_identity_changed`, `manifest_incomplete`, `repository_mismatch`, `revision_drift`, `preexisting_state_changed`, `out_of_scope_change`, `target_missing`, `validation_descriptor_drift`, `blast_radius_widened_outside_review_scope`, `blast_radius_widened_within_review_scope`, `prepare_incomplete`, `ambiguity_requires_input`, `anonymous_identity`, `claim_overlap`, `claim_fence_timeout`, `claim_fence_error`, `ledger_error`, `baseline_write_failed`, `self_verification_failed`.

The mapping is exhaustive and disjoint. `{invalid_json, duplicate_json_key, unsupported_schema, missing_required_field, invalid_field, input_limit, baseline_byte_limit, baseline_digest_mismatch, result_byte_limit, path_limit, total_byte_limit, file_byte_limit, git_output_limit, index_unmerged, index_flag_unsafe, platform_no_follow_unavailable, opened_path_escape, unsafe_file_type, file_identity_changed, manifest_incomplete, prepare_incomplete, ambiguity_requires_input, anonymous_identity, claim_fence_timeout, claim_fence_error, ledger_error, baseline_write_failed, self_verification_failed}` maps to verification `INCOMPLETE`, ticket `incomplete`, and exit 2. `{repository_mismatch, revision_drift, preexisting_state_changed, out_of_scope_change, target_missing, validation_descriptor_drift, blast_radius_widened_outside_review_scope, claim_overlap}` maps to verification `BLOCK`, ticket `blocked`, and exit 1. `blast_radius_widened_within_review_scope` maps to verification `WARN`, ticket `blocked` (strict readiness requires PASS), and exit 1. Empty reasons map to verification `PASS`, ticket `ready`, and exit 0. Tests enumerate every enum member and fail if it appears in zero or multiple sets. Malformed input still emits the full result/ticket key set with unavailable nullable fields null, then exits 2.

`EditVerificationResultV1.receipt_sha256` reuses `evidence_signing.receipt_digest` and `canonical_receipt_bytes` (`tg-canonical-json-v1`): compact key-sorted ASCII JSON with top-level `receipt_sha256` and `signature` excluded; this result has no signature. Digest-stability and one-field mutation tests pin the preimage rule. The result uses one canonical compact UTF-8 serializer with a 5 MiB inclusive final-wire cap; CLI measurement includes its trailing newline, and evidence ingestion measures the exact bytes read from its file or stdin transport. If a complete result would exceed the cap, the producer emits the same full key set with `verdict="INCOMPLETE"`, ordered reasons prefixed by `result_byte_limit`, `changed_paths=[]`, `current_blast_radius=[]`, `validation_descriptors_match=null`, and `receipt_sha256` recomputed for that bounded envelope. It never emits a sampled partial as PASS/BLOCK/WARN. Fixed-size fields plus bounded paths guarantee the fallback fits, asserted at cap−1/cap/cap+1; therefore every emitted result can be ingested by `tg evidence emit`.

The threat model is cooperative-agent safety, not a hostile local-user sandbox. Git ignored files outside declared editable paths are not observable from Git status, and a keyless receipt digest proves only self-consistency/deduplication. Every payload therefore carries `coverage: "git-visible"`, `authorization: false`, `ignored_paths_unobserved: true`, and `identity_trust: "self-asserted"`. Documentation and consumers must not call a PASS an authorization decision. Declared editable ignored files are explicitly hashed within the same caps, but detecting arbitrary writes to other ignored paths requires an external filesystem sandbox and is outside this command.

Every file-backed baseline, validation descriptor set, verification result, primary receipt, and previous receipt uses one safe bounded JSON reader. It confines the supplied path to its declared repository/owned root; opens a regular file through the same Unix `O_NOFOLLOW` or Windows reparse-rejecting/final-path handle adapter used by manifest hashing; reads at most `cap+1` through that handle; verifies final path plus file identity before/after; and only then decodes through a duplicate-rejecting `object_pairs_hook`. FIFOs/devices, leaf links/reparse points, parent junction escapes, identity swaps, oversize files, and duplicate keys at any nesting depth fail before semantic use. Caps are transport-specific (5 MiB baseline/verification/receipt, 256 KiB validation file) but the safety adapter is singular. The evidence-only `--edit-verification -` sentinel instead reads standard input exactly once to EOF or `cap+1`, applies the same exact-byte cap and duplicate-rejecting decoder, and cannot be combined with any other stdin-consuming evidence option. Verification never relies on Python's last-key-wins behavior. The same semantic component validation runs after cryptographic verification for signed and keyless receipts: when `edit_verification` is present, all four trust fields are mandatory and exact even on a freshly and correctly re-signed receipt. Legacy receipts with no component remain valid.

The component has a production ingress through the additive existing-subcommand option:

```text
tg evidence emit REPO --edit-verification FILE|- [existing evidence/signing options] --json
```

`FILE` must resolve inside `REPO` through the safe bounded-reader adapter; `-` is the production verify-to-evidence handoff and consumes the exact `verify-edit --json` stdout bytes from standard input without materializing a repository file that would change the dirty digest. Both transports are capped by the same 5 MiB verification-result limit before decoding as exact `EditVerificationResultV1`. The builder first verifies the result's own `receipt_sha256` against its exact canonical bytes. It copies the result's digest to `verification_result_sha256` and copies the result-producing `verifier_version` verbatim; the current evidence emitter never relabels an older result as newly verified. The receipt builder—not the adapter—captures the canonical root, repository identity, object format, commit, and dirty-tree digest exactly once through the same revision helper/exclusion policy used by `verify-edit`. Inside that same builder call it compares the result to this immutable capture, derives `EvidenceEditVerificationComponentV1`, places the identical captured subject in the outer receipt, and signs it; there is no second Git read and no caller-supplied revision override. Cross-repository inputs and clean/dirty/revision drift visible at that capture exit 2. An Event-gated mutation between adapter read and builder capture must therefore mismatch and fail; a mutation after capture does not rewrite the signed subject and is honestly represented as occurring after the captured revision. Malformed, duplicate-key, null/invalid result digest, or semantically inconsistent input also exits 2 without emitting a receipt. Existing invocations without the option and legacy receipts without the component remain byte-compatible. The option may coexist with the existing capsule/manifest inputs provided none consumes stdin and is exercised through Python and compiled-native front doors in both keyless and signed modes. A subprocess-based round trip runs `verify-edit`, checks its exit code directly, passes its captured stdout bytes as evidence stdin without a shell pipeline, and checks the signed/keyless evidence exit and subject. It proves PASS producer 0/consumer 0; valid WARN and BLOCK producer 1/consumer 0 with the producer status retained and receipt verdict unchanged; digest-valid `result_byte_limit` INCOMPLETE producer 2/consumer 0 with the failure retained and receipt verdict unchanged; and malformed/null/invalid-digest input consumer 2 with no receipt. Direct shell piping is unsupported unless the invoking shell/harness preserves and checks both process statuses; a consumer zero can never erase producer 1/2.

Exit policy:

- 0: `PASS`;
- 1: `WARN` or `BLOCK` with a complete result;
- 2: `INCOMPLETE`, invalid input/schema, or failed verification.

Validation execution is not part of this program because it creates a command-execution trust boundary absent from cooperative comparison. It remains owned by the security program with trigger “a separately approved allowlist/execution policy and threat model”; only then may a dedicated PR produce command-result evidence.

#### `tg edit-ready`

This is a strict cooperative-readiness composition over shared prepare and baseline builders. It never changes legacy `prepare` or ledger semantics and never claims to be a security authorization boundary.

The public Python/native argv is frozen as:

```text
tg edit-ready REPO QUERY [--agent-id ID] --validation-file FILE --out NAME
  [--editable PATH ...] [--review-only PATH ...] [--deadline SECONDS] --json
```

`QUERY` is 1..16,384 UTF-8 bytes. `--agent-id` is parser-optional so omission reaches the full JSON error envelope, but the service requires it for readiness; when present, `ID` matches `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. There is no configured-identity fallback on this strict surface. `--deadline` defaults to 60 seconds and is finite in `[0.1,300]`; strict readiness has no unbounded arm. `NAME` uses the same basename grammar as `verify-edit` and always resolves beneath `.tensor-grep/edit-baselines/`. `FILE` is read by the shared safe JSON adapter at cap−1/cap/cap+1 around 256 KiB and contains an exact array of 1..32 descriptors; zero/33 rows, unknown/duplicate/null keys, and wrong JSON types are invalid. Descriptor IDs are unique and match the agent-ID grammar. `cwd` must normalize to an existing canonical directory inside the repo. `argv` has 1..64 strict JSON strings: `argv[0]` is nonempty, all strings reject NUL, each is at most 8,192 UTF-8 bytes, and the total is at most 65,536 bytes. `timeout_seconds` is a finite JSON number but not a boolean; `expected_exit_codes` has 1..32 unique strict JSON integers (booleans excluded) in 0..255. Duplicate normalized `(cwd, argv)` descriptors are invalid. The existing shell-shaped prepare validation rows are advisory only and never synthesized into argv.

Scope normalization is singular and deterministic. The structured prepare primary target is always editable. Caller `--editable` paths are optional additions (maximum 256 total). Caller `--review-only` paths normalize into `request.review_only_paths`; every `blast_radius_floor.top_callers[].file` normalizes separately into `request.blast_radius_paths`. The three final lists are disjoint with precedence `editable > blast_radius > review_only`: derived blast identities already editable are removed; caller review identities also present in derived blast are stored only in blast. A caller explicitly repeating the primary/editable identity, repeating an option identity, or placing one identity in both caller categories is invalid rather than silently deduplicated; duplicate derived blast rows are safely deduplicated. Path identity uses canonical existing handles/parents plus `normcase` on case-insensitive platforms, so spelling/case aliases cannot form two scopes; output retains one canonical repo-relative slash spelling and is identity-deduplicated then lexical-sorted. NULs, absolute/out-of-root paths, symlink/junction escapes, or more than 256 total identities across all three scope lists are `invalid_field`/exit 2. Existing editable/review-only/blast leaves must be regular files inside the repo; a not-yet-created editable leaf is allowed only when its nearest existing parent is a real directory inside the repo without a symlink/reparse escape, and two nonexistent leaves that normalize to one platform identity are duplicates. Nonexistent review/blast paths, directories as edit leaves, and special/reparse/symlink leaves are invalid. The exact normalized structured primary target and three separate scope lists—not caller ordering—are stored in `EditBaselineV1.request`.

Exit 0 requires all of:

- explicit valid `--agent-id` (no configured-identity fallback);
- a resolved primary target;
- no `ask_user_before_editing` requirement;
- complete capsule and blast-radius analysis;
- an atomic `--out` baseline write;
- a successfully submitted claim owned by that named identity;
- zero pre-existing overlapping claims across all identities, including the same self-asserted agent ID;
- at least one structurally valid validation descriptor;
- a self-verification pass over the persisted baseline.

Every claims-index mutation path—legacy submit, strict submit, and release—uses a callback-style claims RMW API that acquires one crash-released per-root OS fence, reads under it, invokes the mutation callback, and releases only after any required publication. The sole pre-fence exception is release's existing absent-index fast path: a missing claims index returns its legacy no-op immediately and creates neither ledger directories nor a fence; because no claims record exists to remove, this is not an RMW mutation. A callback returns either `WRITE(records, result)` or `NO_WRITE(result)`; only `WRITE` atomically publishes. This preserves an existing-index/no-match release byte-for-byte, including inode and mtime. The callback never receives a publish handle or writable snapshot it can commit later. The fence is a claims-only, stable `<claims-index>.fence` artifact that normal operation never unlinks or atomically replaces; it is distinct from the stale-reclaimable lease file and does not silently alter checkpoint/session/finding lock semantics. Its root, state directory, and fence path are confined and checked for symlink/reparse-point escapes, and the fence is opened no-follow with opened-handle identity verification. Unix opens `O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW` mode `0600` and attempts `flock(LOCK_EX|LOCK_NB)`. Windows uses `CreateFileW(OPEN_ALWAYS, FILE_FLAG_OPEN_REPARSE_POINT)` with read/write sharing but no delete sharing, then `LockFileEx(LOCKFILE_EXCLUSIVE_LOCK|LOCKFILE_FAIL_IMMEDIATELY)` on byte `[0,1)`. Both poll every 20 ms for at most 12 s in production; tests inject a sub-second deadline. A timeout raises `ClaimsFenceTimeoutError`, a subclass of the already-mapped `IndexLockTimeoutError`, so legacy commands keep their existing exit-2 behavior; strict JSON maps it to `claim_fence_timeout`/exit 2. The acquisition order is always OS fence before any retained lease metadata. The reclaimable lease may remain diagnostic, but it is never the serialization or fencing primitive. The strict operation checks every pre-existing overlap and inserts only while holding the OS fence; identity never suppresses an overlap. Exactly one of two concurrent conflicting strict claims may succeed. Same-root operations mutually exclude while different roots remain independent. Any ledger read/fence/write failure is fail-closed on this strict surface even though legacy `prepare` remains advisory. A conflicting strict call emits a full ticket with `status="blocked"`, `reasons=["claim_overlap"]`, null claim/baseline fields, exit 1, and no state write. Every ticket carries `authorization: false`; its named identity is coordination metadata, not authenticated ownership.

### Program 3 — foundational-language caller/reference depth

Five sequential releases: Java, C#, PHP, C, C++. Each implements grammar-backed reference/call extraction through the existing `LanguageDescriptor.references_and_calls` seam. C and C++ may share design utilities but remain separately tested and released.

Per-language acceptance:

- live grammar/node-shape fixture;
- free function/static call;
- instance/member call;
- constructor/type reference where the grammar supports it;
- same-name definition and string/comment decoys;
- nested/qualified name behavior;
- grammar unavailable and parse-error honesty;
- deterministic ordering and deduplication;
- pinned agent/retrieval ranking output;
- no fabricated cross-file target.

Cross-file import resolution is a follow-on architecture slice for Java, Go, PHP, C#, C, and C++. It reads real package/build/module/include configuration and reverse-confirms that the target exports the symbol. Unresolved mappings remain explicit.

### Program 4 — federated multi-root prepare

Refactor the existing prepare composition into a reusable service, then add a versioned workspace aggregator.

Contract:

- explicit roots only; no implicit parent-directory crawl;
- canonical, non-overlapping roots confined to the declared workspace anchor;
- maximum eight roots;
- one shared absolute deadline;
- deterministic root ordering and stable result ordering;
- per-root result, error, partial reason, and omitted-root record;
- aggregate `result_incomplete=true` when any required root is omitted/incomplete;
- no “missing result means no conflict” interpretation;
- token/output ceilings and 1/2/8-root CI benchmarks;
- ledger claims remain root-scoped until CEO-gated cross-root enforcement is designed.

The CLI is a separately versioned command:

```text
tg workspace-prepare ANCHOR QUERY --root ROOT [--root ROOT ...] [--deadline SECONDS] --json
```

`--root` is semantically required and repeatable from one through eight values; the Typer adapter accepts an empty list so zero-root input reaches the service and produces the JSON error envelope rather than parser prose. Relative roots resolve against `ANCHOR`; absolute roots are accepted only when their canonical path is contained by canonical `ANCHOR`. Symlink escapes, duplicate canonical roots, nested roots, zero roots, and more than eight roots are input errors. Query input is 1..16,384 UTF-8 bytes; each supplied/canonical anchor or root is at most 32,768 UTF-8 bytes; deadline must be finite with `0 < seconds <= 300`. Even one root returns the workspace schema rather than the legacy single-root `prepare` payload, leaving `tg prepare` byte-compatible. Roots execute sequentially in canonical path order under one shared absolute deadline; the campaign makes no root-parallelism claim.

The shared service and CLI return exactly one workspace schema version 1 envelope: `{version, schema_version, workspace_prepare_version, routing_backend, routing_reason, anchor, query, roots, root_count, completed_root_count, omitted_roots, result_incomplete, incomplete_reasons, error}`. `roots` is canonically path-sorted and every entry is `{root, status: complete|partial|error, payload, error, incomplete_reason}` with inapplicable fields null. Success and partial results use top-level `error=null`. Invalid input returns the same full key set with canonical `anchor` or null, `roots=[]`, both counts zero, `result_incomplete=true`, `incomplete_reasons=["invalid_input"]`, and a fixed-vocabulary/bounded `error={"code":"invalid_input","message":...}`. One compact UTF-8 serializer owns the 8,388,608-byte inclusive final-wire cap. It accepts transport fields and terminal suffix before measuring: CLI bytes include its single trailing newline; MCP bytes include `mcp_contract_version` and no newline. If the full result exceeds the cap, it rebuilds the same envelope with per-root payloads omitted, `result_incomplete=true`, and `output_limit`; the input limits above guarantee this minimal envelope fits, which is asserted rather than assumed. Cap−1/cap/cap+1 tests measure actual returned bytes after transport injection. Exit 0 means every root completed; every invalid/schema/path input or any partial/omitted/failed root exits 2. JSON is emitted before every post-parse exit 2. Exit 1 is unused because this command has no valid not-found state. For fixtures below the cap, service, Python CLI, compiled native CLI, and MCP stdio are value-identical after removing only `mcp_contract_version`; success and partial arms both enforce this equality.

The MCP adapter first confines the caller's anchor through `_confine_mcp_path` under `_mcp_root()`, then confines every canonical root under both that resolved anchor and `_mcp_root()`. A caller cannot widen MCP authority by supplying a broader anchor.

CLI exposure precedes MCP exposure. MCP exposure is a separate contract-versioned PR.

## 6. Error and security model

- All paths are canonicalized and confined before use.
- Symlinks are refused at artifact destinations before resolution.
- Baselines and receipts are bounded before JSON parsing.
- Unknown schema major versions and unknown required policy fields fail closed.
- Digests use canonical JSON and bind the repository/revision/policy subject.
- No shell command strings are constructed; any future validation runner accepts an executable plus argv under an allowlist, fixed cwd, deadline, and output cap.
- MCP, backend, receipt verification, ledger locks, validation execution, native assets, installers, and migration changes require an independent adversarial `SHIP | FIX-FIRST(file:line, repro, minimal fix)` review.
- Local CPU-heavy evaluation and cold Cargo work are prohibited; decisive matrices run in GitHub Actions/cloud.

## 7. Delivery and review loop

For each implementation slice:

1. Refresh GitHub/main/PyPI truth and enforce the WIP/release gate.
2. Create or rebase an isolated worktree branch.
3. Write the smallest red contract test first.
4. Confirm the red failure is for the intended missing behavior.
5. Implement the minimum change.
6. Run focused tests with explicit timeouts.
7. Run scoped Ruff, preview-format, and mypy checks locally.
8. Run an independent specification review, then an independent quality/security review.
9. Fix every finding and repeat with a fresh reviewer until `APPROVE`/`SHIP`.
10. Open one PR, attach review evidence, wait for CI, then drain only after the latest main publish is complete.
11. Re-verify the merged artifact and the published wheel.
12. Update task board, backlog, handoff, contracts, and dogfood receipt table.

## 8. CEO-, financial-, and external-resource gates

The following are planned but not autonomously executed:

- #72 public benchmark claim;
- #131/#169 GPU hardware, asset-profile promotion, or public performance claims;
- #48 native-front-door startup architecture;
- #77/F9 automatic ledger enforcement in agents or CI;
- #255 native dedup/FFI, int8, warm-server, or GPU investment selection;
- paid cloud/GPU capacity or any action with a direct financial commitment.

The campaign may create evidence and decision records for these items. Execution requires a separate user decision because it changes spend, public claims, or established CEO-gated scope.

## 9. Completion definition

The campaign is complete only when:

- all AI-actionable items above are shipped or retired with evidence;
- every independent thinktank and final code/security gate is clean;
- all PRs are merged through an open release gate;
- latest `main` is green;
- the latest required version is present on GitHub and PyPI;
- a clean-environment published-wheel table passes every wheel-visible changed contract, and separately labeled exact-merged-SHA CI/source receipts pass every internal-only changed contract;
- `docs/TASK_BOARD.md`, `docs/BACKLOG.md`, `docs/SESSION_HANDOFF.md`, `AGENTS.md`, and affected contract/docs files agree;
- CEO/financial items remain explicit decisions, not falsely reported as complete.

## 10. Thinktank audit record

This section is updated after each independent review round.

- Round 1 architecture: `FIX-FIRST` — aggregate dirty-status hashing could not identify same-path edits; MCP surface state was not frozen at import; the plan duplicated ledger claim identity; the Rust removal fork ignored public downstream consumers; Java was absent from cross-file resolution; and the multi-root public API was undecided. All six corrections were incorporated before the next review round.
- Round 1 security: `FIX-FIRST` — self-asserted identity could suppress overlaps; keyless digests were described too strongly; strict output allowed arbitrary external writes; MCP anchor confinement could widen; ignored-file coverage was unstated; manifest reads had path races; and the writer census missed `replace_with_retry`. All seven corrections were incorporated.
- Round 1 TDD/evaluation: `FIX-FIRST` — tracker CI could not know live GitHub state; #859 lacked the actual historical red arm; Rust faults were nondeterministic; native commands missed the native CI census; WARN was unpinned; boundaries/concurrency were ambiguous; workspace exit codes conflicted; language red arms could pass through regex; Task 11 was underspecified; workspace MCP registration was incomplete; and final dogfood rows were not exhaustive. All eleven corrections were incorporated.
- Round 2 architecture: `FIX-FIRST` — the dirty manifest did not bind the staged index or enumerate nested untracked files, and the workspace partial/error schema contradicted itself across service, CLI, and MCP. The plan now records content-addressed stage-0 index identity, fails closed on unmerged stages, uses `--untracked-files=all`, and defines one exact shared envelope.
- Round 2 security: `FIX-FIRST` — the reclaimable lease could not fence a resumed stale holder; Windows no-follow hashing was optional; receipt trust disclosures were outside the signed component; and the writer ratchet omitted direct destination sinks. The plan now requires a crash-released OS fence on every claims mutation, mandatory platform-safe opened-handle hashing or `INCOMPLETE`, signed component-local disclosures, and an enumerated sink census.
- Round 3 architecture: `FIX-FIRST` — Task 8 omitted the concrete fence/helper files and did not distinguish a stable fence inode from reclaimable lease metadata; Task 10A omitted Java's live registry seam in `repo_map.py`. The plan now names the claims-only stable fence artifact, every implementation/test file and cross-process proof, and Java's registry edit explicitly.
- Round 3 architecture re-review: `APPROVE` — the index manifest, shared workspace envelope, stable claims-only fence, and Java registry dependency were found coherent with no remaining architecture blocker.
- Round 3 security: `FIX-FIRST` — executable-bit and index-flag mutations could evade the Git oracle; duplicate JSON keys could create cross-parser trust ambiguity; standard-library sink aliases could evade the writer census; and MCP field injection made the 8 MiB boundary contradictory. The plan now binds worktree mode/index flags, rejects duplicate keys at all depths, resolves every enumerated sink alias against an independent inventory, and measures final transport bytes after injection.
- Round 3 TDD/evaluation: `FIX-FIRST` — edit schemas and reason precedence were not exact; later tests could hide behind import failures; Git fixtures/copy semantics and baseline caps were ambiguous; Windows parent junctions could evade leaf tests; the fence primitive/timing/RMW API lacked an executable contract; signed-disclosure tests could prove only cryptography; the writer red arm was circular; and cross-transport equality/dogfood was incomplete. Exact schemas, behaviorless-shell sequencing, real-Git/platform tests, one baseline cap, callback RMW fencing, semantic signed/keyless tests, mutation controls, and individual final-artifact rows are now specified.
- Round 4 security re-review: `APPROVE` — all prior Git-oracle, duplicate-key, writer-alias, MCP-cap, reparse, fence, malformed-input, and trust-disclosure blockers were closed under the documented cooperative-agent threat model.
- Round 5 architecture: `FIX-FIRST` — exact Git IDs excluded SHA-256 repositories; unconditional callback publication broke legacy no-op release; and overlap/ticket reason mappings conflicted. The schema now carries object format with 40/64-hex consistency, claims callbacks return explicit `WRITE`/`NO_WRITE`, and every reason maps exhaustively to verification verdict, ticket status, and exit.
- Round 4 TDD/evaluation: `FIX-FIRST` — untracked index flags and prepare/receipt-component schemas remained inexact; global pytest `-x` could mask adapter/language reds; zero-root CLI wording contradicted its JSON contract; and source-only writer tests were misattributed to wheel dogfood. These now have exact nullability/types, node-ID red sequencing, parser-optional/service-required roots, and separately labeled merged-source CI receipts.
- Round 5 TDD/evaluation re-review: `APPROVE` — exact schemas/object formats, behavior-specific red arms, real-Git and mandatory Windows fixtures, callback-fence semantics, semantic receipt tests, transport equality, and evidence attribution were clean.
- Round 5 security final re-review: `APPROVE` — no remaining security regression; final proof correctly separates wheel-visible behavior from exact-merged-SHA internal contracts.
- Round 6 architecture: `FIX-FIRST` — public edit command argv/scope/descriptor inputs were not frozen, prepare-to-snapshot policy was unspecified, and the exact evidence component had no production ingress. The plan now fixes Python/native argv, accepts only owned exact baselines, normalizes scope and caller-supplied structured validation deterministically, defines every prepare projection field, and adds bounded `tg evidence emit --edit-verification` coverage through both front doors.
- Round 6 security: `FIX-FIRST` — verification results could be replayed across repositories/revisions, file-backed trust inputs did not all share the safe opened-handle reader, and result-size accounting excluded the final transport. The plan now binds repository/revision/dirty identity, routes every trust input through one confined reader, and caps exact final-wire bytes.
- Round 6 TDD/evaluation: `FIX-FIRST` — compiled-native help/argv nodes were outside the native CI census, evidence behavior REDs could be masked by an unknown option, validation descriptors lacked executable boundary rules, and `partial_other` projection was ambiguous. The plan now puts every native node in `test_native_*.py`, sequences behaviorless shells before one RED per behavior, freezes descriptor identity/type/path/collision bounds, and pins mixed-source projection semantics.
- Round 7 architecture: `FIX-FIRST` — verifier identity inputs and strict baseline publication were not fully frozen. The plan now requires an external baseline SHA-256 anchor, structured target/blast comparison, exact validation inputs, and handle-relative create-if-absent publication with no path fallback.
- Round 7 security: `FIX-FIRST` — handle-relative no-clobber proof and evidence-subject provenance were incomplete. The plan now mandates Unix `openat`/`linkat` and Windows root-handle-relative publication, single-capture receipt subjects, producer-version preservation, and binding of the exact verification-result digest.
- Round 8 architecture: `FIX-FIRST` — the file-only verify-to-evidence handoff changed the dirty digest and made a normal round trip reject itself. The plan now freezes bounded `--edit-verification -` stdin ingestion and proves signed/keyless round trips with separately checked producer and consumer exits.
- Round 9 security: `FIX-FIRST` — a 0→0-only round trip could claim to prove pipe-status safety without exercising a failing producer. The plan now pins 0→0, 1→0, valid 2→0, and invalid-input consumer-2 arms and explicitly disallows unchecked shell-pipeline status masking.
- Round 10 post-approval live deep-dive: `FIX-FIRST` — Task 2's global “shipping entry” parser had no canonical grammar, #90 was incorrectly treated as wholly shipped, required contract/comment/#859-receipt files were omitted, the CEO set was not frozen, and a version-token-only handoff refresh could preserve obsolete content. Program 0 and Task 2 now define one canonical live-status index, exact source decisions and file scope, mixed #90 handling, unique CEO/demand ownership, and substantive handoff reconciliation before implementation.
- Round 11 backend/writer deep-dive: `FIX-FIRST` — the writer population had three live violations plus generated-source/scope-resolution blind spots; Rust hardening overstated direct-file failures and placed private fault seams in an external test; and Python `CPUBackend` retained two unsafe `TypeError` compatibility retries already retired in its `RustCoreBackend` twin. Programs 1 and Tasks 3/5 now pin the exact populations, genuine production REDs, private Rust seam placement, unchanged public semantics, and one-call Python adapter contract.
- Round 12 amendment thinktank: `FIX-FIRST` — the tracker lacked a parseable version and complete demand population, #89/#859 transitions were ambiguous, the writer plan remained vulnerable to parent swaps and no-clobber races, generated helpers/sanctions were not closed-world, the Rust public type was not mechanically pinned, and fixed-string fallback could still mask native `TypeError`. The design and Tasks 2/3/5/15 now freeze those contracts and require independent per-node RED/green receipts before the next review.
