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
