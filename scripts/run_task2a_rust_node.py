#!/usr/bin/env python3
"""Behaviorless Round-60 runner stub for Task 2A stable-Rust nodes.

Contract (when GREEN-bound):
  1. Resolve ``--node-id`` from the closed-world manifest (ownership first).
  2. Select the Cargo test executable from ``cargo test --message-format=json``
     compiler-artifact messages (exact package / --bin / --lib / --test target),
     OR invoke the exact Cargo selection itself against ``rust_core/Cargo.toml``.
  3. Execute that exact Cargo-selected path only (no alternate executable).
  4. Run ``--list --format terse`` and require the fully qualified target to
     appear exactly once as an exact ``<name>: test`` line.
  5. Then run ``--exact --include-ignored`` for that exact leaf in an isolated
     process.
  6. Emit NativeCiReceiptV1 only after live Actions binding + census.

Ownership: unknown / unowned node IDs, job mismatches, and selected-binary
mismatches (missing / duplicate / wrong executable) refuse closed before list
or execute. Exact ID→target/binary/kind mapping — no substring / endswith.

RED phase: ``--fixture-executable`` is protocol-only and ALWAYS exits 2 with no
receipt. Arbitrary caller fixture bytes are never executed for a positive.
Empty ``--cargo-json-messages`` stays refusal-only. Never emits an empty Rust
positive. Writes raw list/output/status evidence plus a bounded non-authoritative
observation artifact; still refuses NativeCiReceiptV1.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_LIST_SUFFIX = ": test"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MANIFEST_PATH = "rust_core/Cargo.toml"
_PACKAGE = "tensor_grep_rs"
_OBSERVATION_SCHEMA = "Task2aRawExecutionObservationV1"
_OBSERVATION_VERSION = 1
_MAX_OBSERVATION_STREAM_CHARS = 64 * 1024


def _fq_from_node_id(node_id: str) -> str:
    if node_id.startswith("rust::"):
        return node_id[len("rust::") :]
    return node_id


def fq_names_from_terse_list(stdout: str) -> list[str]:
    """Parse stable libtest ``--list --format terse`` lines ending in ``: test``."""
    names: list[str] = []
    for line in stdout.splitlines():
        if line.endswith(_LIST_SUFFIX):
            names.append(line[: -len(_LIST_SUFFIX)])
    return names


def _load_manifest_node(manifest_path: Path, node_id: str) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    matches = [n for n in (payload.get("nodes") or []) if n.get("id") == node_id]
    if not matches:
        raise LookupError(f"unowned or unknown rust node id: {node_id!r}")
    if len(matches) != 1:
        raise LookupError(f"duplicate manifest node id: {node_id!r}")
    return matches[0]


def _package_id_matches(package_id: str, package: str) -> bool:
    """Exact Cargo package_id matching — no loose substring acceptance."""
    if package_id == package:
        return True
    # Legacy: "name version (source)" — package name must be a whole token.
    if package_id.startswith(f"{package} "):
        return True
    # New path+file / registry form: "...#name@version"
    if f"#{package}@" in package_id:
        return True
    return False


def validate_rust_node_ownership(*, manifest_path: Path, node_id: str) -> dict[str, Any]:
    """Require exact manifest ownership before listing or executing."""
    if not str(node_id).startswith("rust::"):
        raise ValueError(f"rust runner refuses non-rust node id: {node_id!r}")
    node = _load_manifest_node(manifest_path, node_id)
    if not node.get("required_non_skip"):
        raise ValueError(f"node {node_id!r} is not required_non_skip")
    if node.get("workflow") != "ci.yml":
        raise ValueError(f"workflow ownership mismatch: {node.get('workflow')!r}")
    if node.get("runner_class") != "windows-latest":
        raise ValueError(f"runner_class ownership mismatch: {node.get('runner_class')!r}")
    live_job = os.environ.get("GITHUB_JOB")
    if not live_job:
        raise ValueError("GITHUB_JOB environment variable is required for job ownership")
    job = str(node.get("job") or "")
    if job != live_job:
        raise ValueError(f"job ownership mismatch: manifest={job!r} GITHUB_JOB={live_job!r}")
    vector = [str(x) for x in (node.get("command_vector") or [])]
    if not vector or vector[0] != "cargo":
        raise ValueError(f"rust node command_vector must start with cargo: {vector!r}")
    fq = _fq_from_node_id(node_id)
    if "::" not in fq:
        raise ValueError(
            f"rust node id must carry a fully qualified libtest path with '::': got {node_id!r}"
        )
    target = str(node.get("rust_test_target") or "")
    if not target:
        raise ValueError(f"rust_test_target required for {node_id!r}")
    # Exact mapping only: target equals FQ, or (integration) target equals FQ leaf
    # with binary matching the integration crate module prefix.
    binary = str(node.get("binary") or "")
    kind = str(node.get("rust_kind") or "")
    if kind == "test":
        leaf = fq.rsplit("::", 1)[-1]
        if target != leaf or fq != f"{binary}::{leaf}":
            raise ValueError(
                f"exact rust ID/target/binary mismatch: id FQ={fq!r} target={target!r} "
                f"binary={binary!r}"
            )
    elif kind == "lib":
        if target != fq or binary != _PACKAGE:
            raise ValueError(
                f"exact rust lib ID/target/binary mismatch: id FQ={fq!r} target={target!r} "
                f"binary={binary!r}"
            )
    else:
        # Derive kind from vector when rust_kind absent (legacy rows).
        if "--lib" in vector:
            if target != fq:
                raise ValueError(f"exact rust_test_target must equal FQ: {target!r} != {fq!r}")
        elif "--test" in vector:
            leaf = fq.rsplit("::", 1)[-1]
            if target != leaf:
                raise ValueError(f"exact integration leaf mismatch: {target!r} != {leaf!r}")
        else:
            raise ValueError(f"rust node missing --lib/--test in command_vector: {vector!r}")
    return node


def select_cargo_test_executable(
    cargo_json_messages: list[dict[str, Any]],
    *,
    package: str,
    selected_kind: str,
    selected_name: str,
) -> Path:
    """Cargo JSON message selection seam.

    ``selected_kind`` is one of ``bin`` / ``lib`` / ``test``. Requires exactly one
    compiler-artifact with an explicit ``executable`` field matching the selected
    target; missing, duplicate, or wrong-executable populations refuse closed.
    Filenames are never used as executables.
    """
    if selected_kind not in {"bin", "lib", "test"}:
        raise ValueError(f"unknown selected_kind: {selected_kind!r}")
    matches: list[Path] = []
    for msg in cargo_json_messages:
        if msg.get("reason") != "compiler-artifact":
            continue
        package_id = str(msg.get("package_id") or "")
        package_name = str(msg.get("package_name") or "")
        if not (_package_id_matches(package_id, package) or package_name == package):
            continue
        target = msg.get("target") or {}
        kind_list = list(target.get("kind") or [])
        name = str(target.get("name") or "")
        if selected_kind == "bin" and "bin" not in kind_list:
            continue
        if selected_kind == "lib" and not any(
            k in kind_list for k in ("lib", "rlib", "dylib", "cdylib")
        ):
            continue
        if selected_kind == "test" and "test" not in kind_list:
            continue
        if name != selected_name:
            continue
        executable = msg.get("executable")
        if not executable:
            # Do not fall back to filenames — require explicit executable.
            continue
        matches.append(Path(str(executable)))
    if not matches:
        raise LookupError(
            f"cargo selected executable missing for {selected_kind}:{selected_name} "
            f"in package {package}"
        )
    unique = list(dict.fromkeys(matches))
    if len(unique) != 1:
        raise LookupError(
            f"cargo selected executable duplicate for {selected_kind}:{selected_name}: {unique!r}"
        )
    selected = unique[0]
    if selected_kind == "bin" and selected.name not in {selected_name, f"{selected_name}.exe"}:
        raise LookupError(
            f"cargo selected wrong executable: expected name {selected_name!r}, got {selected!r}"
        )
    return selected


def _selection_from_manifest_node(node: dict[str, Any]) -> tuple[str, str, str]:
    """Return (package, selected_kind, selected_name) from a rust manifest node."""
    vector = [str(x) for x in (node.get("command_vector") or [])]
    package = _PACKAGE
    if "-p" in vector:
        package = vector[vector.index("-p") + 1]
        if package != _PACKAGE:
            raise ValueError(f"cargo -p must be {_PACKAGE!r}, got {package!r}")
    kind = str(node.get("rust_kind") or "")
    binary = str(node.get("binary") or "")
    if kind == "bin" or "--bin" in vector:
        name = vector[vector.index("--bin") + 1] if "--bin" in vector else binary
        return package, "bin", name
    if kind == "test" or "--test" in vector:
        name = vector[vector.index("--test") + 1] if "--test" in vector else binary
        return package, "test", name
    if kind == "lib" or "--lib" in vector:
        return package, "lib", binary or package
    raise ValueError(f"cannot derive cargo selection from node vector: {vector!r}")


def _invoke_cargo_json_selection(node: dict[str, Any]) -> Path:
    """Invoke exact Cargo selection against rust_core/Cargo.toml; return executable."""
    vector = [str(x) for x in node["command_vector"]]
    # Build a message-format=json selection argv from the manifest vector, stopping
    # before the libtest filter / -- separator.
    if "--" in vector:
        cargo_prefix = vector[: vector.index("--")]
    else:
        cargo_prefix = list(vector)
    package, kind, name = _selection_from_manifest_node(node)
    select_argv = ["cargo", "test", "--message-format=json", "--no-run"]
    if "--manifest-path" in cargo_prefix:
        mp = cargo_prefix[cargo_prefix.index("--manifest-path") + 1]
        select_argv.extend(["--manifest-path", mp])
    else:
        select_argv.extend(["--manifest-path", _DEFAULT_MANIFEST_PATH])
    select_argv.extend(["-p", package])
    if kind == "lib":
        select_argv.append("--lib")
    elif kind == "test":
        select_argv.extend(["--test", name])
    elif kind == "bin":
        select_argv.extend(["--bin", name])
    proc = subprocess.run(
        select_argv,
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise LookupError(f"cargo selection failed (exit {proc.returncode}): {proc.stderr[-500:]}")
    messages = [
        json.loads(line) for line in proc.stdout.splitlines() if line.strip().startswith("{")
    ]
    return select_cargo_test_executable(
        messages, package=package, selected_kind=kind, selected_name=name
    )


def _write_text(path: Path | None, text: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _clip(text: str) -> str:
    if len(text) <= _MAX_OBSERVATION_STREAM_CHARS:
        return text
    return text[:_MAX_OBSERVATION_STREAM_CHARS] + "\n...<clipped>...\n"


def _emit_observation(
    path: Path | None,
    *,
    node_id: str,
    phase: str,
    argv: list[str],
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    detail: str = "",
    selected_executable: str = "",
) -> None:
    payload = {
        "schema": _OBSERVATION_SCHEMA,
        "version": _OBSERVATION_VERSION,
        "authoritative": False,
        "note": (
            "Non-authoritative raw execution observation only. "
            "Not a NativeCiReceipt; not final proof."
        ),
        "node_id": node_id,
        "phase": phase,
        "argv": argv,
        "exit_code": exit_code,
        "stdout": _clip(stdout),
        "stderr": _clip(stderr),
        "detail": detail,
        "selected_executable": selected_executable,
    }
    _write_json(path, payload)


def classify_rust_node_phase(*, exit_code: int, stdout: str, stderr: str) -> str:
    """A61 / HIGH#2: panic/abort/crash are not behavioral RED.

    Fail-CLOSED: unknown/abnormal exits are ``crash_or_setup``.
    Assertion failures that exercise the contract remain ``executed_refused_receipt``.
    """
    if exit_code < 0:
        return "crash_or_setup"
    blob = f"{stdout}\n{stderr}"
    panic_markers = (
        "panicked at",
        "fatal runtime error",
        "stack backtrace:",
        "SIGSEGV",
        "SIGABRT",
        "SIGBUS",
        "SIGILL",
        "Aborted",
        "Segmentation fault",
        "memory allocation of",
        "has overflowed its stack",
    )
    if any(marker in blob for marker in panic_markers):
        if "panicked at" in blob or "fatal runtime error" in blob:
            return "crash_or_setup"
        if "SIGSEGV" in blob or "SIGABRT" in blob or "SIGBUS" in blob or "SIGILL" in blob:
            return "crash_or_setup"
        if "Aborted" in blob or "Segmentation fault" in blob:
            return "crash_or_setup"
        if "memory allocation of" in blob or "has overflowed its stack" in blob:
            return "crash_or_setup"
        # backtrace alone with no assertion evidence → crash_or_setup
        if "assertion `left == right`" not in blob and "FAILED" not in blob:
            return "crash_or_setup"
    # Behavioral RED only with explicit assertion-failure evidence.
    assertion_markers = (
        "assertion `left == right`",
        "assertion failed",
        "FAILED",
    )
    if exit_code == 0:
        return "executed_refused_receipt"
    if any(m in blob for m in assertion_markers) and "panicked at" not in blob:
        return "executed_refused_receipt"
    # Unknown/abnormal non-zero without assertion evidence → fail closed.
    return "crash_or_setup"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--fixture-executable", type=Path, default=None)
    parser.add_argument("--rust-list-out", type=Path, default=None)
    parser.add_argument("--rust-stdout-out", type=Path, default=None)
    parser.add_argument("--rust-stderr-out", type=Path, default=None)
    parser.add_argument("--rust-status-out", type=Path, default=None)
    parser.add_argument("--argv-out", type=Path, default=None)
    parser.add_argument("--observation-out", type=Path, default=None)
    parser.add_argument("--artifact-source", type=Path, default=None)
    parser.add_argument(
        "--cargo-json-messages",
        type=Path,
        default=None,
        help="Optional cargo test --message-format=json lines for selection RED/GREEN",
    )
    args = parser.parse_args(argv)

    try:
        node = validate_rust_node_ownership(
            manifest_path=args.manifest,
            node_id=args.node_id,
        )
    except (LookupError, ValueError, OSError, json.JSONDecodeError) as err:
        print(
            f"run_task2a_rust_node: ownership validation refused: {err}",
            file=sys.stderr,
        )
        _emit_observation(
            args.observation_out,
            node_id=args.node_id,
            phase="setup_refused",
            argv=[],
            exit_code=2,
            detail=f"ownership validation refused: {err}",
        )
        return 2

    fq = _fq_from_node_id(args.node_id)
    exact_leaf = str(node.get("rust_test_target") or "")
    if not exact_leaf:
        print(
            f"run_task2a_rust_node: rust_test_target missing for {args.node_id!r}",
            file=sys.stderr,
        )
        _emit_observation(
            args.observation_out,
            node_id=args.node_id,
            phase="setup_refused",
            argv=[],
            exit_code=2,
            detail="rust_test_target missing",
        )
        return 2

    # Fixture protocol path: NEVER a positive. Exit 2, no receipt.
    if args.fixture_executable is not None:
        print(
            "run_task2a_rust_node: fixture protocol path refuses receipt "
            "(arbitrary fixture binaries cannot produce positive receipts)",
            file=sys.stderr,
        )
        _emit_observation(
            args.observation_out,
            node_id=args.node_id,
            phase="fixture_refused",
            argv=[str(args.fixture_executable)],
            exit_code=2,
            detail="fixture protocol path refuses NativeCiReceiptV1",
        )
        return 2

    # Positive path: require Cargo messages OR invoke exact Cargo selection.
    # Empty cargo-message files stay refusal-only (selection LookupError).
    try:
        if args.cargo_json_messages is not None:
            raw = args.cargo_json_messages.read_text(encoding="utf-8")
            messages = [json.loads(line) for line in raw.splitlines() if line.strip()]
            package, kind, name = _selection_from_manifest_node(node)
            selected = select_cargo_test_executable(
                messages,
                package=package,
                selected_kind=kind,
                selected_name=name,
            )
        else:
            selected = _invoke_cargo_json_selection(node)
    except (LookupError, ValueError, OSError, json.JSONDecodeError) as err:
        print(
            f"run_task2a_rust_node: cargo selected-binary refused: {err}",
            file=sys.stderr,
        )
        _emit_observation(
            args.observation_out,
            node_id=args.node_id,
            phase="setup_refused",
            argv=[],
            exit_code=2,
            detail=f"cargo selected-binary refused: {err}",
        )
        return 2

    # Exact Cargo-selected path is the only executable path.
    list_argv = [str(selected), "--list", "--format", "terse"]
    listed = subprocess.run(list_argv, capture_output=True, text=True, check=False)
    if args.rust_list_out is not None:
        args.rust_list_out.write_text(listed.stdout, encoding="utf-8")
    if listed.returncode != 0:
        print(
            "run_task2a_rust_node: selected executable --list --format terse failed; "
            "refusing receipt",
            file=sys.stderr,
        )
        _write_text(args.rust_status_out, f"list_exit={listed.returncode}\n")
        _write_text(args.rust_stderr_out, listed.stderr)
        _emit_observation(
            args.observation_out,
            node_id=args.node_id,
            phase="setup_refused",
            argv=list_argv,
            exit_code=2,
            stdout=listed.stdout,
            stderr=listed.stderr,
            detail="selected executable --list failed",
            selected_executable=str(selected),
        )
        return 2
    names = fq_names_from_terse_list(listed.stdout)
    # Exact selected leaf only — never a contains-colons heuristic or endswith fallback.
    count = names.count(exact_leaf)
    if count != 1:
        print(
            f"run_task2a_rust_node: --list --format terse must contain exact leaf "
            f"{exact_leaf!r} exactly once (count={count}); refusing receipt",
            file=sys.stderr,
        )
        _write_text(args.rust_status_out, f"list_leaf_count={count}\n")
        _emit_observation(
            args.observation_out,
            node_id=args.node_id,
            phase="setup_refused",
            argv=list_argv,
            exit_code=2,
            stdout=listed.stdout,
            stderr=listed.stderr,
            detail=f"exact leaf list count={count}",
            selected_executable=str(selected),
        )
        return 2

    exact_argv = [str(selected), exact_leaf, "--exact", "--include-ignored"]
    _write_json(
        args.argv_out,
        {
            "selected_executable": str(selected),
            "list_argv": list_argv,
            "exact_argv": exact_argv,
            "exact_leaf": exact_leaf,
            "fq": fq,
        },
    )
    exact = subprocess.run(exact_argv, capture_output=True, text=True, check=False)
    _write_text(args.rust_stdout_out, exact.stdout)
    _write_text(args.rust_stderr_out, exact.stderr)
    _write_text(
        args.rust_status_out,
        f"list_exit=0\nexact_exit={exact.returncode}\nselected={selected}\nleaf={exact_leaf}\n",
    )
    phase = classify_rust_node_phase(
        exit_code=exact.returncode,
        stdout=exact.stdout,
        stderr=exact.stderr,
    )
    if phase != "executed_refused_receipt":
        print(
            f"run_task2a_rust_node: non-behavioral outcome phase={phase}; "
            "crash/panic is not behavioral RED",
            file=sys.stderr,
        )
        _emit_observation(
            args.observation_out,
            node_id=args.node_id,
            phase=phase,
            argv=exact_argv,
            exit_code=2,
            stdout=exact.stdout,
            stderr=exact.stderr,
            detail=f"non-behavioral phase={phase} (A61)",
            selected_executable=str(selected),
        )
        return 2

    _ = (args.receipt_out, args.artifact_source)
    print(
        "run_task2a_rust_node: stable-Rust live Actions binding not implemented; "
        "refusing to emit NativeCiReceiptV1 (empty Rust positive forbidden)",
        file=sys.stderr,
    )
    _emit_observation(
        args.observation_out,
        node_id=args.node_id,
        phase="executed_refused_receipt",
        argv=exact_argv,
        exit_code=2,
        stdout=exact.stdout,
        stderr=exact.stderr,
        detail=(
            "Selected executable listed exact leaf once and executed it; "
            "NativeCiReceiptV1 emit not implemented (RED)"
        ),
        selected_executable=str(selected),
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
