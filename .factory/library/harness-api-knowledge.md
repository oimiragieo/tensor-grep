# Harness API Milestone — Discovered Knowledge

## bench_data and Ignore Rules
- Files in `bench_data/*.log` are ignored by default (repo ignore rules). Use `--no-ignore` when searching bench_data for log files.

## GPU Sidecar Testing Without GPU
- `TG_SIDECAR_SCRIPT` environment variable can point to a mock Python script to exercise the GPU sidecar envelope path when actual GPU backends are unavailable. This is useful for testing the JSON envelope augmentation in `normalize_gpu_sidecar_json()`.

## JSON_OUTPUT_VERSION Coupling
- `JSON_OUTPUT_VERSION` constant is defined in `main.rs` but `backend_ast.rs` hardcodes `version: 1` in `plan_rewrites()` and `plan_and_apply()`. If the version is ever bumped, both locations must be updated. Consider centralizing the constant in a shared module.

## JSON Example Artifacts
- Example JSON artifacts in `docs/examples/` contain machine-specific Windows absolute paths. They serve as shape references, not runnable fixtures. Regeneration requires creating temporary fixture directories and running tg commands.
