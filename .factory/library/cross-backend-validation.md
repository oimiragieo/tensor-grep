## Cross-backend validation notes

- `tests/integration/test_cross_backend.py` resolves ripgrep from `TG_RG_PATH`, PATH, or extracts `benchmarks/rg.zip` on Windows before exercising the Python `RipgrepBackend` JSON path.
- Python JSON formatter now emits the v1 envelope fields `version=1` and `sidecar_used=false` so the `RipgrepBackend` JSON surface matches the native Rust envelope contract.
- Native trigram index routing now fails fast for missing search roots and invalid regex patterns instead of silently returning zero matches.
- On this host, `python benchmarks/run_benchmarks.py` still fails `check_regression.py --baseline auto` by a wide margin (roughly 7% to 62% slower than `benchmarks/baselines/run_benchmarks.windows.json`) even though the cross-backend tests, AST benchmark, harness loop benchmark, Python suite, Rust suite, mypy, ruff, and clippy all passed.
