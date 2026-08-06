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

    # Parse + census derive must be real (not NotImplemented shells).
    assert not isinstance(
        getattr(receipt_mod.parse_native_ci_receipt, "__doc__", ""), NotImplementedError
    )
    parse_src = Path(receipt_mod.__file__).read_text(encoding="utf-8")
    for name in ("def parse_native_ci_receipt", "def derive_junit_population", "def derive_rust_list_census"):
        assert name in parse_src
    parse_body = parse_src.split("def parse_native_ci_receipt", 1)[1].split("\ndef ", 1)[0]
    assert "raise NotImplementedError" not in parse_body
    junit_body = parse_src.split("def derive_junit_population", 1)[1].split("\ndef ", 1)[0]
    assert "raise NotImplementedError" not in junit_body
    rust_body = parse_src.split("def derive_rust_list_census", 1)[1].split("\ndef ", 1)[0]
    assert "raise NotImplementedError" not in rust_body

    # Runners may emit receipts (refusing forever is forbidden).
    for rel in (
        "scripts/run_task2a_pytest_nodes.py",
        "scripts/run_task2a_rust_node.py",
    ):
        runner_src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "write_receipt" in runner_src
        assert "NativeCiReceiptV1 emit not implemented" not in runner_src

    # CI lanes must invoke the real verify script; exit must follow verify_rc
    # (not an unconditional forever-stub exit 1 after verify).
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
        assert "VERIFY_RC" in run
        # Unconditional `exit 1` after verify (ignoring VERIFY_RC) is forbidden.
        assert "exit \"$VERIFY_RC\"" in run or "exit $VERIFY_RC" in run or (
            "if [ \"$VERIFY_RC\"" in run and "exit" in run
        )
        # Sol R4 HIGH#1: verify must receive census artifact paths the runners produce.
        # Omitting --junit/--rust-list forever-stubs artifact_incomplete even when
        # Actions tuple + receipt + junit.xml / rust-list.txt are present.
        assert "--junit" in run, (
            f"{job_name} verify must pass --junit (runners write NODE_DIR/junit.xml)"
        )
        if job_name == "native-build-smoke":
            assert "--rust-list" in run, (
                "native-build-smoke verify must pass --rust-list "
                "(runners write NODE_DIR/rust-list.txt)"
            )
        assert "junit.xml" in run
        if job_name == "native-build-smoke":
            assert "rust-list.txt" in run


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
    # Sol R4 HIGH#2: bare "panicked at" (no real assertion marker) = crash_or_setup.
    assert (
        rust.classify_rust_node_phase(
            exit_code=101,
            stdout="",
            stderr="thread 'tests::leaf' panicked at src/x.rs:1:1:\ncapacity overflow",
        )
        == "crash_or_setup"
    )
    # Sol R4 HIGH#2: generic cargo "FAILED" text is NOT assertion evidence.
    # Ordinary panic + "... FAILED" must stay crash_or_setup (not behavioral RED).
    assert (
        rust.classify_rust_node_phase(
            exit_code=101,
            stdout="test leaf ... FAILED\n",
            stderr="thread 'tests::leaf' panicked at src/x.rs:1:1:\ncapacity overflow",
        )
        == "crash_or_setup"
    )
    # Positive control: real assertion markers + "panicked at" → executed_refused_receipt.
    assert (
        rust.classify_rust_node_phase(
            exit_code=101,
            stdout="test leaf ... FAILED\n",
            stderr=(
                "thread 'tests::leaf' panicked at src/x.rs:1:1:\n"
                "assertion `left == right` failed"
            ),
        )
        == "executed_refused_receipt"
    )
    # Classifier source must not treat bare "FAILED" as an assertion marker.
    rust_src = (REPO_ROOT / "scripts" / "run_task2a_rust_node.py").read_text(encoding="utf-8")
    markers_body = rust_src.split("assertion_markers", 1)[1].split(")", 1)[0]
    assert '"FAILED"' not in markers_body and "'FAILED'" not in markers_body, (
        "generic FAILED must not be assertion evidence (misclassifies ordinary panicked-at)"
    )


@task2a_owned
def test_sol_r2_pcre2_oracle_uses_production_route_hook() -> None:
    """HIGH#3: PCRE2 gate must be called from production native search path."""
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
    # Production path: run_native_search must call the gate (not oracle-only).
    run_body = src.split("pub fn run_native_search", 1)[1].split("\npub fn ", 1)[0]
    assert "gate_uninstrumented_pcre2_native_route" in run_body, (
        "PCRE2 production gate must be invoked from run_native_search, not only the test oracle"
    )
    # Sol R4: CLI builders must thread pcre2 into NativeSearchConfig (positional/command/gpu).
    # A gate call with config.pcre2 forever-default-false is a vacuous production door.
    # Anchor on the production signatures (paren after name) — not cfg(test) helpers
    # whose names start with the same prefix.
    main_src = (REPO_ROOT / "rust_core" / "src" / "main.rs").read_text(encoding="utf-8")
    cmd_marker = "fn native_search_config_for_command(\n"
    pos_marker = "fn native_search_config_for_positional(\n"
    gpu_marker = "fn native_search_config_for_gpu_params(\n"
    assert cmd_marker in main_src, "production native_search_config_for_command missing"
    assert pos_marker in main_src, "production native_search_config_for_positional missing"
    assert gpu_marker in main_src, "production native_search_config_for_gpu_params missing"
    cmd_body = main_src.split(cmd_marker, 1)[1].split("\nfn ", 1)[0]
    pos_body = main_src.split(pos_marker, 1)[1].split("\nfn ", 1)[0]
    gpu_body = main_src.split(gpu_marker, 1)[1].split("\nfn ", 1)[0]
    assert "pcre2: args.pcre2" in cmd_body, (
        "native_search_config_for_command must thread args.pcre2 into NativeSearchConfig "
        "(not leave Default pcre2:false — vacuous gate)"
    )
    assert "pcre2: cli.pcre2" in pos_body, (
        "native_search_config_for_positional must thread cli.pcre2 into NativeSearchConfig"
    )
    assert "pcre2: params.pcre2" in gpu_body, (
        "native_search_config_for_gpu_params must thread params.pcre2 into NativeSearchConfig"
    )
    # Behavioral pin in native_search.rs: run_native_search({pcre2:true}) must refuse.
    assert "fn run_native_search_refuses_pcre2_before_matcher" in src, (
        "must ship a non-ignored behavioral test that run_native_search refuses pcre2:true"
    )
    refuse_body = src.split("fn run_native_search_refuses_pcre2_before_matcher", 1)[1].split(
        "\n    #[", 1
    )[0]
    assert "pcre2: true" in refuse_body
    assert "search_input_limit" in refuse_body


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
    """HIGH#5 / Sol R5: descendant inherits pipe and writes OWN pid+nonce heartbeat.

    Parent must NOT fabricate os.write / canary_event.set. Vacuous parent-written
    payloads are refused. Adversarial withhold (child never executes) proves no
    valid heartbeat or event appears.
    """
    import inspect
    import os
    import time

    # Production default factory must wire real Win32 (not NotImplemented stubs).
    src = inspect.getsource(win32.DefaultJobFactoryPrimitives)
    assert "CreateJobObjectW" in src or "CreateJobObject" in src
    assert "CreateProcessW" in src
    create_job_body = src.split("def create_job", 1)[1].split("\n    def ", 1)[0]
    assert "raise NotImplementedError" not in create_job_body
    pipe_body = src.split("def setup_pipe_worker", 1)[1].split("\n    def ", 1)[0]
    # Sol R4: must not discard parent/pipe/event/nonce with a blanket `_ = ...`.
    assert "_ = parent, canary_event, canary_pipe_write_fd, writer_nonce" not in pipe_body
    assert "writer_nonce" in pipe_body and "parent" in pipe_body
    # Sol R5: parent must not fabricate heartbeat write or event signal.
    assert "os.write(canary_pipe_write_fd" not in pipe_body, (
        "setup_pipe_worker must not parent-write the canary pipe (descendant owns heartbeat)"
    )
    assert "canary_event.set()" not in pipe_body, (
        "setup_pipe_worker must not parent-signal canary_event (descendant owns signal)"
    )
    assert "create_process_suspended" in pipe_body
    create_susp = src.split("def create_process_suspended", 1)[1].split("\n    def ", 1)[0]
    assert "writer_nonce" in create_susp, (
        "create_process_suspended must accept writer_nonce for pipe-worker descendants"
    )
    assert "spawn_descendant_job_pipe_heartbeat_writer" in create_susp, (
        "writer_nonce path must spawn a descendant pipe heartbeat writer "
        "(not suspended inert cmd.exe with parent-forged heartbeat)"
    )

    closed_via_production: list[int] = []

    def _spy_close(handle: int) -> None:
        closed_via_production.append(handle)

    monkeypatch.setattr(win32, "close_handle", _spy_close)
    monkeypatch.setattr(win32, "IS_WINDOWS", True)

    acquired: list[int] = []
    nxt = {"n": 100}
    pids = {"n": 1000}

    def _alloc() -> int:
        nxt["n"] += 1
        acquired.append(nxt["n"])
        return nxt["n"]

    def _pid() -> int:
        pids["n"] += 1
        return pids["n"]

    # Happy path: REAL DefaultJobFactoryPrimitives.setup_pipe_worker (no subclass override).
    real_factory = win32.DefaultJobFactoryPrimitives()
    assert type(real_factory) is win32.DefaultJobFactoryPrimitives
    r_fd, w_fd = os.pipe()
    worker_handles: win32.ProcessThreadHandles | None = None
    try:
        parent = win32.ProcessThreadHandles(process_handle=11, thread_handle=12, pid=7001)
        nonce = b"\xab" * 16
        ev = threading.Event()
        worker_handles = real_factory.setup_pipe_worker(
            parent=parent,
            canary_event=ev,
            canary_pipe_write_fd=w_fd,
            writer_nonce=nonce,
        )
        worker = worker_handles
        # Parent must not have signaled the event (descendant owns heartbeat/signal).
        assert ev.is_set() is False, (
            "parent must not set canary_event; fabrication refused"
        )
        # Non-blocking drain (select() does not accept pipe fds on Windows).
        os.set_blocking(r_fd, False)
        deadline = time.monotonic() + 5.0
        payload = b""
        while time.monotonic() < deadline:
            try:
                chunk = os.read(r_fd, 4096)
            except BlockingIOError:
                chunk = b""
            if chunk:
                payload += chunk
                break
            time.sleep(0.01)
        assert payload, "descendant must write heartbeat to inherited pipe"
        parsed = win32.parse_descendant_job_pipe_heartbeat_pid(payload, writer_nonce=nonce)
        assert parsed == worker.pid
        assert worker.pid != parent.pid
    finally:
        if worker_handles is not None:
            try:
                real_factory.terminate_process(worker_handles.process_handle)
            except Exception:
                try:
                    win32.terminate_spawned_descendant(worker_handles.pid)
                except Exception:
                    pass
            for h in (worker_handles.process_handle, worker_handles.thread_handle):
                try:
                    real_factory.close_handle(h)
                except Exception:
                    pass
        try:
            os.close(r_fd)
        except OSError:
            pass
        if w_fd >= 0:
            try:
                os.close(w_fd)
            except OSError:
                pass

    # --- Adversarial oracle: withhold child execution → no heartbeat / event ---
    class _WithholdChildFactory(win32.DefaultJobFactoryPrimitives):
        def create_job(self) -> int:
            return _alloc()

        def create_process_suspended(self, **kwargs):  # type: ignore[no-untyped-def]
            # Return handles WITHOUT spawning a writer — withhold descendant execution.
            _ = kwargs
            return win32.ProcessThreadHandles(
                process_handle=_alloc(), thread_handle=_alloc(), pid=_pid()
            )

        def assign_process_to_job(self, job_handle: int, process_handle: int) -> None:
            _ = job_handle, process_handle

        def resume_thread(self, thread_handle: int) -> None:
            _ = thread_handle

        def query_process_image(self, process_handle: int) -> str:
            _ = process_handle
            return "img"

        def terminate_process(self, process_handle: int) -> None:
            _ = process_handle

    withhold = _WithholdChildFactory()
    assert "setup_pipe_worker" not in type(withhold).__dict__
    r2, w2 = os.pipe()
    try:
        parent = win32.ProcessThreadHandles(process_handle=21, thread_handle=22, pid=8001)
        nonce = b"\xcd" * 16
        ev2 = threading.Event()
        worker = withhold.setup_pipe_worker(
            parent=parent,
            canary_event=ev2,
            canary_pipe_write_fd=w2,
            writer_nonce=nonce,
        )
        assert worker.pid != parent.pid
        os.set_blocking(r2, False)
        deadline = time.monotonic() + 0.3
        forged = b""
        while time.monotonic() < deadline:
            try:
                chunk = os.read(r2, 4096)
            except BlockingIOError:
                chunk = b""
            if chunk:
                forged += chunk
                break
            time.sleep(0.01)
        assert forged == b"", (
            f"withheld child must yield empty pipe; parent forged {forged!r}"
        )
        assert ev2.is_set() is False, (
            "withheld child must not leave canary_event set (parent must not signal)"
        )
        with pytest.raises(ValueError):
            win32.parse_descendant_job_pipe_heartbeat_pid(forged, writer_nonce=nonce)
    finally:
        os.close(r2)
        os.close(w2)

    # --- Cleanup arm: alloc-only stubs (no live spawn) prove close_handle order ---
    class _AllocOnlyFactory(win32.DefaultJobFactoryPrimitives):
        def create_job(self) -> int:
            return _alloc()

        def create_process_suspended(self, **kwargs):  # type: ignore[no-untyped-def]
            _ = kwargs
            return win32.ProcessThreadHandles(
                process_handle=_alloc(), thread_handle=_alloc(), pid=_pid()
            )

        def assign_process_to_job(self, job_handle: int, process_handle: int) -> None:
            _ = job_handle, process_handle

        def resume_thread(self, thread_handle: int) -> None:
            _ = thread_handle

        def query_process_image(self, process_handle: int) -> str:
            _ = process_handle
            return "img"

        def terminate_process(self, process_handle: int) -> None:
            _ = process_handle

    factory = _AllocOnlyFactory()
    assert "setup_pipe_worker" not in type(factory).__dict__
    acquired.clear()
    closed_via_production.clear()
    canary = threading.Event()
    fault_r, fault_w = os.pipe()
    try:
        with pytest.raises(BaseException, match="injected fault"):
            win32.create_suspended_job_with_descendant_breakaway(
                canary_event=canary,
                canary_pipe_write_fd=fault_w,
                inject_fault_after="pipe_worker_setup",
                factory=factory,
                writer_nonce=b"\xef" * 16,
            )
    finally:
        os.close(fault_r)
        os.close(fault_w)
    assert acquired, "premise: factory acquired handles"
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

    from tensor_grep.core.result import SearchResult

    class _Backend:
        def search(self, *args, **kwargs):  # noqa: ANN002, ANN003
            order.append("search")
            return SearchResult(matches=[], total_files=0, total_matches=0)

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
    # HIGH#7 Sol R3: emit AFTER search begins/returns — never pre-start.
    assert "search" in order
    assert order.index("search") > order.index("pipeline_ctor")
    assert order.index("hook:cpu") > order.index("search"), (
        f"child_start must emit AFTER search begins; order={order!r}"
    )


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
    # HIGH#8 Sol R3: -f admission must run before rg passthrough early-exit.
    # Locate the first can_passthrough_rg early-exit and require ledger/-f read before it.
    passthrough_idx = src.find("if can_passthrough_rg:")
    assert passthrough_idx > 0
    before = src[:passthrough_idx]
    assert (
        "_read_patterns_from_file_list" in before
        or "read_pattern_or_ignore_file_bounded" in before
        or "_admit_pattern_files" in before
    ), "-f files must go through ledger before rg passthrough exit"

    # Rust aggregate file-count admission (not bytes-only).
    rust_src = (REPO_ROOT / "rust_core" / "src" / "main.rs").read_text(encoding="utf-8")
    assert "MAX_COMBINED_PATTERN" in rust_src or "MAX_COMBINED_PATTERN_IGNORE_FILES" in rust_src
    resolve_body = rust_src.split("fn resolve_search_request_with_stdin", 1)[1].split(
        "\nfn ", 1
    )[0]
    assert "file_count" in resolve_body or "aggregate_files" in resolve_body or "pattern_file_count" in resolve_body, (
        "rust multi -f path must admit aggregate file-count, not only decoded bytes"
    )
