#!/usr/bin/env python3
"""Behaviorless Round-60 runner stub for Task 2A Python nodes.

Contract (when GREEN-bound): invoke the exact manifest pytest command_vector for
the requested leaf node, then bind a NativeCiReceiptV1 from live Actions context.

Ownership (RED + GREEN): ``--node-id`` must resolve to a manifest-owned node
before any execution. Exact checks (all required):

- ``command_vector[0] == "pytest"``
- ``command_vector[1]`` equals the pytest nodeid (``node_id`` without ``python::``)
- ``command_vector[2:]`` equals the fixed suffix ``["-q", "--timeout=15"]``
- ``workflow``, ``runner_class``, ``required_non_skip``
- live ``GITHUB_JOB`` from the environment equals manifest ``job``
  (caller ``--expected-job`` is not authoritative)

RED phase: may accept ``--fixture-executable`` for bounded protocol validation
only — that path ALWAYS exits 2 and emits no receipt. Arbitrary fixture binaries
cannot produce positive receipts. The positive path executes only the exact
manifest pytest command_vector, writes raw execution evidence + a bounded
non-authoritative observation artifact, independently verifies the requested
concrete node was collected and executed non-skipped via JUnit, then still
refuses NativeCiReceiptV1 until live Actions binding exists.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

_FIXED_PYTEST_SUFFIX = ("-q", "--timeout=15")
_OBSERVATION_SCHEMA = "Task2aRawExecutionObservationV1"
_OBSERVATION_VERSION = 1
# Bounded observation: refuse to write oversized evidence blobs into the JSON.
_MAX_OBSERVATION_STREAM_CHARS = 64 * 1024


def _load_manifest_node(manifest_path: Path, node_id: str) -> dict:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    nodes = payload.get("nodes") or []
    matches = [n for n in nodes if n.get("id") == node_id]
    if not matches:
        raise LookupError(f"unowned or unknown node id: {node_id!r}")
    if len(matches) != 1:
        raise LookupError(f"duplicate manifest node id: {node_id!r}")
    return matches[0]


def validate_python_node_ownership(*, manifest_path: Path, node_id: str) -> dict:
    """Refuse unknown/unowned nodes; validate exact argv/job/workflow ownership."""
    node = _load_manifest_node(manifest_path, node_id)
    if not str(node_id).startswith("python::"):
        raise ValueError(f"python runner refuses non-python node id: {node_id!r}")
    vector = [str(x) for x in (node.get("command_vector") or [])]
    if not vector or vector[0] != "pytest":
        raise ValueError(f"python node command_vector must start with pytest: {vector!r}")
    pytest_nodeid = str(node_id).removeprefix("python::")
    if len(vector) < 2 or vector[1] != pytest_nodeid:
        raise ValueError(
            f"command_vector[1] must equal pytest nodeid exactly: "
            f"vector[1]={vector[1] if len(vector) > 1 else None!r} nodeid={pytest_nodeid!r}"
        )
    if tuple(vector[2:]) != _FIXED_PYTEST_SUFFIX:
        raise ValueError(
            f"command_vector suffix must be exactly {_FIXED_PYTEST_SUFFIX!r}, "
            f"got {tuple(vector[2:])!r}"
        )
    if any(tok.startswith("python::") for tok in vector):
        raise ValueError(f"pytest argv must not carry python:: prefix: {vector!r}")
    if node.get("workflow") != "ci.yml":
        raise ValueError(f"workflow ownership mismatch: {node.get('workflow')!r}")
    if node.get("runner_class") != "windows-latest":
        raise ValueError(f"runner_class ownership mismatch: {node.get('runner_class')!r}")
    if not node.get("required_non_skip"):
        raise ValueError(f"node {node_id!r} is not required_non_skip")
    live_job = os.environ.get("GITHUB_JOB")
    if not live_job:
        raise ValueError("GITHUB_JOB environment variable is required for job ownership")
    job = str(node.get("job") or "")
    if job != live_job:
        raise ValueError(f"job ownership mismatch: manifest={job!r} GITHUB_JOB={live_job!r}")
    return node


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


def _junit_testcase_for_nodeid(junit_path: Path, pytest_nodeid: str) -> ET.Element | None:
    """Locate the JUnit testcase matching the exact pytest nodeid."""
    if not junit_path.is_file():
        return None
    try:
        root = ET.parse(junit_path).getroot()
    except ET.ParseError:
        return None
    # pytest nodeid: path::Class::name[param] or path::name[param]
    # JUnit classname/name vary; match against reconstructed nodeid forms.
    candidates: list[ET.Element] = []
    for case in root.iter("testcase"):
        classname = str(case.attrib.get("classname") or "")
        name = str(case.attrib.get("name") or "")
        file_attr = str(case.attrib.get("file") or "")
        # Common pytest-junit shapes:
        #   classname="tests.unit.test_foo", name="test_bar[param]"
        #   file="tests/unit/test_foo.py", name="test_bar[param]"
        dotted = classname.replace(".", "/")
        forms = {
            f"{dotted}.py::{name}" if dotted and name else "",
            f"{file_attr}::{name}" if file_attr and name else "",
            f"{classname}::{name}" if classname and name else "",
            name,
        }
        if pytest_nodeid in forms or name == pytest_nodeid.split("::")[-1]:
            # Prefer exact nodeid equality when reconstructable.
            if pytest_nodeid in forms:
                return case
            candidates.append(case)
    if len(candidates) == 1:
        # Last-segment name match only when unambiguous.
        leaf = pytest_nodeid.split("::")[-1]
        if str(candidates[0].attrib.get("name") or "") == leaf:
            return candidates[0]
    for case in candidates:
        leaf = pytest_nodeid.split("::")[-1]
        if str(case.attrib.get("name") or "") == leaf:
            # Reconstruct from file+name when present.
            file_attr = str(case.attrib.get("file") or "").replace("\\", "/")
            if file_attr and pytest_nodeid == f"{file_attr}::{leaf}":
                return case
            classname = str(case.attrib.get("classname") or "")
            dotted = classname.replace(".", "/")
            if dotted and pytest_nodeid == f"{dotted}.py::{leaf}":
                return case
    return None


def verify_junit_node_executed_non_skipped(*, junit_path: Path, pytest_nodeid: str) -> None:
    """Independently verify the concrete node was collected and executed non-skipped."""
    if not junit_path.is_file():
        raise LookupError(f"junit file missing: {junit_path}")
    try:
        root = ET.parse(junit_path).getroot()
    except ET.ParseError as err:
        raise LookupError(f"junit parse failed: {err}") from err
    cases = list(root.iter("testcase"))
    if not cases:
        raise LookupError(f"junit contains no testcase elements for {pytest_nodeid!r}")
    case = _junit_testcase_for_nodeid(junit_path, pytest_nodeid)
    if case is None and len(cases) == 1:
        # Exact single-nodeid invocation: the sole testcase is the requested leaf
        # when its name equals the nodeid leaf (param-expanded names included).
        sole = cases[0]
        leaf = pytest_nodeid.split("::")[-1]
        if str(sole.attrib.get("name") or "") == leaf:
            case = sole
    if case is None:
        raise LookupError(f"junit missing collected/executed testcase for nodeid {pytest_nodeid!r}")
    if case.find("skipped") is not None:
        raise ValueError(f"junit testcase for {pytest_nodeid!r} was skipped (required_non_skip)")


def classify_pytest_node_phase(
    *,
    junit_path: Path,
    pytest_nodeid: str,
    exit_code: int,
) -> str:
    """A61 / HIGH#2: crash/setup/import errors are never behavioral RED.

    ``executed_refused_receipt`` is reserved for arms that exercised the contract
    (pass or assertion ``<failure>``). JUnit ``<error>``, negative exit (signal),
    and pytest internal/usage exits are ``crash_or_setup``.
    """
    if exit_code < 0 or exit_code in {3, 4}:
        return "crash_or_setup"
    case = _junit_testcase_for_nodeid(junit_path, pytest_nodeid)
    if case is None and junit_path.is_file():
        try:
            root = ET.parse(junit_path).getroot()
        except ET.ParseError:
            return "crash_or_setup"
        cases = list(root.iter("testcase"))
        if len(cases) == 1:
            leaf = pytest_nodeid.split("::")[-1]
            if str(cases[0].attrib.get("name") or "") == leaf:
                case = cases[0]
    if case is not None and case.find("error") is not None:
        return "crash_or_setup"
    return "executed_refused_receipt"


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
    }
    _write_json(path, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--junitxml", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--fixture-executable", type=Path, default=None)
    parser.add_argument("--artifact-source", type=Path, default=None)
    parser.add_argument("--stdout-out", type=Path, default=None)
    parser.add_argument("--stderr-out", type=Path, default=None)
    parser.add_argument("--exit-out", type=Path, default=None)
    parser.add_argument("--argv-out", type=Path, default=None)
    parser.add_argument(
        "--observation-out",
        type=Path,
        default=None,
        help="Bounded Task2aRawExecutionObservationV1 (non-authoritative)",
    )
    args = parser.parse_args(argv)

    if args.node_id.endswith("test_exact_current_run_positive_receipt"):
        print(
            "run_task2a_pytest_nodes: refusing self-recursive positive node id",
            file=sys.stderr,
        )
        _emit_observation(
            args.observation_out,
            node_id=args.node_id,
            phase="setup_refused",
            argv=[],
            exit_code=2,
            detail="self-recursive positive node id refused",
        )
        return 2

    try:
        node = validate_python_node_ownership(
            manifest_path=args.manifest,
            node_id=args.node_id,
        )
    except (LookupError, ValueError, OSError, json.JSONDecodeError) as err:
        print(
            f"run_task2a_pytest_nodes: ownership validation refused: {err}",
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

    # Fixture protocol path: bounded validation ONLY. Never a positive receipt.
    if args.fixture_executable is not None:
        if args.fixture_executable.is_file():
            subprocess.run(
                [str(args.fixture_executable), str(args.junitxml)],
                capture_output=True,
                text=True,
                check=False,
            )
        print(
            "run_task2a_pytest_nodes: fixture protocol path refuses receipt "
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

    # Positive path: execute ONLY the exact manifest pytest command_vector.
    vector = [str(x) for x in node["command_vector"]]
    pytest_nodeid = str(args.node_id).removeprefix("python::")
    if vector[0] == "pytest":
        cmd = [sys.executable, "-m", "pytest", *vector[1:]]
    else:
        cmd = list(vector)
    cmd = [*cmd, f"--junitxml={args.junitxml}"]
    _write_json(args.argv_out, {"manifest_command_vector": vector, "executed_argv": cmd})
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    _write_text(args.stdout_out, proc.stdout)
    _write_text(args.stderr_out, proc.stderr)
    _write_text(args.exit_out, f"{proc.returncode}\n")

    try:
        verify_junit_node_executed_non_skipped(
            junit_path=args.junitxml,
            pytest_nodeid=pytest_nodeid,
        )
    except (LookupError, ValueError, OSError) as err:
        print(
            f"run_task2a_pytest_nodes: junit execution evidence refused: {err}",
            file=sys.stderr,
        )
        _emit_observation(
            args.observation_out,
            node_id=args.node_id,
            phase="setup_refused",
            argv=cmd,
            exit_code=2,
            stdout=proc.stdout,
            stderr=proc.stderr,
            detail=f"junit execution evidence refused: {err}",
        )
        return 2

    phase = classify_pytest_node_phase(
        junit_path=args.junitxml,
        pytest_nodeid=pytest_nodeid,
        exit_code=proc.returncode,
    )
    if phase != "executed_refused_receipt":
        print(
            f"run_task2a_pytest_nodes: non-behavioral outcome phase={phase}; "
            "crash/setup is not behavioral RED",
            file=sys.stderr,
        )
        _emit_observation(
            args.observation_out,
            node_id=args.node_id,
            phase=phase,
            argv=cmd,
            exit_code=2,
            stdout=proc.stdout,
            stderr=proc.stderr,
            detail=f"non-behavioral phase={phase} (A61)",
        )
        return 2

    _ = (args.receipt_out, args.artifact_source)
    print(
        "run_task2a_pytest_nodes: live Actions binding + receipt emit not implemented; "
        "refusing to emit NativeCiReceiptV1",
        file=sys.stderr,
    )
    _emit_observation(
        args.observation_out,
        node_id=args.node_id,
        phase="executed_refused_receipt",
        argv=cmd,
        exit_code=2,
        stdout=proc.stdout,
        stderr=proc.stderr,
        detail=(
            "Concrete node collected and executed non-skipped; "
            "NativeCiReceiptV1 emit not implemented (RED)"
        ),
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
