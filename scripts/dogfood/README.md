# Post-release Docker dogfood

**Run this after every release confirms on PyPI.** It installs the *published* `tensor-grep` into a
clean container and runs the real `tg` binary across every user-facing feature, asserting no
regression.

## Why

Our unit/integration tests use Typer's `CliRunner`, which invokes the `app` object **directly and
bypasses the real `tg` front door** (`tensor_grep.cli.bootstrap:main_entry`, which forwards plain
text searches to ripgrep). v1.14.0's `tg search --rank` shipped broken in plain-text mode
(`rg: unrecognized flag --rank`) and no test caught it — because none ran the installed binary the way
a customer does. This harness closes that blind spot: a clean install + the real binary + every
feature.

## Run it

```bash
# After e.g. v1.15.1 publishes:
docker build --build-arg TG_VERSION=1.15.1 -f scripts/dogfood/Dockerfile -t tg-dogfood scripts/dogfood
docker run --rm tg-dogfood
```

- **Exit 0** — the shipped artifact installs and every feature works.
- **Exit 1** — a regression; the failing `tg <command>` and its output are printed.

The `RUN tg --version` line in the Dockerfile also fails the *build* early if the wheel didn't resolve
or `tg` isn't on `PATH` (an install/packaging regression).

### Without Docker

The battery is environment-agnostic — point it at any installed `tg`:

```bash
pip install "tensor-grep==<version>"
python scripts/dogfood/dogfood_features.py      # or TG_BIN=/path/to/tg python scripts/dogfood/dogfood_features.py
```

## Coverage & extending

`dogfood_features.py` generates a tiny multi-file fixture (a hub imported by two modules, plus a Rust
file) and exercises: `--version`, plain `search`, **`search --rank` (plain AND `--json`)**, `search
--json`, `orient` (+ `--json` + empty-dir), `map`, and `agent --json`.

**When you ship a new feature, add a `check(...)` line** so the battery grows with the product. The
`search --rank (PLAIN)` check is a permanent regression guard for the v1.14.0/v1.15.1 bug — it asserts
the output never contains `unrecognized flag`.

## Dogfooding the BETA (working tree), not the published wheel

`Dockerfile` above installs the **published** wheel from PyPI. By construction it cannot exercise
unreleased code, and installing a beta onto a developer machine would clobber whatever stable `tg`
that machine relies on. `Dockerfile.source` builds the **current working tree** inside a container
instead, so the host's installed `tg` is never touched.

```bash
# From the REPO ROOT (the build context must contain the source):
docker build -f scripts/dogfood/Dockerfile.source -t tg-dogfood-src .
docker run --rm tg-dogfood-src
```

- **Exit 0** — the working tree builds, installs, and every feature works against the real binary.
- **Exit 1** — a regression; the failing `tg <command>` and its output are printed.

The build itself fails early if `tg` is not on `PATH` or the PyO3 extension did not load, and it
prints `tensor_grep.__file__` so a stale layer cannot masquerade as a fresh build.

First build is slow (release profile + LTO on `rust_core`). Rebuilds are cheap while
`Cargo.toml`/`Cargo.lock` are unchanged.

### Read the exit code UNPIPED

```bash
docker build -f scripts/dogfood/Dockerfile.source -t tg-dogfood-src . > build.log 2>&1
echo "exit=$?"      # <- tg's build status
```

`docker build ... | tail` reports **tail's** exit status, not the build's. A failing build read
through a pipe looks like `exit 0` while producing **no image at all** — verify with
`docker images tg-dogfood-src`, which is the only claim that cannot be faked by a misread pipe.

### Why `.dockerignore` is load-bearing here

Docker does **not** read `.gitignore`, so every gitignored directory is still walked and sent as
build context. Three real failures this caused, each aborting the whole build before a single
layer ran:

| Offender | Error |
|---|---|
| `.pytest_tmp_review_<hex>/` | `error from sender: ... Access is denied` |
| `.tmp_council_<date>/` | `error from sender: ... Access is denied` |
| `rust_core/.venv/bin/python` | `invalid file request` (a venv symlink pointing outside the context) |

Two lessons are encoded in the repo-root `.dockerignore`:

1. **Patterns are anchored at the context root** unless prefixed with `**/`. A bare `.venv/`
   excludes only the top-level one, which is why a nested `rust_core/.venv/` still broke the build.
2. **Exclude the family, not the instance.** The transient directories are named per-run, so
   `.tmp*/` is the entry that survives; naming the two that happened to bite is how this recurs.

It also keeps `target/` (Rust build artifacts, easily gigabytes) and `.git/` out of the context.
