# Architecture

**What belongs here:** Architectural decisions, patterns, key file roles.

---

## Rust Core (rust_core/src/)
- main.rs: CLI entry, routing decisions, all command handlers
- backend_ast.rs: AST search, rewrite plan/apply/verify, diff generation
- backend_cpu.rs: Native regex/literal search + replace via memmap
- index.rs: Trigram index build/persist/load/query/staleness
- rg_passthrough.rs: Spawn ripgrep for cold text search
- python_sidecar.rs: JSON-over-stdio IPC with Python subprocess
- backend_gpu.rs: PyO3 bridge to Python GPU backends

## JSON Output Shapes (v1 contract)
All outputs must include unified envelope: version (u32), routing_backend (string), routing_reason (string), sidecar_used (bool).

Shapes:
1. Search JSON: envelope + matches[{file, line, text}] + total_matches
2. Index search JSON: envelope + matches[] + total_matches
3. Rewrite plan JSON: envelope + pattern, replacement, lang, total_edits, edits[], rejected_overlaps[]
4. Apply+verify JSON: envelope + plan{} + verification{total_edits, verified, mismatches[]}
5. GPU sidecar JSON: envelope + matches[] (validated/augmented by Rust)

## Routing Decision Tree
- Positional/search without --index: rg passthrough (when available)
- search --index: TrigramIndex
- Warm index auto-routing: TrigramIndex (if warm, not stale, pattern >= 3, no unsupported flags)
- run: AstBackend (100% Rust, no Python)
- --gpu-device-ids: GpuSidecar (Python subprocess)
- mcp/classify/scan/test/new: Python sidecar IPC

## Rewrite Safety
- Atomic writes: write-to-temp + rename
- Stale-file detection: mtime check between plan and apply
- Encoding safety: BOM preservation, CRLF preservation, binary skip, large file guard
