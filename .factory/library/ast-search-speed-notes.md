# AST search speed notes

- `parallel-walk-and-prefilter` now uses `ignore::WalkParallel`, walker-level type filters, fixed-string prefiltering, binary skipping, BOM handling, and deterministic result sorting in `rust_core/src/backend_ast.rs`.
- On the Windows 16-core mission host, `python benchmarks/run_ast_benchmarks.py --output artifacts/bench_ast_m3.json` still reported `tg_median=0.403s`, `sg_median=0.137s`, `ratio=2.937`.
- The benchmark corpus (`benchmarks/gen_corpus.py`) is a single flat directory of 1000 Python files where every line matches `def $F($$$ARGS): return $EXPR`, so the new prefilter provides no skip benefit there.
- Likely next investigation: compare `WalkParallel` in-thread processing against a hybrid `WalkParallel` discovery + dedicated worker pool on flat corpora, because the functional changes landed but the benchmark ratio did not improve on this corpus.
