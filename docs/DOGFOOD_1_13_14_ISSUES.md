# v1.13.14 Dogfood Issue Ledger

Date: 2026-05-25

This ledger consolidates the supplied v1.13.14 dogfood reports. It separates
confirmed release-blocking contracts from UX notes and longer roadmap items.

## Research Anchors

- Ripgrep ignore semantics: `--no-ignore` disables standard ignore-file
  filtering, while hidden-file filtering remains controlled separately by
  `--hidden`; `-u/-uu/-uuu` are layered shortcuts. Sources:
  <https://github.com/BurntSushi/ripgrep/blob/14.1.0/doc/rg.1.txt.tpl> and
  <https://iepathos.github.io/ripgrep/common-options/file-filtering/>.
- Ripgrep JSON compatibility is JSON Lines event output. Tensor-grep aggregate
  JSON is a different contract and must not be conflated with
  `--format rg --json`. Source:
  <https://iepathos.github.io/ripgrep/output-formats/>.
- LSP proof requires completed provider requests, not provider availability
  alone. LSP initialization and document sync are explicit lifecycle/request
  contracts. Source:
  <https://github.com/Microsoft/language-server-protocol/blob/gh-pages/_specifications/lsp/3.18/specification.md>.
- MCP initializes first, then tools are listed/called through explicit tool
  schemas. Tool metadata calls should be deterministic and bounded. Sources:
  <https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle>
  and <https://modelcontextprotocol.io/specification/2025-11-25/server/tools>.
- Typer/Click help text is derived from command docstrings or explicit `help=`;
  passthrough/native front doors need equivalent command descriptions. Sources:
  <https://typer.tiangolo.com/tutorial/subcommands/name-and-help/> and
  <https://typer.tiangolo.com/tutorial/options/help/>.
- Pyright environment and command-line behavior depends on configured Python
  paths and import resolution. SRE/ABI mismatch stderr should be surfaced as
  provider health noise, not hidden behind a proof marker. Sources:
  <https://github.com/microsoft/pyright/blob/main/docs/command-line.md> and
  <https://github.com/microsoft/pyright/blob/main/docs/import-resolution.md>.

## Must Fix for v1.13.15

1. Search/ripgrep parity edges
   - Reports: one dogfood run counted zero stdout for
     `tg search --no-ignore -l "tensor-grep"`, while `rg --no-ignore` found
     thousands. A subagent reproduced that the current native command exits 2
     with the broad generated-root guard on this repo, so the user-visible bug
     is ambiguous: the command is not a silent no-match, but the guard message
     is easy to miss if a harness only counts stdout.
   - Confirmed edge: native `--format rg` treats an implicit no-path search as
     explicit `.`, so it prints `.\AGENTS.md` where rg and the Python front door
     print `AGENTS.md`.
   - Confirmed edge: MCP `tg_search` counts ripgrep JSON context rows as matches
     through `RipgrepBackend`, which explains MCP vs CLI count drift for
     context-bearing searches.
   - Recommendation: preserve implicit-path state in native rg passthrough,
     count only `match` events as matches in `RipgrepBackend`, and improve the
     generated-root refusal text so harnesses and humans do not interpret an
     exit-2 guard as zero-result parity.

2. Hybrid/LSP proof consistency
   - Reports: `refs` / `callers` / `blast-radius` can return top-level
     `lsp_proof=true` while final rows or provider status report no provider
     response, and pyright stderr_tail can include repeated SRE mismatch traces.
   - Root cause: proof counts are taken from intermediate external rows before
     final hybrid merge/dedupe; normal successful navigation rows do not always
     update provider status.
   - Recommendation: prefer marker-backed LSP rows during hybrid merge, compute
     top-level proof from final emitted rows, and mark provider response only
     after usable navigation responses.

3. Map/session cold scan bounds and exclusions
   - Reports: `tg map . --json` emitted 91-115 MB and took several minutes on
     Windows; one report observed `.venv`-class scope leakage. `tg session open`
     also had multi-minute cold setup.
   - Root cause: `tg map` and `tg session open` default to uncapped
     `max_repo_files=None`; repo-map exclusions do not cover local generated
     names such as `.venv_cuda`, `bench_data`, `gpu_bench_data`, `.tmp_*`,
     `many_files`, `group2_many_files`, and `site`.
   - Recommendation: default agent-facing map/session entry points to the
     existing 512-file budget unless callers opt into a larger cap, widen
     generated-directory exclusions, and surface `scan_limit`/truncation.

4. Edit-plan headline fields
   - Reports: `tg edit-plan ... --json` returns useful
     `candidate_edit_targets`, `edit_plan_seed`, and `navigation_pack`, but
     top-level `plan`, `primary_target`, and `edit_order` are null or empty.
   - Recommendation: add additive top-level aliases derived from existing
     nested data; do not remove the nested schema.

5. Blast-radius headline fields
   - Reports: `blast_radius_score=null` and `affected_files=[]` while
     `files`, `file_matches`, and `caller_tree` are populated.
   - Recommendation: add `affected_files` as a compatibility alias over ranked
     radius files and compute a simple bounded score from existing file/caller
     evidence. Keep the formula documented and deterministic.

6. Daemon status observability
   - Reports: `tg session daemon status --json` lacks
     `response_cache_size_bytes`, max bytes, hits, misses, and skip counters,
     even though daemon internals already track them.
   - Recommendation: merge live daemon `stats` into `status` when available;
     keep current behavior if stats cannot be fetched.

7. Help/deprecation accuracy
   - Reports: native root help shows blank descriptions for passthrough
     commands; examples still advertise `--query` and `--symbol` while commands
     warn that those forms are deprecated.
   - Recommendation: add native command doc comments/help strings, update root
     examples to positional forms, hide deprecated Typer options from normal
     help while preserving parsing/warnings, and document PowerShell single
     quotes for AST patterns.

## Should Fix if Low Risk

8. Checkpoint undo PATH UX
   - Reports: `tg checkpoint undo <PATH>` is parsed as a checkpoint id and
     fails with a generic "Checkpoint not found"; the supported latest-path form
     is hard to infer.
   - Recommendation: clarify help and error text, and keep `checkpoint undo
     --last PATH` discoverable. A path-first shortcut can be considered if it
     does not collide with checkpoint ids.

9. Parser-backed definition confidence
   - Reports: `defs` rows have `confidence=null`.
   - Recommendation: set confidence for parser-backed/native definitions where
     the code already knows the provenance.

10. MCP capabilities latency
   - Reports conflict: one saw `tg_mcp_capabilities` take more than 30 seconds,
     another saw instant response.
   - Recommendation: avoid expensive probes inside metadata-only tool calls and
     add a timeout-smoke test if the local behavior reproduces.

11. Duplicate launcher clutter
   - Reports: multiple `tg`, `tg.cmd`, `tg.exe`, and `tg.ps1` launchers exist
     across user bin dirs.
   - Recommendation: keep doctor reporting explicit; avoid destructive pruning
     in this PR unless there is a confirmed tensor-grep-owned stale launcher.

## Defer to Roadmap

- Strict `--rg-compat` mode for byte-equivalent rg behavior across every public
  flag combination.
- Published JSON schemas plus fixture validation for agent-facing payloads.
- Persistent daemon query-result cache, implicit daemon routing, fast degraded
  session open, and progress-phase streaming for slow commands.
- Fuzzy AST matching / ignore type annotations / AST pattern-from-file.
- Blast-radius TUI.
- Native "bare metal" grep bypass beyond the current native front door.
- Production GPU acceleration and public GPU promotion claims.
- Full duplicate-launcher repair/pruning workflow.

