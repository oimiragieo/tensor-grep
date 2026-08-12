"""Round-60 RED group 4: NativeCiReceiptV1 live-tuple verifier + census cross-check.

Task 2A / #89 Step 1. Parser is a behaviorless shell with exact contract tests.
Verifier accepts primitive ArtifactSource paths and independently derives live
tuple / JUnit / Rust census / digests — never caller-supplied claims. Positive
control actually executes both Python and Rust runner entry paths against
bounded deterministic fixture executables (source-tree attribution). Empty Rust
positive is forbidden. Fail-closed stubs keep this RED.

These tests MUST fail against unmodified / behaviorless seams. Do not weaken.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import native_ci_receipt as receipt_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "tests" / "fixtures" / "task2a_windows_node_manifest.json"
PY_RUNNER = REPO_ROOT / "scripts" / "run_task2a_pytest_nodes.py"
RUST_RUNNER = REPO_ROOT / "scripts" / "run_task2a_rust_node.py"


def task2a_owned(fn):  # type: ignore[no-untyped-def]
    """Ownership marker for closed-world AST census (must match helper name)."""
    fn._task2a_owned = True  # type: ignore[attr-defined]
    return fn


def _live_environ() -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": "oimiragieo/tensor-grep",
        "GITHUB_SHA": "deadbeef" * 5,
        "GITHUB_RUN_ID": "30793797849",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_WORKFLOW": "CI",
        "GITHUB_JOB": "native-build-smoke",
        "RUNNER_NAME": "GitHub Actions 100000",
        "ACTIONS_RUNTIME_URL": "https://example.invalid/actions/",
        "ACTIONS_RUNTIME_TOKEN": "token",
    }


def _live_runner_identity() -> str:
    return hashlib.sha256(b"GitHub Actions 100000").hexdigest()


def _receipt_obj(**overrides: object) -> receipt_mod.NativeCiReceiptV1:
    base: dict[str, object] = {
        "version": 1,
        "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "commit_sha": "deadbeef" * 5,
        "workflow_run_id": "30793797849",
        "run_attempt": "1",
        "job_name": "native-build-smoke",
        "runner_identity_sha256": _live_runner_identity(),
        "binary_path": "tg.exe",
        "binary_version": "1.102.1",
        "binary_sha256_pre": "cc" * 32,
        "binary_sha256_post": "cc" * 32,
        "node_list": (
            "python::tests/unit/test_native_ci_receipt_v1.py::"
            "test_exact_current_run_positive_receipt",
        ),
        "node_census_digest": "dd" * 32,
        "argv_digest": "ee" * 32,
        "output_digest": "ff" * 32,
        "exit_digest": "11" * 32,
        "artifact_namespace": "task2a-native-ci/30793797849/1",
        "attribution": "source-tree",
    }
    base.update(overrides)
    nodes = base["node_list"]
    if isinstance(nodes, list):
        node_tuple = tuple(str(n) for n in nodes)
    else:
        node_tuple = tuple(nodes)  # type: ignore[arg-type]
    return receipt_mod.NativeCiReceiptV1(
        version=int(base["version"]),  # type: ignore[arg-type]
        manifest_sha256=str(base["manifest_sha256"]),
        commit_sha=str(base["commit_sha"]),
        workflow_run_id=str(base["workflow_run_id"]),
        run_attempt=str(base["run_attempt"]),
        job_name=str(base["job_name"]),
        runner_identity_sha256=str(base["runner_identity_sha256"]),
        binary_path=str(base["binary_path"]),
        binary_version=str(base["binary_version"]),
        binary_sha256_pre=str(base["binary_sha256_pre"]),
        binary_sha256_post=str(base["binary_sha256_post"]),
        node_list=node_tuple,
        node_census_digest=str(base["node_census_digest"]),
        argv_digest=str(base["argv_digest"]),
        output_digest=str(base["output_digest"]),
        exit_digest=str(base["exit_digest"]),
        artifact_namespace=str(base["artifact_namespace"]),
        attribution=str(base["attribution"]),
    )


def _write_fixture_executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@task2a_owned
def test_manifest_command_digests_recompute_and_closed_world_nodes() -> None:
    """command_digest recomputable; closed world vs independent ownership registry."""
    import hashlib
    import os
    import subprocess
    import time

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from helpers import task2a_ownership as ownership

    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ids = [n["id"] for n in payload["nodes"]]
    assert len(ids) == len(set(ids)), "duplicate manifest node IDs refused"
    assert not any("*" in i or i.endswith("[") for i in ids), "wildcard/base param IDs refused"
    for node in payload["nodes"]:
        vector = node["command_vector"]
        blob = json.dumps(vector, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert hashlib.sha256(blob).hexdigest() == node["command_digest"]
        assert node["workflow"] == "ci.yml"
        assert node["job"] in {"test-python", "native-build-smoke"}
        assert node["runner_class"] == "windows-latest"
        assert node["required_non_skip"] is True
        if vector and vector[0] == "pytest":
            assert not any(isinstance(tok, str) and tok.startswith("python::") for tok in vector), (
                f"pytest argv must use tests/...::node without python:: prefix: {vector}"
            )

    # Independent ownership registry spanning all four families — deletion of a
    # marker OR a manifest row must fail.
    ownership.assert_closed_world_ownership()
    registry = ownership.registry_canonical_unique_ids()
    assert set(ids) == set(registry), (
        f"manifest/registry diverge; missing={sorted(set(registry) - set(ids))} "
        f"extra={sorted(set(ids) - set(registry))}"
    )
    # Refuse leftover parametrized ledger IDs.
    assert not any("test_uninstrumented_pcre2_refused_on_every_real_route" in i for i in ids)

    # Mutation-style governance: marker / manifest / static-registry deletions
    # cannot self-thin (embedded so the native suite shape stays 44f/9p).
    ast_full = ownership.owned_python_ids_from_ast()
    man_full = ownership.load_manifest_node_ids()
    static_full = list(ownership.TASK2A_OWNED_PYTHON_NODE_IDS)
    assert len(ast_full) >= 2 and len(man_full) >= 2 and len(static_full) >= 2
    victim = ast_full[0]
    orig_ast = ownership.owned_python_ids_from_ast
    orig_man = ownership.load_manifest_node_ids
    try:
        ownership.owned_python_ids_from_ast = lambda: [x for x in ast_full if x != victim]  # type: ignore[method-assign]
        with pytest.raises(AssertionError, match="static Python registry != AST"):
            ownership.assert_closed_world_ownership()
        ownership.owned_python_ids_from_ast = orig_ast  # type: ignore[method-assign]

        ownership.load_manifest_node_ids = lambda: [x for x in man_full if x != victim]  # type: ignore[method-assign]
        with pytest.raises(AssertionError, match="static registry != manifest"):
            ownership.assert_closed_world_ownership()
        ownership.load_manifest_node_ids = orig_man  # type: ignore[method-assign]

        ownership.owned_python_ids_from_ast = lambda: [x for x in ast_full if x != victim]  # type: ignore[method-assign]
        ownership.load_manifest_node_ids = lambda: [x for x in man_full if x != victim]  # type: ignore[method-assign]
        with pytest.raises(AssertionError):
            ownership.assert_closed_world_ownership()
        ownership.owned_python_ids_from_ast = orig_ast  # type: ignore[method-assign]
        ownership.load_manifest_node_ids = orig_man  # type: ignore[method-assign]

        ownership.TASK2A_OWNED_PYTHON_NODE_IDS = tuple(  # type: ignore[misc]
            x for x in static_full if x != victim
        )
        with pytest.raises(AssertionError):
            ownership.assert_closed_world_ownership()
    finally:
        ownership.owned_python_ids_from_ast = orig_ast  # type: ignore[method-assign]
        ownership.load_manifest_node_ids = orig_man  # type: ignore[method-assign]
        ownership.TASK2A_OWNED_PYTHON_NODE_IDS = tuple(static_full)  # type: ignore[misc]
    ownership.assert_closed_world_ownership()

    # One bounded pytest --collect-only over the four family files (no per-node
    # startup). Parse exact concrete node IDs; Counter equality only — substring
    # matching / module-wide extras / warnings / free text are not evidence.
    from collections import Counter

    py_nodes = [n for n in payload["nodes"] if str(n["id"]).startswith("python::")]
    assert py_nodes, "manifest must own concrete Python nodes"
    expected_nodeids = [str(n["id"]).removeprefix("python::") for n in py_nodes]
    assert not any("*" in i or i.endswith("[") for i in expected_nodeids)
    family_files = list(ownership.PYTHON_FAMILY_FILES)
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    collect_started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *family_files],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    collect_elapsed = time.perf_counter() - collect_started
    assert proc.returncode == 0, (
        f"batched pytest --collect-only failed ({collect_elapsed:.2f}s): {proc.stderr}"
    )

    def _parse_collect_nodeids(stdout: str) -> list[str]:
        """Parse ``pytest --collect-only -q`` into exact concrete nodeids only."""
        out: list[str] = []
        for line in stdout.splitlines():
            raw = line.strip()
            if not raw or raw.startswith("=") or "warning" in raw.lower():
                continue
            # Summary lines: "N tests collected in ..."
            if " selected" in raw or "collected" in raw or raw.startswith("no tests"):
                continue
            if "::" not in raw:
                continue
            # Exact nodeid lines look like tests/unit/foo.py::test_bar[param]
            if raw.startswith("tests/") or raw.startswith("scripts/"):
                out.append(raw)
        return out

    actual_nodeids = _parse_collect_nodeids(proc.stdout)
    assert Counter(actual_nodeids) == Counter(expected_nodeids), (
        "batched collect Counter(actual) != Counter(expected); "
        f"only_actual={sorted((Counter(actual_nodeids) - Counter(expected_nodeids)).elements())} "
        f"only_expected={sorted((Counter(expected_nodeids) - Counter(actual_nodeids)).elements())} "
        f"elapsed={collect_elapsed:.2f}s"
    )
    assert collect_elapsed < 12.0, (
        f"batched collect must stay comfortably under pytest --timeout=15 "
        f"(elapsed={collect_elapsed:.2f}s)"
    )


@task2a_owned
def test_manifest_is_static_without_live_run_ids() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["schema"] == "task2a_windows_node_manifest"
    blob = MANIFEST.read_text(encoding="utf-8")
    assert "GITHUB_RUN_ID" not in blob
    assert "pending-round60-green" not in blob
    assert "pending" not in {n.get("command_digest") for n in payload["nodes"]}
    windows_required = [n for n in payload["nodes"] if n.get("required_non_skip")]
    assert windows_required
    assert all(n.get("runner_class") == "windows-latest" for n in windows_required)
    ledger_py = [
        n["id"]
        for n in payload["nodes"]
        if n["id"].endswith("test_uninstrumented_pcre2_refused_on_bootstrap")
        or n["id"].endswith("test_uninstrumented_pcre2_refused_on_full_cli")
    ]
    assert len(ledger_py) == 2
    rust_routes = [n for n in payload["nodes"] if n["id"].startswith("rust::")]
    assert len(rust_routes) == 12
    expected_rust_targets = {
        "pattern_file_search_input_limit_direct_native",
        "pattern_file_bytes_search_input_limit_direct_native",
        "pattern_file_below_cap_native_json_success",
        "pcre2_search_input_limit_direct_native",
        "below_cap_non_pcre2_direct_native_json_success",
        "rg_passthrough::tests::execute_ripgrep_search_pcre2_search_input_limit",
        "rg_passthrough::tests::execute_ripgrep_search_below_cap_non_pcre2_starts_rg_once",
        "python_sidecar::tests::early_passthrough_pcre2_format_json_search_input_limit",
        "python_sidecar::tests::early_passthrough_below_cap_non_pcre2_starts_sidecar_once",
        "native_search::tests::run_native_search_leaf_matcher_construction_exactly_once",
        "native_search::tests::pcre2_direct_native_route_zero_matcher_constructions_before_refusal",
        "native_search::tests::below_cap_direct_native_route_one_matcher_construction",
    }
    assert {n["rust_test_target"] for n in rust_routes} == expected_rust_targets
    for node in rust_routes:
        target = str(node.get("rust_test_target") or "")
        assert target, f"rust_test_target required: {node['id']}"
        assert not target.startswith("tg::"), target
        assert not target.startswith("tensor_grep_rs::"), target
        assert node.get("binary") in {
            "tg",
            "tensor_grep_rs",
            "task2a_direct_native_round60",
        }
        assert "tensor_grep_rs" in node["command_vector"]
        assert "--manifest-path" in node["command_vector"]
        assert "rust_core/Cargo.toml" in node["command_vector"]
        assert node["job"] == "native-build-smoke"
        vector = node["command_vector"]
        assert "--exact" in vector
        binary = node["binary"]
        if binary == "tg":
            assert "--bin" in vector and "tg" in vector
            assert "--include-ignored" in vector
        elif binary == "task2a_direct_native_round60":
            assert "--test" in vector and "task2a_direct_native_round60" in vector
            assert "--include-ignored" in vector
            # Integration leaf names are not crate-prefixed FQs.
            assert "::" not in target
        else:
            assert "--lib" in vector
            assert "::" in target, f"lib rust_test_target must be FQ: {target!r}"
            if "matcher_construction" not in target:
                assert "--include-ignored" in vector
    # Runner must pin terse list argv (exact equality protocol, not substring count).
    runner_src = RUST_RUNNER.read_text(encoding="utf-8")
    assert '"--list", "--format", "terse"' in runner_src or (
        "'--list', '--format', 'terse'" in runner_src
    )
    assert "fq_names_from_terse_list" in runner_src
    assert "names.count(" in runner_src
    assert "listed.stdout.count" not in runner_src
    assert ".stdout.count(fq)" not in runner_src


@task2a_owned
def test_parser_accepts_valid_receipt_positive_control() -> None:
    raw = {
        "version": 1,
        "manifest_sha256": "aa" * 32,
        "commit_sha": "deadbeef" * 5,
        "workflow_run_id": "30793797849",
        "run_attempt": "1",
        "job_name": "native-build-smoke",
        "runner_identity_sha256": "bb" * 32,
        "binary_path": "tg.exe",
        "binary_version": "1.102.1",
        "binary_sha256_pre": "cc" * 32,
        "binary_sha256_post": "cc" * 32,
        "node_list": ["python::x::y"],
        "node_census_digest": "dd" * 32,
        "argv_digest": "ee" * 32,
        "output_digest": "ff" * 32,
        "exit_digest": "11" * 32,
        "artifact_namespace": "task2a-native-ci/1/1",
        "attribution": "source-tree",
    }
    receipt = receipt_mod.parse_native_ci_receipt(raw)
    assert receipt.version == 1
    assert receipt.attribution == "source-tree"


@task2a_owned
@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"version": True}, "version|type|bool"),
        ({"version": "1"}, "version|type|string"),
        ({"schema": "Other"}, "schema|unknown"),
        ({"manifest_sha256": "g" * 64}, "digest|alphabet"),
        ({"manifest_sha256": "AA" * 32}, "digest|alphabet|lowercase"),
        ({"manifest_sha256": "a" * 63}, "digest|length"),
        ({"node_list": ["a", "a"]}, "duplicate"),
        ({"commit_sha": ["x"]}, "nonstring|type"),
        ({"job_name": {"x": 1}}, "nonstring|type"),
        ({"run_attempt": True}, "nonstring|type|bool"),
    ],
    ids=[
        "bool_version",
        "string_version",
        "arbitrary_schema",
        "digest_alphabet",
        "digest_uppercase",
        "digest_length",
        "duplicate_nodes",
        "list_field",
        "dict_field",
        "bool_field",
    ],
)
def test_parser_refuses_schema_type_value_length_negatives(payload: dict, match: str) -> None:
    base = {
        "version": 1,
        "manifest_sha256": "aa" * 32,
        "commit_sha": "deadbeef" * 5,
        "workflow_run_id": "1",
        "run_attempt": "1",
        "job_name": "native-build-smoke",
        "runner_identity_sha256": "bb" * 32,
        "binary_path": "tg.exe",
        "binary_version": "1.0.0",
        "binary_sha256_pre": "cc" * 32,
        "binary_sha256_post": "cc" * 32,
        "node_list": ["python::x"],
        "node_census_digest": "dd" * 32,
        "argv_digest": "ee" * 32,
        "output_digest": "ff" * 32,
        "exit_digest": "11" * 32,
        "artifact_namespace": "ns",
        "attribution": "source-tree",
    }
    base.update(payload)
    with pytest.raises(ValueError, match=match):
        receipt_mod.parse_native_ci_receipt(base)


@task2a_owned
def test_parser_refuses_duplicate_unknown_oversized() -> None:
    with pytest.raises(ValueError, match="unknown"):
        receipt_mod.parse_native_ci_receipt({
            "version": 1,
            "extra_authority": True,
            "manifest_sha256": "aa" * 32,
            "commit_sha": "deadbeef" * 5,
            "workflow_run_id": "1",
            "run_attempt": "1",
            "job_name": "j",
            "runner_identity_sha256": "bb" * 32,
            "binary_path": "tg",
            "binary_version": "1",
            "binary_sha256_pre": "cc" * 32,
            "binary_sha256_post": "cc" * 32,
            "node_list": ["n"],
            "node_census_digest": "dd" * 32,
            "argv_digest": "ee" * 32,
            "output_digest": "ff" * 32,
            "exit_digest": "11" * 32,
            "artifact_namespace": "ns",
            "attribution": "source-tree",
        })
    dup = '{"version":1,"version":9}'
    with pytest.raises(ValueError, match="duplicate"):
        receipt_mod.parse_native_ci_receipt(dup)


@task2a_owned
def test_derive_live_actions_tuple_from_environ() -> None:
    derived = receipt_mod.derive_live_actions_tuple(_live_environ())
    assert derived.repository == "oimiragieo/tensor-grep"
    assert derived.commit_sha == "deadbeef" * 5
    assert derived.workflow_run_id == "30793797849"
    assert derived.run_attempt == "1"
    assert derived.job_name == "native-build-smoke"
    assert derived.runner_identity_sha256
    assert derived.artifact_namespace == "task2a-native-ci/30793797849/1"


@task2a_owned
def test_runner_owned_nonrecursive_leaf() -> None:
    """Manifest-owned nonrecursive runner leaf used by the positive receipt control.

    This leaf must remain a no-op structural pin (not self-recursive). Ownership
    markers/registry/manifest must all name it.
    """
    assert MANIFEST.is_file()
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert any(n["id"].endswith("::test_runner_owned_nonrecursive_leaf") for n in payload["nodes"])


@task2a_owned
def test_exact_current_run_positive_receipt(tmp_path: Path) -> None:
    """Execute both runners against separate fresh dirs; load exact receipt files.

    Python and Rust artifact directories must both be fresh and distinct. Fixture
    executables implement real JUnit emit and a nonempty fully-qualified stable
    Rust ``--list`` plus ``--exact`` protocol. The node under test must not be
    this positive itself (no self-recursion).
    """
    import hashlib
    import textwrap

    environ = _live_environ()
    py_dir = tmp_path / "py-artifacts"
    rust_dir = tmp_path / "rust-artifacts"
    py_dir.mkdir()
    rust_dir.mkdir()
    assert receipt_mod.require_empty_current_run_directory(py_dir) is True
    assert receipt_mod.require_empty_current_run_directory(rust_dir) is True
    assert py_dir.resolve() != rust_dir.resolve()

    # Manifest-owned nonrecursive leaf — never the outer positive.
    leaf_node = (
        "python::tests/unit/test_native_ci_receipt_v1.py::test_runner_owned_nonrecursive_leaf"
    )
    rust_fq = "pattern_file_search_input_limit_direct_native"
    rust_node_id = (
        "rust::task2a_direct_native_round60::pattern_file_search_input_limit_direct_native"
    )

    py_fixture = _write_fixture_executable(
        tmp_path / "fixture_pytest_junit.py",
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import sys
            from pathlib import Path
            # Bounded fixture: emit a nonempty JUnit document for the leaf node.
            out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("junit.xml")
            out.write_text(
                '<testsuite tests="1">'
                '<testcase classname="tests.unit.test_native_ci_receipt_v1" '
                'name="test_runner_owned_nonrecursive_leaf"/>'
                "</testsuite>\\n",
                encoding="utf-8",
            )
            print("fixture-junit-ok")
            sys.exit(0)
            """
        ),
    )
    rust_fixture = _write_fixture_executable(
        tmp_path / "fixture_rust_libtest.py",
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import sys
            FQ = {rust_fq!r}
            argv = sys.argv[1:]
            if argv == ["--list", "--format", "terse"]:
                # Stable libtest --list --format terse: exact "<name>: test" lines.
                print(f"{{FQ}}: test")
                sys.exit(0)
            if "--exact" in argv and "--include-ignored" in argv:
                # Exact-run arm after terse --list counted FQ by exact name equality once.
                assert FQ in argv, argv
                print("ok")
                sys.exit(0)
            print(
                "fixture-rust requires --list --format terse or --exact --include-ignored",
                file=sys.stderr,
            )
            sys.exit(2)
            """
        ),
    )

    junit_path = py_dir / "junit.xml"
    rust_list_path = rust_dir / "rust-list.txt"
    receipt_out_py = py_dir / "py-receipt.json"
    receipt_out_rust = rust_dir / "rust-receipt.json"
    env = {
        **os.environ,
        **environ,
        "PYTHONPATH": str(REPO_ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }

    py_proc = subprocess.run(
        [
            sys.executable,
            str(PY_RUNNER),
            "--manifest",
            str(MANIFEST),
            "--junitxml",
            str(junit_path),
            "--receipt-out",
            str(receipt_out_py),
            "--node-id",
            leaf_node,
            "--fixture-executable",
            str(py_fixture),
            "--artifact-source",
            str(py_dir),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    rust_proc = subprocess.run(
        [
            sys.executable,
            str(RUST_RUNNER),
            "--manifest",
            str(MANIFEST),
            "--receipt-out",
            str(receipt_out_rust),
            "--node-id",
            rust_node_id,
            "--fixture-executable",
            str(rust_fixture),
            "--rust-list-out",
            str(rust_list_path),
            "--artifact-source",
            str(rust_dir),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert py_proc.returncode == 0, py_proc.stderr
    assert rust_proc.returncode == 0, rust_proc.stderr
    assert receipt_out_py.is_file() and receipt_out_py.stat().st_size > 0
    assert receipt_out_rust.is_file() and receipt_out_rust.stat().st_size > 0
    assert junit_path.is_file() and junit_path.stat().st_size > 0
    assert rust_list_path.is_file()
    rust_list_text = rust_list_path.read_text(encoding="utf-8")

    def _fq_names_from_terse_list(stdout: str) -> list[str]:
        return [line[: -len(": test")] for line in stdout.splitlines() if line.endswith(": test")]

    listed_names = _fq_names_from_terse_list(rust_list_text)
    assert listed_names.count(rust_fq) == 1, rust_list_text
    # Bounded fixture protocol validation without cold cargo.
    list_proc = subprocess.run(
        [sys.executable, str(rust_fixture), "--list", "--format", "terse"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert list_proc.returncode == 0
    assert list_proc.args == [sys.executable, str(rust_fixture), "--list", "--format", "terse"]
    assert _fq_names_from_terse_list(list_proc.stdout).count(rust_fq) == 1
    exact_proc = subprocess.run(
        [sys.executable, str(rust_fixture), rust_fq, "--exact", "--include-ignored"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert exact_proc.returncode == 0
    # Load the exact runner-produced receipts — never a handwritten replacement.
    py_receipt = receipt_mod.load_receipt(receipt_out_py)
    rust_receipt = receipt_mod.load_receipt(receipt_out_rust)
    py_source = receipt_mod.ArtifactSource(
        current_run_dir=py_dir,
        manifest_path=MANIFEST,
        junit_path=junit_path,
        binary_path=py_fixture,
        environ=environ,
        expected_attribution="source-tree",
    )
    rust_source = receipt_mod.ArtifactSource(
        current_run_dir=rust_dir,
        manifest_path=MANIFEST,
        rust_list_path=rust_list_path,
        binary_path=rust_fixture,
        environ=environ,
        expected_attribution="source-tree",
    )
    py_verdict = receipt_mod.verify_native_ci_receipt(py_receipt, artifact_source=py_source)
    rust_verdict = receipt_mod.verify_native_ci_receipt(rust_receipt, artifact_source=rust_source)
    assert py_verdict.get("ok") is True, f"python receipt verify failed: {py_verdict!r}"
    assert rust_verdict.get("ok") is True, f"rust receipt verify failed: {rust_verdict!r}"
    assert py_receipt.attribution == "source-tree"
    assert rust_receipt.attribution == "source-tree"
    _ = hashlib  # retained for digest helpers in sibling tests


@task2a_owned
def test_seeded_current_run_directory_rejected(tmp_path: Path) -> None:
    current = tmp_path / "seeded"
    current.mkdir()
    (current / "seed.json").write_text("{}", encoding="utf-8")
    assert receipt_mod.require_empty_current_run_directory(current) is False


@task2a_owned
def test_caller_supplied_claims_refused() -> None:
    receipt = _receipt_obj()
    verdict = receipt_mod.verify_native_ci_receipt(
        receipt,
        environ=_live_environ(),
        junit_nodes={"python::x"},
        rust_list_nodes=set(),
        manifest_bytes=MANIFEST.read_bytes(),
    )
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "caller_supplied_claims_refused"


@task2a_owned
def test_clearance_refuses_without_live_immutable_sha_actions_run(tmp_path: Path) -> None:
    """A68 / HIGH#1: no live Actions immutable-SHA tuple ⇒ clearance never ok=True.

    Receipt JSON carrying commit_sha / workflow_run_id alone must not satisfy
    clearance. Absent/empty GITHUB_SHA + GITHUB_RUN_ID + GITHUB_RUN_ATTEMPT +
    GITHUB_WORKFLOW + GITHUB_JOB refuse with an exact reason class.
    """
    current = tmp_path / "run"
    current.mkdir()
    receipt = _receipt_obj()  # caller-shaped SHA/run fields present in JSON
    for environ in (
        None,
        {},
        {
            k: ""
            for k in (
                "GITHUB_SHA",
                "GITHUB_RUN_ID",
                "GITHUB_RUN_ATTEMPT",
                "GITHUB_WORKFLOW",
                "GITHUB_JOB",
            )
        },
    ):
        source = receipt_mod.ArtifactSource(
            current_run_dir=current,
            manifest_path=MANIFEST,
            environ=environ,
            expected_attribution="source-tree",
        )
        verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
        assert verdict.get("ok") is False, f"environ={environ!r} must refuse clearance"
        assert verdict.get("reason") in {
            "live_actions_tuple_missing",
            "no_immutable_sha_run",
        }, f"unexpected reason for environ={environ!r}: {verdict!r}"


@task2a_owned
def test_cross_attempt_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ={**_live_environ(), "GITHUB_RUN_ATTEMPT": "1"},
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(run_attempt="2")
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "run_attempt_mismatch"


@task2a_owned
def test_manifest_drift_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(manifest_sha256="0" * 64)
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False


@task2a_owned
def test_binary_drift_rejected(tmp_path: Path) -> None:
    """Combined binary identity drift when post digest disagrees with live bytes."""
    import hashlib

    current = tmp_path / "run"
    current.mkdir()
    binary = current / "tg.bin"
    binary.write_bytes(b"bin-v1")
    live = hashlib.sha256(binary.read_bytes()).hexdigest()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        binary_path=binary,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    # Only post is wrong; pre matches live — isolates post-side binary_drift.
    receipt = _receipt_obj(binary_sha256_pre=live, binary_sha256_post="f" * 64)
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "binary_drift"


@task2a_owned
def test_source_tree_attribution_cannot_satisfy_wheel_or_installer_proof(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    for expected in ("wheel", "installer"):
        source = receipt_mod.ArtifactSource(
            current_run_dir=current,
            manifest_path=MANIFEST,
            environ=_live_environ(),
            expected_attribution=expected,
        )
        receipt = _receipt_obj(attribution="source-tree")
        verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
        assert verdict.get("ok") is False


@task2a_owned
def test_wheel_attribution_without_raw_wheel_artifact_is_false(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="wheel",
    )
    receipt = _receipt_obj(attribution="wheel")
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "wheel_artifact_missing"


@task2a_owned
def test_installer_attribution_without_raw_installer_artifact_is_false(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="installer",
    )
    receipt = _receipt_obj(attribution="installer")
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "installer_artifact_missing"


@task2a_owned
def test_wheel_attribution_with_source_created_bytes_is_not_publication(tmp_path: Path) -> None:
    """Step-1: source-created current-run bytes must not pass as publication."""
    import hashlib

    current = tmp_path / "run"
    current.mkdir()
    raw = b"PK\x03\x04wheel-bytes-exact"
    wheel = current / "tensor_grep-1.102.1-py3-none-any.whl"
    wheel.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="wheel",
        binary_path=wheel,
    )
    receipt = _receipt_obj(
        attribution="wheel",
        binary_sha256_pre=digest,
        binary_sha256_post=digest,
    )
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "wheel_publication_unproven"


@task2a_owned
def test_wheel_attribution_digest_mismatch_is_false(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    wheel = current / "tensor_grep-1.102.1-py3-none-any.whl"
    wheel.write_bytes(b"PK\x03\x04wheel-bytes-exact")
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="wheel",
        binary_path=wheel,
    )
    receipt = _receipt_obj(
        attribution="wheel",
        binary_sha256_pre="0" * 64,
        binary_sha256_post="0" * 64,
    )
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False


@task2a_owned
def test_installer_attribution_with_source_created_bytes_is_not_publication(tmp_path: Path) -> None:
    """Step-1: source-created current-run bytes must not pass as publication."""
    import hashlib

    current = tmp_path / "run"
    current.mkdir()
    raw = b"# installer artifact exact bytes\n"
    installer = current / "install.ps1"
    installer.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="installer",
        binary_path=installer,
    )
    receipt = _receipt_obj(
        attribution="installer",
        binary_sha256_pre=digest,
        binary_sha256_post=digest,
    )
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "installer_publication_unproven"


@task2a_owned
def test_installer_attribution_digest_mismatch_is_false(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    installer = current / "install.ps1"
    installer.write_bytes(b"# installer artifact exact bytes\n")
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="installer",
        binary_path=installer,
    )
    receipt = _receipt_obj(
        attribution="installer",
        binary_sha256_pre="f" * 64,
        binary_sha256_post="f" * 64,
    )
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False


@task2a_owned
def test_source_tree_mismatch_against_wheel_expected_is_false(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="wheel",
    )
    receipt = _receipt_obj(attribution="source-tree")
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False


@task2a_owned
def test_census_skipped_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    junit = current / "junit.xml"
    junit.write_text(
        '<testsuite><testcase classname="t" name="n"><skipped/></testcase></testsuite>',
        encoding="utf-8",
    )
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        junit_path=junit,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj()
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "census_skipped"


@task2a_owned
def test_census_extra_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    rust_list = current / "rust.txt"
    rust_list.write_text("rust::extra\nrust::also_extra\n", encoding="utf-8")
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        rust_list_path=rust_list,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj()
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "census_extra"


@task2a_owned
def test_census_duplicate_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    rust_list = current / "rust.txt"
    rust_list.write_text("rust::same\nrust::same\n", encoding="utf-8")
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        rust_list_path=rust_list,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj()
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "census_duplicate"


@task2a_owned
def test_junit_drift_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    junit = current / "junit.xml"
    junit.write_text("<testsuite tests='1'/>", encoding="utf-8")
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        junit_path=junit,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(node_census_digest="0" * 64)
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "junit_drift"


@task2a_owned
def test_rust_list_drift_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    rust_list = current / "rust.txt"
    rust_list.write_text("rust::only\n", encoding="utf-8")
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        rust_list_path=rust_list,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(node_census_digest="0" * 64)
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "rust_list_drift"


@task2a_owned
def test_argv_drift_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    argv = current / "argv.txt"
    argv.write_text("pytest -q\n", encoding="utf-8")
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        argv_path=argv,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(argv_digest="0" * 64)
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "argv_drift"


@task2a_owned
def test_stdout_drift_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    stdout = current / "stdout.txt"
    stdout.write_text("ok\n", encoding="utf-8")
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        stdout_path=stdout,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(output_digest="0" * 64)
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "stdout_drift"


@task2a_owned
def test_stderr_drift_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    stderr = current / "stderr.txt"
    stderr.write_text("err\n", encoding="utf-8")
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        stderr_path=stderr,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(output_digest="0" * 64)
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "stderr_drift"


@task2a_owned
def test_exit_drift_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    exitp = current / "exit.txt"
    exitp.write_text("0\n", encoding="utf-8")
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        exit_path=exitp,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(exit_digest="0" * 64)
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "exit_drift"


@task2a_owned
def test_run_attempt_mismatch_isolated_predicate(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ={**_live_environ(), "GITHUB_RUN_ATTEMPT": "1"},
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(run_attempt="9")
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "run_attempt_mismatch"


@task2a_owned
def test_artifact_namespace_drift_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(artifact_namespace="wrong-ns")
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "artifact_namespace_drift"


@task2a_owned
def test_wrong_job_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(job_name="wrong-job")
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "job_drift"


@task2a_owned
def test_binary_pre_drift_rejected(tmp_path: Path) -> None:
    """Isolate pre-digest predicate: post matches live bytes, pre does not."""
    import hashlib

    current = tmp_path / "run"
    current.mkdir()
    binary = current / "tg.bin"
    binary.write_bytes(b"bin")
    live = hashlib.sha256(binary.read_bytes()).hexdigest()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        binary_path=binary,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(binary_sha256_pre="0" * 64, binary_sha256_post=live)
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "binary_pre_drift"


@task2a_owned
def test_binary_post_drift_rejected(tmp_path: Path) -> None:
    """Real post-execution drift: independent pre capture, mutate, then post capture."""
    import hashlib

    current = tmp_path / "run"
    current.mkdir()
    binary = current / "tg.bin"
    binary.write_bytes(b"bin-pre")
    pre = hashlib.sha256(binary.read_bytes()).hexdigest()

    def _mutate() -> None:
        binary.write_bytes(b"bin-post-mutated")

    _mutate()
    post = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert pre != post, "mutation hook must change bytes for an independent post capture"
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        binary_path=binary,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    # Receipt claims pre==post (no drift), but live post bytes differ from pre.
    receipt = _receipt_obj(binary_sha256_pre=pre, binary_sha256_post=pre)
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "binary_post_drift"


@task2a_owned
def test_workflow_run_id_drift_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(workflow_run_id="99999999999")
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "workflow_run_id_drift"


@task2a_owned
def test_runner_identity_drift_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(runner_identity_sha256="0" * 64)
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "runner_identity_drift"


@task2a_owned
def test_command_digest_drift_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    # Mutate argv_digest as the receipt's binding to the manifest command vector.
    receipt = _receipt_obj(argv_digest="a" * 64)
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") in {"command_digest_drift", "argv_drift", "manifest_command_drift"}


@task2a_owned
def test_attribution_drift_rejected(tmp_path: Path) -> None:
    current = tmp_path / "run"
    current.mkdir()
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=MANIFEST,
        environ=_live_environ(),
        expected_attribution="source-tree",
    )
    receipt = _receipt_obj(attribution="wheel")
    verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    assert verdict.get("ok") is False
    assert verdict.get("reason") == "attribution_drift"


@task2a_owned
def test_scripts_fail_closed_until_live_binding(tmp_path: Path) -> None:
    for rel in (
        "scripts/run_task2a_pytest_nodes.py",
        "scripts/run_task2a_rust_node.py",
        "scripts/verify_task2a_windows_nodes.py",
    ):
        path = REPO_ROOT / rel
        assert path.is_file()
    receipt_out = tmp_path / "should-not-exist.json"
    for script, extra in (
        (
            PY_RUNNER,
            [
                "--manifest",
                str(MANIFEST),
                "--junitxml",
                str(tmp_path / "out.xml"),
                "--receipt-out",
                str(receipt_out),
                "--node-id",
                "python::x",
            ],
        ),
        (
            RUST_RUNNER,
            [
                "--manifest",
                str(MANIFEST),
                "--receipt-out",
                str(receipt_out),
                "--node-id",
                "rust::x",
            ],
        ),
    ):
        if receipt_out.exists():
            receipt_out.unlink()
        proc = subprocess.run(
            [sys.executable, str(script), *extra],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 2
        assert not receipt_out.exists()


@task2a_owned
def test_cargo_selected_binary_exact_target_and_executable(tmp_path: Path) -> None:
    """Behaviorless Cargo JSON selection seam: exact selected target + executable."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_task2a_rust_node", RUST_RUNNER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    exe = tmp_path / "tg"
    exe.write_text("x", encoding="utf-8")
    messages = [
        {
            "reason": "compiler-artifact",
            "package_id": "tensor_grep_rs 0.0.0 (path+file:///rust_core)",
            "target": {"kind": ["bin"], "name": "tg"},
            "executable": str(exe),
        }
    ]
    selected = mod.select_cargo_test_executable(
        messages, package="tensor_grep_rs", selected_kind="bin", selected_name="tg"
    )
    assert selected == exe
    # Filenames must not substitute for an explicit executable field.
    with pytest.raises(LookupError, match="missing"):
        mod.select_cargo_test_executable(
            [
                {
                    "reason": "compiler-artifact",
                    "package_id": "path+file:///rust_core#tensor_grep_rs@0.0.0",
                    "target": {"kind": ["lib"], "name": "tensor_grep_rs"},
                    "filenames": [str(exe)],
                }
            ],
            package="tensor_grep_rs",
            selected_kind="lib",
            selected_name="tensor_grep_rs",
        )
    # Real-shape lib + integration compiler-artifact rows.
    lib_exe = tmp_path / "tensor_grep_rs-abc"
    lib_exe.write_text("lib", encoding="utf-8")
    assert (
        mod.select_cargo_test_executable(
            [
                {
                    "reason": "compiler-artifact",
                    "package_id": "path+file:///x/rust_core#tensor_grep_rs@1.102.1",
                    "target": {"kind": ["lib"], "name": "tensor_grep_rs"},
                    "executable": str(lib_exe),
                }
            ],
            package="tensor_grep_rs",
            selected_kind="lib",
            selected_name="tensor_grep_rs",
        )
        == lib_exe
    )
    test_exe = tmp_path / "task2a_direct_native_round60-abc"
    test_exe.write_text("test", encoding="utf-8")
    assert (
        mod.select_cargo_test_executable(
            [
                {
                    "reason": "compiler-artifact",
                    "package_id": "tensor_grep_rs 1.102.1 (path+file:///rust_core)",
                    "target": {"kind": ["test"], "name": "task2a_direct_native_round60"},
                    "executable": str(test_exe),
                }
            ],
            package="tensor_grep_rs",
            selected_kind="test",
            selected_name="task2a_direct_native_round60",
        )
        == test_exe
    )


@task2a_owned
def test_cargo_selected_binary_missing_duplicate_wrong_executable(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_task2a_rust_node", RUST_RUNNER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    with pytest.raises(LookupError, match="missing"):
        mod.select_cargo_test_executable(
            [], package="tensor_grep_rs", selected_kind="bin", selected_name="tg"
        )
    exe_a = tmp_path / "tg-a"
    exe_b = tmp_path / "tg-b"
    exe_a.write_text("a", encoding="utf-8")
    exe_b.write_text("b", encoding="utf-8")
    dup = [
        {
            "reason": "compiler-artifact",
            "package_id": "tensor_grep_rs 0.0.0 (path+file:///rust_core)",
            "target": {"kind": ["bin"], "name": "tg"},
            "executable": str(exe_a),
        },
        {
            "reason": "compiler-artifact",
            "package_id": "tensor_grep_rs 0.0.0 (path+file:///rust_core)",
            "target": {"kind": ["bin"], "name": "tg"},
            "executable": str(exe_b),
        },
    ]
    with pytest.raises(LookupError, match="duplicate"):
        mod.select_cargo_test_executable(
            dup, package="tensor_grep_rs", selected_kind="bin", selected_name="tg"
        )
    wrong = tmp_path / "not-tg"
    wrong.write_text("x", encoding="utf-8")
    with pytest.raises(LookupError, match="wrong executable"):
        mod.select_cargo_test_executable(
            [
                {
                    "reason": "compiler-artifact",
                    "package_id": "tensor_grep_rs 0.0.0 (path+file:///rust_core)",
                    "target": {"kind": ["bin"], "name": "tg"},
                    "executable": str(wrong),
                }
            ],
            package="tensor_grep_rs",
            selected_kind="bin",
            selected_name="tg",
        )


@task2a_owned
def test_python_runner_refuses_unowned_node(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(PY_RUNNER),
            "--manifest",
            str(MANIFEST),
            "--junitxml",
            str(tmp_path / "j.xml"),
            "--receipt-out",
            str(tmp_path / "r.json"),
            "--node-id",
            "python::tests/unit/test_native_ci_receipt_v1.py::test_not_a_real_owned_node",
        ],
        cwd=str(REPO_ROOT),
        env={**os.environ, "GITHUB_JOB": "native-build-smoke"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "ownership validation refused" in proc.stderr or "unowned" in proc.stderr


@task2a_owned
def test_rust_runner_requires_manifest_ownership_before_list(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(RUST_RUNNER),
            "--manifest",
            str(MANIFEST),
            "--receipt-out",
            str(tmp_path / "r.json"),
            "--node-id",
            "rust::not_in_manifest::missing_node",
        ],
        cwd=str(REPO_ROOT),
        env={**os.environ, "GITHUB_JOB": "native-build-smoke"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "ownership validation refused" in proc.stderr or "unowned" in proc.stderr
