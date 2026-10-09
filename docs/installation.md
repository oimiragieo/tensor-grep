# Installation

As of 2026-10-06, the live install channels described here are PyPI, GitHub Release binaries, and the install scripts that download those binaries. npm, Homebrew, and Winget manifests are present in the repository, but those packages are not published to their registries. Check the [support matrix](SUPPORT_MATRIX.md) for tested platforms and versions.

## Recommended channel by use case

| Use case | Install method |
|---|---|
| Use `tg` from a managed native command with an isolated Python environment | Install script |
| Use Python directly or pin the package in a Python environment | PyPI with `pip` or `uv` |
| Manage a binary yourself | GitHub Release asset |

The managed install script is the simplest choice for most individual users. It installs a native CPU front door where a matching release asset is available and manages Python support files separately. The default published native asset profile is CPU. GPU assets require a separate gated build profile and are not the default install.

## Install with the managed script

**Windows PowerShell:**

```powershell
irm https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.ps1 | iex
```

**Linux or macOS Bash:**

```bash
curl -LsSf https://raw.githubusercontent.com/oimiragieo/tensor-grep/main/scripts/install.sh | bash
```

The script installs `tg` and prints its version. These commands fetch an installer from the mutable `main` branch. For a pinned install, use an exact PyPI version or download a GitHub Release asset and verify its checksum before running it.

## Install the Python package

Install from PyPI with either `pip` or `uv`:

```text
python -m pip install tensor-grep
```

Or run the package with `uvx`:

```text
uvx tensor-grep --version
```

To pin an exact version, replace `X.Y.Z` with the version you selected:

```text
uvx --from 'tensor-grep==X.Y.Z' tg --version
```

Basic Python installation does not require a GPU, dense model, CUDA, or an API key. Optional dense search dependencies belong to the [experimental feature setup](EXPERIMENTAL.md).

Python users can add `tensor-grep[scan]` for structural patterns and rules that use
the ast-grep command, or `tensor-grep[ast]` for the optional tree-sitter language
parsers used by code analysis. These are separate from GPU and dense-model dependencies.
For example:

```text
python -m pip install "tensor-grep[ast,scan]"
```

The Python package path also supports the `tg update` / `tg upgrade` workflow. If you need those commands, install with `pip` or `uv` rather than a directly downloaded binary.

## Install a release binary directly

Download the CPU asset for your system from [GitHub Releases](https://github.com/oimiragieo/tensor-grep/releases):

- Windows x64: `tg-windows-amd64-cpu.exe`
- Linux x64: `tg-linux-amd64-cpu`
- macOS x64: `tg-macos-amd64-cpu`

Place it in a directory on your `PATH`, then verify it against that release's `CHECKSUMS.txt`. This route does not install the managed Python sidecar. Some Python-backed commands therefore need a Python package installation or an explicitly configured sidecar environment.

## Verify the installation

Open a new terminal and run:

```text
tg --version
tg --help
tg search -F 'ERROR' .
```

The final command searches the current directory for the literal word `ERROR`. It can return no matches if your files do not contain that word; that is a valid search result.

If `tg` is not found, open a new terminal so it reads updated `PATH` settings. On Windows, check which command will run:

```powershell
where.exe tg
Get-Command tg -All
tg doctor --json
```

If more than one launcher appears, the first one on `PATH` wins. Follow the install script's diagnostics before removing old launchers.

For a source checkout, `tg doctor PATH --json` inspects that checkout's `.venv` metadata without executing its interpreter. A `stale_editable` diagnosis includes the refresh command `uv run --refresh-package tensor-grep tg --version`; run it from that checkout, then use its environment interpreter for checkout scripts. Unrelated or ambiguous environments are reported separately.

Run `tg dogfood --features --json` for packaged feature checks on disposable fixtures. `tg dogfood --all --json` combines these with readiness checks; the default remains readiness. Set `TG_BIN` to choose an executable explicitly, and `TG_SIDECAR_PYTHON` when testing a specific native sidecar. The report records tested artifact identity and refuses version mismatches before running feature checks.

## What the install provides

Managed native installs use the native binary as the command front door and may invoke a managed Python sidecar for Python-backed commands. PyPI installs begin through the Python command. These are different entry points; [architecture](architecture.md) describes the split and [routing policy](routing_policy.md) describes text-search engine selection.

The release's normal native asset profile is CPU. GPU execution is experimental and requires the appropriate opt-in build and compatible hardware and drivers. A platform-specific CI build or manifest does not mean that the install will use a GPU. See [experimental features](EXPERIMENTAL.md) and the [GPU troubleshooting runbook](runbooks/gpu-troubleshooting.md).

## Channels not yet published

These source manifests are not live registry packages:

- npm/`npx`: the repository contains an npm wrapper, but `tensor-grep` is not published to the npm registry.
- Homebrew: the formula is present in the repository, but no published tap is available.
- Winget: a manifest is present in the repository, but it has not been submitted to the public Winget package index.

Use PyPI, GitHub Releases, or the install scripts until those channels are published. The existence of a manifest does not make its registry install command available.

## Maintainer publishing details

Release maintainers can use [package manager publishing](package_manager_publish.md) for manifest validation and release steps. See the [release checklist](RELEASE_CHECKLIST.md) for the broader release process. Users installing `tg` do not need to follow those procedures.
