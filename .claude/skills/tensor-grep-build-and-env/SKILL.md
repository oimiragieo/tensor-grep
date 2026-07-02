---
name: tensor-grep-build-and-env
description: Use when setting up the tensor-grep dev environment from a fresh clone, rebuilding the Rust/PyO3 extension or standalone `tg` binary after touching `rust_core/`, or debugging a build/toolchain problem — uv install, `maturin develop`, `cargo build`, the pinned 1.96.0 Rust toolchain, Python >=3.11 floor, a "hanging" cargo build, cargo/rustc missing from PATH, ruff CRLF false-alarms, or a dependency upper-cap silently downgrading tensor-grep on a newer Python. Gives exact copy-paste setup commands and the traps that have each cost a real cycle.
---

# tensor-grep: Build & Environment Runbook

Recreate a working tensor-grep dev environment from nothing, and rebuild it correctly after
touching Rust. Every command below is verified against `pyproject.toml`, `rust_core/Cargo.toml`,
`rust_core/rust-toolchain.toml`, `CONTRIBUTING.md`, `AGENTS.md`, and `.github/workflows/ci.yml` as
of **2026-07-02, v1.17.25**. Re-verify anything version-shaped before trusting it long-term — see
"Provenance and maintenance" at the bottom.

## When to use this skill

- Fresh clone → getting `tg` runnable locally.
- You touched `rust_core/src/**` and need to rebuild the PyO3 extension and/or the standalone binary.
- A build "hangs", `cargo`/`rustc` isn't found, `ruff format --check` flags files you didn't edit,
  or a fresh install resolved to a suspiciously old version.
- You're about to run the required local validation gate before a push/PR.

## When NOT to use this skill (go to the sibling instead)

| If you're actually trying to... | Use |
|---|---|
| Add a new `tg` command or search flag (registration sites) | `tensor-grep-architecture-contract` (why) + `tensor-grep-change-control` (the gates) |
| Interpret a full pytest/mypy/ruff QA run or CI failure in depth | `tensor-grep-validation-and-qa` |
| Debug wrong runtime *behavior* in code that already builds | `tensor-grep-debugging-playbook` |
| Understand `tg doctor` / `tg dogfood` / launcher routing | `tensor-grep-diagnostics-and-tooling` |
| Cut or diagnose a release, semantic-release, push-race | `tensor-grep-release-and-positioning` |
| Look up an env var / feature flag's default and meaning | `tensor-grep-config-and-flags` |
| Read about a settled historical incident before re-litigating it | `tensor-grep-failure-archaeology` |
| Run or interpret a benchmark | `tensor-grep-benchmark-and-proof-toolkit` |

## Jargon glossary (defined once, used throughout)

- **uv** — fast Python package/venv manager (Astral). Replaces `pip` + `venv` + `pip-tools` in one binary.
- **maturin** — a PEP 517 build backend *and* CLI that compiles a Rust crate into a Python extension
  module and can install it into the active venv in one step (`maturin develop`).
- **PyO3** — the Rust crate that generates CPython C-API glue so Rust functions are callable from Python.
- **abi3 / stable ABI** — a subset of the CPython C-API guaranteed stable across minor versions; a wheel
  built with `abi3-py311` loads unmodified on any CPython >=3.11 without a per-version rebuild.
- **cargo / rustc** — Rust's build tool and compiler, respectively.
- **rustup** — the Rust toolchain version manager; reads `rust-toolchain.toml` and auto-selects the
  pinned channel for any `cargo`/`rustc` invocation inside the crate.
- **LTO (link-time optimization)** — a release-build pass that optimizes across crate boundaries. Slow
  to compile, faster at runtime. The reason `--release` Rust builds here take minutes.
- **PEP 517** — the standard that lets `pip`/`uv` delegate "how do I build this package" to a
  build-backend package (here, `maturin`) instead of assuming plain `setuptools`.

## Repo layout: two build systems, one package

- **Python package**: `src/tensor_grep/` — driven by `pyproject.toml`. Entry point:
  `tg = "tensor_grep.cli.bootstrap:main_entry"` (`pyproject.toml:382`).
- **Rust workspace**: `rust_core/` — driven by `rust_core/Cargo.toml` (crate `tensor_grep_rs`). It
  builds **two separate targets** from the same source:
  1. a `cdylib` PyO3 extension module, importable as `tensor_grep.rust_core`
     (`module-name = "tensor_grep.rust_core"`, `pyproject.toml:8`) — this is what the Python CLI calls
     into for accelerated search.
  2. two standalone binaries declared as `[[bin]]` targets (`rust_core/Cargo.toml:53-59`): `tg` and
     `tg-search-fast` — the "native front door" shipped as a release asset and picked up by launcher
     resolution ahead of the Python path.

Editing `rust_core/src/**` requires an explicit rebuild of whichever target you're testing — an
editable Python install does **not** watch and recompile Rust for you. See the rebuild table below.

## Prerequisites

| Tool | Version pin | Pinned where | Why it matters |
|---|---|---|---|
| Python | `>=3.11` | `pyproject.toml:325` | floor for the PyO3 `abi3-py311` stable ABI |
| uv | `0.11.25` | every `pip install uv==...` step in `.github/workflows/ci.yml` | exact CI parity |
| maturin | `>=1.5,<2.0` | `pyproject.toml:2` `[build-system].requires` | PEP 517 backend that compiles `rust_core/` |
| Rust toolchain | `1.96.0` | `rust_core/rust-toolchain.toml` | reproducible, supply-chain-safe builds (audit MEDIUM finding) |
| rustfmt, clippy | bundled with 1.96.0 | `rust_core/rust-toolchain.toml` `components` | CI's "Check Rust Formatting" + clippy jobs need them; a channel-only pin on a minimal-profile runner would drop them |
| ruff | `==0.15.11` | `pyproject.toml` `[project.optional-dependencies].dev` | lint + format gate |
| mypy | `>=1.11` | same | typecheck gate, `strict = true` (`pyproject.toml:112`) |

## Zero-to-running setup (copy-paste)

### 1. Clone and install uv

```bash
git clone https://github.com/oimiragieo/tensor-grep.git
cd tensor-grep
python -m pip install uv==0.11.25   # exact version CI pins — do this, not "latest"
```

If you'd rather install `uv` itself via the official Astral installer script (`curl ... | sh`),
that invocation is a remote-script-exec pattern — read `supply-chain-hardening` first and prefer a
package manager (`pipx install uv`, Homebrew, winget) or the pinned `pip install uv==0.11.25` above.
This repo's own installers were hardened against exactly this pattern (#312, checksum-gating the
Unix uv bootstrap in the release `build_command`).

### 2. Install/select the Rust toolchain

```bash
rustup default 1.96.0
rustup component add rustfmt clippy
```

If `rustup` isn't installed yet, this is the exact bootstrap the release pipeline itself uses
(`pyproject.toml:133`, semantic-release `build_command`):

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
. "$HOME/.cargo/env"
rustup default 1.96.0
```

Once `rust_core/rust-toolchain.toml` is on disk (it already is, checked into the repo), any `cargo`/
`rustc` invocation from inside `rust_core/` auto-selects channel `1.96.0` — you rarely need to think
about the pin again after this step.

### 3. Create the venv and install the package (this also builds the Rust extension)

```bash
uv venv --python 3.12          # any >=3.11 works; CI's test matrix is 3.11 and 3.12
uv pip install -e ".[dev,ast]" # dev = pytest/ruff/mypy/hypothesis/...; ast = tree-sitter grammars
```

This single command invokes `maturin` as the PEP 517 build backend and compiles `rust_core/` into
the `tensor_grep.rust_core` extension automatically as part of the editable install — you do **not**
need a separate `maturin develop` call for the first install.

### 4. Verify

```bash
uv run tg --version
uv run python -c "import tensor_grep.rust_core; print('rust_core OK')"
uv run pytest tests/unit/test_rust_core.py -q
```

`tests/unit/test_rust_core.py` asserts the extension actually imports (`find_spec`) and that
`RustCoreBackend().search(...)` returns real results — a stronger check than "the install didn't
error," which can pass even when the extension silently failed to build (see Trap 8).

## Rebuilding after a Rust-side change

| You changed... | Rebuild with | Typical time |
|---|---|---|
| `rust_core/src/**` for the extension consumed by the Python CLI | `uv run maturin develop` (installs the `maturin` CLI transparently via `uv run`/`uvx`; or `pip install "maturin>=1.5,<2.0"` once, matching the build-system pin) | **~15 s** (dev/debug profile) |
| Same, but you need the release-optimized extension | `uv run maturin develop --release` | minutes (LTO — see Trap 2) |
| the standalone `tg` / `tg-search-fast` binaries | `cargo build --manifest-path rust_core/Cargo.toml` (debug) or add `--release` | debug: seconds; release: minutes (LTO) |
| just want fast Rust-only feedback (no Python glue) | `cargo test`, `cargo fmt -- --check`, `cargo clippy -- -D warnings` — all run from `rust_core/` | seconds |

Re-run the two verify commands from step 4 after **any** Rust rebuild. Do not assume the change took
effect — see Trap 6 (stale in-tree binaries) and Trap 8 (mocked tests hiding a dead bridge).

## Local validation (run before every push)

Exact commands from `CONTRIBUTING.md` "Local Validation" + `AGENTS.md` "Required Local Validation":

```bash
uv run ruff check .
uv run ruff format --check --preview .
uv run mypy src/tensor_grep
uv run pytest -q
```

Rust equivalents of CI's `static-analysis` job (`.github/workflows/ci.yml`, job `static-analysis`):

```bash
cd rust_core
cargo fmt -- --check
cargo clippy -- -D warnings
cargo test --no-default-features
cd ..
```

Registration-completeness gate (blocking since v1.17.1 — catches "added a command/flag, missed a
front-door site"; string/comment-aware, so a `#`-commented entry won't false-pass):

```bash
PYTHONPATH=src python -m tensor_grep.core.registration_check .tg-registration.toml
```

Fast pre-push agent-surface gate (complements, does not replace, the full gate above):

```bash
python scripts/agent_readiness.py --output artifacts/agent_readiness.json
tg dogfood --output artifacts/dogfood_readiness.json
```

**Test corpus size** (2026-07-02): `tests/` has 155 unit test files + 15 e2e test files + 9
integration test files (`pyproject.toml` sets `testpaths = ["tests"]`, so a bare `uv run pytest -q`
covers all three). `uv run pytest -q` "can take substantially longer than 70-90 seconds on
[a Windows] machine when the full JS/TS and e2e surface is hot" — use a timeout of at least 120s for
narrow suites and considerably more for the full run when automating this (`AGENTS.md:306`).

## Known traps (each one has cost a real cycle — read before debugging blind)

### 1. `cargo`/`rustc` "missing" from PATH

**Symptom:** `cargo: command not found` / `rustc: command not found` despite Rust being installed.
**Cause:** rustup's install directory (`~/.cargo/bin`) isn't on this shell's `PATH`.
**Fix:** prepend `~/.cargo/bin` to `PATH`, or call the binaries by full path. (This project's own dev
box hits this concretely at `C:/Users/oimir/.cargo/bin/cargo.exe` — that exact path is a
machine-specific example, not a portable claim; the general fix is "put *your* `~/.cargo/bin` on
PATH.") Source: `AGENTS.md:546`.

### 2. A "hanging" Rust build is not hung — it's LTO

**Symptom:** `cargo build --release` or `maturin develop --release` appears to sit for minutes with
no output.
**Cause:** `rust_core/Cargo.toml:234-235` sets `[profile.release] lto = true` — link-time optimization
is slow to run but does complete.
**Fix:** don't kill it; let it finish. Use plain `maturin develop` (no `--release`, ~15s) for the fast
inner dev loop, and reserve `--release` builds for when you actually need release-profile
performance or are reproducing a release artifact. Source: `AGENTS.md:546`.

### 3. Windows CRLF makes `ruff format --check` false-alarm

**Symptom:** a bare local `ruff format --check .` flags files you never touched.
**Cause:** `.gitattributes` pins `*.py`/`*.rs` to `eol=lf`; a Windows working tree can smudge lines to
CRLF even though the committed blob is LF, and CI's Linux runner enforces LF.
**Fix:** run `ruff format --preview .` (which normalizes line endings per `pyproject.toml:87`
`line-ending = "lf"`) before committing, not just `--check`. Audit actual on-disk endings with
`git ls-files --eol` — `git show`/`git cat-file -p` smudge output and can report false CR.
Source: `CONTRIBUTING.md:24`, `AGENTS.md:552`.

### 4. `ruff format` WITHOUT `--preview` is an active revert

CI runs an asymmetric split: `ruff format --check --preview .` for the format gate but bare
`ruff check .` (no `--preview`) for lint. Running a bare `ruff format` (no `--preview`) locally
**rewrites preview-style lines back to non-preview style on disk** — the next CI
`ruff format --check --preview` then fails on lines you didn't intend to touch, even though your
local lint was clean.
**Rule:** always pass `--preview` to `ruff format`; never pass `--preview` to `ruff check` (preview
lint rules like RUF056 produce false failures that don't match CI). Source: `CONTRIBUTING.md:22`,
`AGENTS.md:304`.

### 5. A dependency upper-cap can silently downgrade the whole install

**Symptom:** a fresh install on a newer Python (e.g. 3.14) resolves to a stale `tensor-grep` version
with no error message at all.
**Cause:** an upper-bound pin (historically `typer<0.25`) had no release compatible with the newer
Python; `pip`/`uv` then silently resolve the **entire package** down to the newest version whose full
dependency graph is satisfiable on that interpreter. `requires-python>=3.11` has no upper bound, so it
can't catch this.
**Fix:** when a fresh Python yields a suspiciously old `tg --version`, suspect a transitive
dependency cap (`typer`/`click`/`pydantic` today — `pyproject.toml:333` currently pins
`typer>=0.12,<0.26`), not `requires-python`. Fixed for the `typer` case in #310; see the
`tensor-grep-dep-cap-silent-downgrade-2026-06-30` memory note for the full incident.

### 6. Stale in-tree native binaries shadow your rebuild

**Symptom:** you rebuilt `rust_core/`, but `tg`'s behavior didn't change.
**Cause:** a leftover `rust_core/target/debug/tg.exe` or `rust_core/target/release/tg.exe` from a
prior build can be resolved by the native-binary launcher path instead of your fresh build.
**Fix:** `uv run tg doctor --json` reports these under `skipped_native_tg_binaries` with
`rust_binary_version_status`. Rebuild explicitly with
`cargo build --manifest-path rust_core/Cargo.toml --release`, or pin `TG_NATIVE_TG_BINARY` to the
exact binary path you intend to exercise. Source: `AGENTS.md:145`.

### 7. Very new CPython + the `abi3-py311` floor (candidate/open)

`rust_core/Cargo.toml:37` pins `pyo3 = { version = "0.29.0", features = ["anyhow", "abi3-py311"] }` —
a stable-ABI build meant to load unmodified on any CPython `>=3.11`. CI sets
`PYO3_USE_ABI3_FORWARD_COMPATIBILITY: "1"` globally (`ci.yml:17`) so PyO3 doesn't refuse to compile
against a CPython release newer than the PyO3 crate itself recognizes as supported. If a local build
fails specifically on a bleeding-edge Python (3.14+) with an "unsupported Python version"-shaped PyO3
error, set the same env var before building: `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1`. **Labeled
candidate/open** — this is inferred from why CI sets the var, not from a confirmed local repro; verify
before treating it as settled.

### 8. Mock-based FFI tests can pass green while the real bridge is dead

Not a build step, but the verification step every Rust-side build change needs. A prior PyO3 bridge
regression shipped with every *mocked* Python-side unit test green while the *real* compiled bridge
silently dropped every forwarded flag and fell back to the Python engine. After any change touching
`rust_core/src/*` and its Python caller (`src/tensor_grep/backends/rust_backend.py`), run a live call
into the compiled extension — step 4's `import tensor_grep.rust_core` plus
`tests/unit/test_rust_core.py` — not just mock-patched unit tests. See `dogfood-the-shipped-artifact`
and `tensor-grep-debugging-playbook` for the general form of this rule.

## CI parity cheat sheet

What `.github/workflows/ci.yml` actually runs, and the closest local reproduction. Release/publish
jobs are intentionally omitted here — see `tensor-grep-release-and-positioning`.

| CI job | Checks | Local equivalent |
|---|---|---|
| `smoke` | Rust core builds (`cargo build --no-default-features`), package installs, one golden search | `cargo build --manifest-path rust_core/Cargo.toml --no-default-features`; `uv pip install -e .`; `tg search ERROR tests/golden/fixture_data --format rg --sort path` |
| `static-analysis` | `cargo fmt -- --check`, `cargo clippy -- -D warnings`, `ruff check .`, `ruff format --check --preview .`, `mypy src/tensor_grep`, registration-completeness | see "Local validation" above |
| `test-python` (3 OS × py3.11/3.12) | `uv run pytest tests -v --tb=short` | `uv run pytest -q` |
| `test-rust-core` (3 OS × stable/nightly) | `cargo test --verbose --no-default-features` | `cargo test --manifest-path rust_core/Cargo.toml --no-default-features` |
| `search-golden-parity` (windows) | `cargo test --test test_search_golden` | `cargo test --manifest-path rust_core/Cargo.toml --test test_search_golden` |
| `native-build-smoke` (4 OS) | `cargo build --release --no-default-features` then `tg --version`/`--help`/one search on the built binary | same, run from `rust_core/` |
| `agent-readiness` / `windows-agent-readiness` | `scripts/agent_readiness.py` (13-check contract gate) | `python scripts/agent_readiness.py --output artifacts/agent_readiness.json`; see `tensor-grep-diagnostics-and-tooling` |
| `benchmark-regression` | perf regression gates vs. base revision | see `tensor-grep-benchmark-and-proof-toolkit` — do not eyeball timings without that skill's noise-floor rules |
| `release` and everything after it | semantic-release + PyPI/GitHub/package-manager publish | see `tensor-grep-release-and-positioning` — not locally reproducible, don't try |

## Provenance and maintenance

Volatile facts stated above and how to re-check them if this skill feels stale:

- **Version pins** (Python floor, uv, maturin, Rust toolchain, ruff, mypy, pyo3):
  `grep -nE "requires-python|version|channel" pyproject.toml rust_core/Cargo.toml rust_core/rust-toolchain.toml`
- **uv version CI pins**: `grep -n "uv==" .github/workflows/ci.yml`
- **Test file counts** (155 unit / 15 e2e / 9 integration as of 2026-07-02):
  `find tests/unit tests/e2e tests/integration -name "test_*.py" | wc -l` run per directory, or one
  combined `find tests -name "test_*.py" | wc -l` for the total file count.
  Note this counts *files*, not individual `def test_*` cases — the suite has thousands of the latter.
- **CI job names/order**: read `.github/workflows/ci.yml` directly (`grep -n "^  [a-z][a-z-]*:$" .github/workflows/ci.yml`).
- **LTO / release-profile setting**: `grep -n "profile.release" -A2 rust_core/Cargo.toml`
- **Registration-completeness gate presence**: `ls .tg-registration.toml` and
  `grep -n "registration_check" .github/workflows/ci.yml`
- **Current versions at authoring time**: tensor-grep `v1.17.25`, Rust toolchain `1.96.0`, uv
  `0.11.25`, ruff `0.15.11`, mypy `>=1.11`, pyo3 `0.29.0`, maturin build-system pin `>=1.5,<2.0`,
  Python floor `>=3.11`.
