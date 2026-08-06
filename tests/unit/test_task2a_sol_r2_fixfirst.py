"""Sol round-2 FIX-FIRST remaining HIGHs (exact-byte citations).

TDD pins for the 8 remaining Sol complaints after R1. Do not weaken to match
stubs. RETAINED_OK (SDDL/CNG/cap+1/census nodes) are covered elsewhere.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import threading
from pathlib import Path

import pytest
import yaml

from tensor_grep.cli import _win32_path_domain as win32
from tensor_grep.cli import installer_shim_receipt as shim_mod
from tensor_grep.cli import main as main_mod
from tensor_grep.cli import native_ci_receipt as receipt_mod
from tensor_grep.cli import search_input_ledger as ledger_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def task2a_owned(fn):  # type: ignore[no-untyped-def]
    fn._task2a_owned = True  # type: ignore[attr-defined]
    return fn


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@task2a_owned
def test_sol_r2_verify_never_raises_notimplemented_and_ci_wires_emit_verify(
    tmp_path: Path,
) -> None:
    """HIGH#1: verifier is a real fail-closed path; CI must call emit/verify."""
    current = tmp_path / "run"
    current.mkdir()
    receipt = receipt_mod.NativeCiReceiptV1(
        version=1,
        manifest_sha256="aa" * 32,
        commit_sha="deadbeef" * 5,
        workflow_run_id="1",
        run_attempt="1",
        job_name="native-build-smoke",
        runner_identity_sha256="bb" * 32,
        binary_path="tg.exe",
        binary_version="0",
        binary_sha256_pre="cc" * 32,
        binary_sha256_post="cc" * 32,
        node_list=("python::x",),
        node_census_digest="dd" * 32,
        argv_digest="ee" * 32,
        output_digest="ff" * 32,
        exit_digest="11" * 32,
        artifact_namespace="task2a-native-ci/1/1",
        attribution="source-tree",
    )
    # Live tuple present but artifacts incomplete → fail closed dict, never NI.
    source = receipt_mod.ArtifactSource(
        current_run_dir=current,
        manifest_path=REPO_ROOT / "tests" / "fixtures" / "task2a_windows_node_manifest.json",
        environ={
            "GITHUB_SHA": "deadbeef" * 5,
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKFLOW": "CI",
            "GITHUB_JOB": "native-build-smoke",
            "GITHUB_REPOSITORY": "oimiragieo/tensor-grep",
        },
        expected_attribution="source-tree",
    )
    try:
        verdict = receipt_mod.verify_native_ci_receipt(receipt, artifact_source=source)
    except NotImplementedError as exc:  # pragma: no cover - Sol R2 failure mode
        raise AssertionError(
            "verify_native_ci_receipt must not raise NotImplementedError; "
            "fail closed with ok=False reasons instead"
        ) from exc
    assert isinstance(verdict, dict)
    assert verdict.get("ok") is False
    assert verdict.get("reason")

    # CI lanes must invoke the real verify script (not stub-only forever text).
    doc = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    for job_name in ("test-python", "native-build-smoke"):
        steps = [
            s
            for s in doc["jobs"][job_name]["steps"]
            if "Task 2A" in str(s.get("name") or "") and "Upload" not in str(s.get("name") or "")
        ]
        run = str(steps[0].get("run") or "")
        assert "verify_task2a_windows_nodes.py" in run, (
            f"{job_name} must wire scripts/verify_task2a_windows_nodes.py emit/verify path"
        )
        assert "NativeCiReceipt emit and verifier not implemented" not in run
        assert "refusing green CI" in run or "clearance still requires" in run


@task2a_owned
def test_sol_r2_crash_classification_fail_closed(tmp_path: Path) -> None:
    """HIGH#2: unknown/abnormal → crash_or_setup; never fail-open behavioral RED."""
    py = _load_script(
        "run_task2a_pytest_nodes_r2", REPO_ROOT / "scripts" / "run_task2a_pytest_nodes.py"
    )
    rust = _load_script("run_task2a_rust_node_r2", REPO_ROOT / "scripts" / "run_task2a_rust_node.py")

    # Exit 2 with empty/missing junit case must NOT be behavioral RED.
    missing = tmp_path / "missing.xml"
    missing.write_text("<testsuite tests='0'></testsuite>\n", encoding="utf-8")
    phase = py.classify_pytest_node_phase(
        junit_path=missing,
        pytest_nodeid="tests/unit/test_x.py::test_leaf",
        exit_code=2,
    )
    assert phase == "crash_or_setup"

    # Exit 1 with neither <failure> nor <error> is abnormal → crash_or_setup.
    bare = tmp_path / "bare.xml"
    bare.write_text(
        """<?xml version="1.0"?>
<testsuite tests="1" errors="0" failures="0">
  <testcase classname="tests.unit.test_x" name="test_leaf"/>
</testsuite>
""",
        encoding="utf-8",
    )
    assert (
        py.classify_pytest_node_phase(
            junit_path=bare,
            pytest_nodeid="tests/unit/test_x.py::test_leaf",
            exit_code=1,
        )
        == "crash_or_setup"
    )

    # Rust: abnormal exit without assertion markers → crash_or_setup (fail-closed).
    assert (
        rust.classify_rust_node_phase(exit_code=101, stdout="", stderr="something odd")
        == "crash_or_setup"
    )
    # Positive control: assertion failure remains behavioral RED.
    assert (
        rust.classify_rust_node_phase(
            exit_code=101,
            stdout="test leaf ... FAILED\n",
            stderr="assertion `left == right` failed",
        )
        == "executed_refused_receipt"
    )


@task2a_owned
def test_sol_r2_pcre2_oracle_uses_production_route_hook() -> None:
    """HIGH#3: PCRE2 oracle must call production construction path (not hardcoded bool)."""
    src = (REPO_ROOT / "rust_core" / "src" / "native_search.rs").read_text(encoding="utf-8")
    assert "fn gate_uninstrumented_pcre2_native_route" in src
    assert "search_input_limit" in src
    oracle_body = src.split("fn task2a_observe_pcre2_native_refusal")[1].split("\n}\n")[0]
    assert "gate_uninstrumented_pcre2_native_route" in oracle_body, (
        "PCRE2 oracle must exercise production gate route, not a hardcoded bool"
    )
    # Hardcoded `return false` / `false` alone is forbidden as the oracle body.
    stripped = "\n".join(
        ln for ln in oracle_body.splitlines() if "gate_uninstrumented" not in ln and "=>" not in ln
    )
    assert "return false" not in stripped


@task2a_owned
def test_sol_r2_heartbeat_factory_mints_nonce_and_refuses_framing_garbage() -> None:
    """HIGH#4: factory mints nonce; parser refuses pre/post framing garbage."""
    minted = win32.mint_writer_nonce()
    assert isinstance(minted, (bytes, bytearray))
    assert len(minted) >= 16

    # Default create path mints when caller omits writer_nonce (factory authority).
    monkey_nonce = b"\x11" * 16
    # Parse must refuse framing garbage around an otherwise-valid heartbeat.
    pid = 4242
    good = win32.descendant_job_pipe_heartbeat(pid, writer_nonce=monkey_nonce)
    with pytest.raises(ValueError, match=r"framing|garbage|exact"):
        win32.parse_descendant_job_pipe_heartbeat_pid(
            b"PRE" + good, writer_nonce=monkey_nonce
        )
    with pytest.raises(ValueError, match=r"framing|garbage|exact"):
        win32.parse_descendant_job_pipe_heartbeat_pid(
            good + b"POST", writer_nonce=monkey_nonce
        )
    assert (
        win32.parse_descendant_job_pipe_heartbeat_pid(good, writer_nonce=monkey_nonce) == pid
    )


@task2a_owned
def test_sol_r2_default_job_cleanup_uses_real_default_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HIGH#5: prove default factory path without double-mocking factory+closer."""
    closed_via_production: list[int] = []

    def _spy_close(handle: int) -> None:
        closed_via_production.append(handle)

    monkeypatch.setattr(win32, "close_handle", _spy_close)
    monkeypatch.setattr(win32, "IS_WINDOWS", True)

    # Real default factory object — do NOT replace windows_job_factory_primitives
    # with a wholly separate recording factory instance.
    factory = win32.windows_job_factory_primitives()
    assert type(factory).__name__ in {
        "DefaultJobFactoryPrimitives",
        "WindowsJobFactoryPrimitives",
    }
    acquired: list[int] = []
    next_handle = {"n": 100}

    def _alloc() -> int:
        next_handle["n"] += 1
        h = next_handle["n"]
        acquired.append(h)
        return h

    def _create_job(self) -> int:  # noqa: ANN001
        return _alloc()

    def _create_proc(self, **kwargs):  # noqa: ANN001
        _ = kwargs
        ph, th = _alloc(), _alloc()
        return win32.ProcessThreadHandles(process_handle=ph, thread_handle=th, pid=1000 + ph)

    def _setup_pipe(self, **kwargs):  # noqa: ANN001
        _ = kwargs
        ph, th = _alloc(), _alloc()
        return win32.ProcessThreadHandles(process_handle=ph, thread_handle=th, pid=2000 + ph)

    monkeypatch.setattr(type(factory), "create_job", _create_job)
    monkeypatch.setattr(type(factory), "create_process_suspended", _create_proc)
    monkeypatch.setattr(type(factory), "assign_process_to_job", lambda self, j, p: None)
    monkeypatch.setattr(type(factory), "resume_thread", lambda self, t: None)
    monkeypatch.setattr(type(factory), "query_process_image", lambda self, p: "img")
    monkeypatch.setattr(type(factory), "setup_pipe_worker", _setup_pipe)
    monkeypatch.setattr(type(factory), "terminate_process", lambda self, p: None)
    # Default factory path: factory=None resolves via windows_job_factory_primitives().
    # Minted nonce path (caller omits writer_nonce).
    with pytest.raises(BaseException, match="injected fault"):
        win32.create_suspended_job_with_descendant_breakaway(
            canary_event=threading.Event(),
            inject_fault_after="pipe_worker_setup",
            factory=None,
            writer_nonce=None,
        )
    assert acquired, "premise: default factory acquired handles"
    assert closed_via_production == list(reversed(acquired))


@task2a_owned
def test_sol_r2_create_transaction_inside_try_no_leak_on_record() -> None:
    """HIGH#6: create_transaction must be inside try so record() cannot leak txn."""
    closed_txns: list[int] = []

    class _Txr:
        def __init__(self) -> None:
            self.created = False

        def create_transaction(self) -> int:
            self.created = True
            return 7

        def transacted_registry_open(self, transaction: int, key_path: str) -> int:
            _ = transaction, key_path
            return 8

        def transacted_registry_write(self, key_handle: int, value: str) -> None:
            _ = key_handle, value

        def commit_transaction(self, transaction: int) -> None:
            _ = transaction

        def rollback_transaction(self, transaction: int) -> None:
            _ = transaction

        def close_registry_key(self, key_handle: int) -> None:
            _ = key_handle

        def close_transaction(self, transaction: int) -> None:
            closed_txns.append(transaction)

    class _BoomLog(shim_mod.PrimitiveCallLog):
        def record(self, name: str, *args: object) -> None:
            if name == "CreateTransaction":
                raise RuntimeError("record boom after create")
            super().record(name, *args)

    txr = _Txr()
    log = _BoomLog()
    with pytest.raises(RuntimeError, match="record boom"):
        shim_mod.mutate_user_path_txr_only(
            path_preimage="pre",
            intended_image="post",
            remove_token_identity="tok",
            txr=txr,  # type: ignore[arg-type]
            call_log=log,
        )
    assert txr.created is True
    assert closed_txns == [7], f"txn must close despite record() raise; closed={closed_txns!r}"


@task2a_owned
def test_sol_r2_child_start_after_search_not_pipeline_ctor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """HIGH#7: child_start only after actual search/backend start, not Pipeline()."""
    order: list[str] = []

    def _hook(kind: str, argv: list[str]) -> None:
        _ = argv
        order.append(f"hook:{kind}")

    class _Backend:
        def search(self, *args, **kwargs):  # noqa: ANN002, ANN003
            order.append("search")
            raise RuntimeError("observed search start")

        def search_many(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return self.search(*args, **kwargs)

        def search_passthrough(self, *args, **kwargs):  # noqa: ANN002, ANN003
            _ = args, kwargs
            raise AssertionError("passthrough must not run")

    class _FakePipeline:
        selected_backend_name = "CPUBackend"
        selected_backend_reason = "force_cpu"
        selected_gpu_device_ids: list[int] = []
        selected_gpu_chunk_plan_mb: list[int] = []
        fallback_reason = None

        def __init__(self, *args, **kwargs) -> None:
            _ = args, kwargs
            order.append("pipeline_ctor")

        def get_backend(self) -> _Backend:
            order.append("get_backend")
            return _Backend()

    import tensor_grep.core.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "Pipeline", _FakePipeline)
    monkeypatch.setattr(main_mod, "_CHILD_START_HOOK", _hook)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("needle\n", encoding="utf-8")
    # Drive the full_cli CPU path with a tiny argv.
    from tensor_grep.cli import bootstrap as bootstrap_mod

    monkeypatch.setattr(sys, "argv", ["tg", "search", "needle", str(root), "--cpu"])
    try:
        with pytest.raises(RuntimeError, match="observed search start"):
            # Invoke search entry that constructs Pipeline then searches.
            from contextlib import redirect_stderr, redirect_stdout

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                bootstrap_mod._run_full_cli()
    except SystemExit:
        pass
    assert "pipeline_ctor" in order
    assert "hook:cpu" in order
    assert order.index("pipeline_ctor") < order.index("hook:cpu")
    assert order.index("hook:cpu") > order.index("get_backend"), (
        f"hook must not fire at Pipeline ctor alone; order={order!r}"
    )
    # Emit is allowed immediately before the search call (actual backend start),
    # but never at Pipeline() construction.
    assert "search" in order
    assert order.index("search") > order.index("pipeline_ctor")


@task2a_owned
def test_sol_r2_multi_pattern_file_threads_aggregate_ledger(tmp_path: Path) -> None:
    """HIGH#8: public multi -f must thread aggregate no-refund ledger across files."""
    ledger = ledger_mod.SearchInputLedger()
    # Four at-cap files fill the 4 MiB aggregate; a fifth byte must refuse on
    # aggregate (each file alone is within the 1 MiB per-file cap).
    paths: list[Path] = []
    for i in range(4):
        p = tmp_path / f"f{i}.txt"
        p.write_bytes(b"x" * ledger_mod.MAX_PATTERN_OR_IGNORE_FILE_BYTES)
        paths.append(p)
        ledger_mod.read_pattern_or_ignore_file_bounded(p, ledger=ledger)
    assert ledger.decoded_bytes == ledger_mod.MAX_COMBINED_DECODED_BYTES
    fifth = tmp_path / "f4.txt"
    fifth.write_bytes(b"y")
    with pytest.raises(ledger_mod.SearchInputLimitExceeded) as ei:
        ledger_mod.read_pattern_or_ignore_file_bounded(fifth, ledger=ledger)
    assert ei.value.limit == ledger_mod.MAX_COMBINED_DECODED_BYTES

    # Public CLI helper must pass one shared ledger across -f files.
    src = Path(main_mod.__file__).read_text(encoding="utf-8")
    assert "def _read_patterns_from_file_list" in src
    body = src.split("def _read_patterns_from_file_list", 1)[1].split("\ndef ", 1)[0]
    assert "SearchInputLedger" in body
    assert "ledger=" in body or "ledger =" in body
