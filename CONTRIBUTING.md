# Contributing to tensor-grep

To ensure the high standards and stability of the `tensor-grep` pipeline, all code modifications MUST pass the following strict sequence of checks before committing.

## 🛠️ The Developer Checklist

Every time you implement a feature, fix a bug, or modify the codebase, you must verify the following steps:

- [ ] **1. Ruff Linting (Auto-fix)**
      Run `python -m ruff check --fix .` to automatically catch and fix any code-quality or bug-bear violations.
- [ ] **2. Ruff Formatting**
      Run `python -m ruff format .` to strictly adhere to the 100-character line-limit standard.
- [ ] **3. MyPy Strict Type Checking**
      Run `python -m mypy --strict src/tensor_grep` to ensure all type signatures are intact and robust.
- [ ] **4. Rust Core Validation (If applicable)**
      Run `cargo clippy --all-targets --all-features -- -D warnings` and `cargo fmt --check` inside the `rust_core` directory.
- [ ] **5. Pytest Execution**
      Run `python -m pytest tests/` to confirm no core logic regressions occurred.
- [ ] **6. Update `CHANGELOG.md`**
      Add your changes under the `TBD` or the current unreleased version header. Do not skip this!
- [ ] **7. Version Bump (If required)**
      If this is a new release, bump `pyproject.toml` and `npm/package.json`, then commit with `git tag vX.Y.Z`.
