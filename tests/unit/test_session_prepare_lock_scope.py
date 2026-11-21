import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest

from tensor_grep.cli import session_resume_service as service
from tensor_grep.cli.session_store import SessionStaleError, get_session, open_session


def _install_surface(monkeypatch, tmp_path: Path, *, current_tag: str):
    state = {"locked": False, "wrote_locked": False}
    initial = {"session_provenance": {"tag": "initial"}, "snapshot": []}
    session_path = tmp_path / "session.json"
    session_path.write_text("{}", encoding="utf-8")

    @contextmanager
    def lock(_path):
        state["locked"] = True
        try:
            yield
        finally:
            state["locked"] = False

    def build(**_kwargs):
        assert state["locked"] is False
        return object()

    def write(_root, _path, _payload, **_kwargs):
        state["wrote_locked"] = state["locked"]

    monkeypatch.setattr(service, "_session_root_for_payload", lambda *_args: tmp_path)
    monkeypatch.setattr(service, "_session_payload_path", lambda *_args: session_path)
    monkeypatch.setattr(service, "_load_session_payload", lambda *_args: dict(initial))
    monkeypatch.setattr(
        service,
        "get_session",
        lambda *_args: {"session_provenance": {"tag": current_tag}, "snapshot": []},
    )
    monkeypatch.setattr(service, "index_lock", lock)
    monkeypatch.setattr(service, "build_prepare_snapshot", build)
    monkeypatch.setattr(service, "asdict", lambda _snapshot: {"query": "needle"})
    monkeypatch.setattr(service, "seal_session", lambda *_args: None)
    monkeypatch.setattr(service, "atomic_write_bytes_confined", write)
    return state


def test_session_prepare_builds_outside_index_lock_and_publishes_inside(
    tmp_path, monkeypatch
) -> None:
    state = _install_surface(monkeypatch, tmp_path, current_tag="initial")

    result = service.session_prepare("session", "needle", str(tmp_path))

    assert result["query"] == "needle"
    assert state["wrote_locked"] is True


def test_session_prepare_refuses_concurrent_payload_change(tmp_path, monkeypatch) -> None:
    state = _install_surface(monkeypatch, tmp_path, current_tag="newer")

    with pytest.raises(SessionStaleError, match="changed while preparing"):
        service.session_prepare("session", "needle", str(tmp_path))

    assert state["wrote_locked"] is False


def test_session_prepare_refuses_byte_identical_session_store_swap(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "sample.py").write_text("value = 1\n", encoding="utf-8")
    opened = open_session(str(project), max_repo_files=10)
    root = Path(opened.root)
    sessions = root / ".tensor-grep" / "sessions"
    replacement = sessions.with_name("sessions-replacement")
    original = sessions.with_name("sessions-original")
    shutil.copytree(sessions, replacement)

    def build(**_kwargs):
        sessions.rename(original)
        replacement.rename(sessions)
        return object()

    monkeypatch.setattr(service, "build_prepare_snapshot", build)
    monkeypatch.setattr(service, "asdict", lambda _snapshot: {"query": "needle"})

    with pytest.raises(SessionStaleError, match="store changed while preparing"):
        service.session_prepare(opened.session_id, "needle", str(project))

    assert "last_prepare" not in get_session(opened.session_id, str(project))
    original_payload = json.loads(
        (original / f"{opened.session_id}.json").read_text(encoding="utf-8")
    )
    assert "last_prepare" not in original_payload


def test_session_prepare_builds_from_authenticated_session_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    scope = project / "src"
    scope.mkdir(parents=True)
    (project / ".git").mkdir()
    (scope / "sample.py").write_text("value = 1\n", encoding="utf-8")
    opened = open_session(str(scope), max_repo_files=10)
    build_paths: list[str] = []

    def build(**kwargs):
        build_paths.append(kwargs["path"])
        kwargs["_input_validator"]()
        return object()

    monkeypatch.setattr(service, "build_prepare_snapshot", build)
    monkeypatch.setattr(service, "asdict", lambda _snapshot: {"query": "needle"})

    service.session_prepare(opened.session_id, "needle", str(project))

    assert build_paths == [str(scope.resolve())]


@pytest.mark.parametrize(
    "original_consumer",
    [None, "def unrelated():\n    return None\n"],
    ids=("added-path", "same-path-changed-content"),
)
def test_session_prepare_uses_signed_file_universe_for_temporary_project_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original_consumer: str | None,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "billing.py").write_text(
        "def calculate_late_fee(balance, days_late):\n"
        "    return balance * days_late\n\n"
        "def apply_late_fee(account):\n"
        "    return calculate_late_fee(account['balance'], account['days_late'])\n\n"
        "def process_billing_cycle(accounts):\n"
        "    return [apply_late_fee(account) for account in accounts]\n",
        encoding="utf-8",
    )
    for name in ("z_padding.py", "zz_padding.py"):
        (project / name).write_text("value = 1\n", encoding="utf-8")
    if original_consumer is not None:
        (project / "consumer.py").write_text(original_consumer, encoding="utf-8")

    opened = open_session(str(project), max_repo_files=10)
    payload = get_session(opened.session_id, str(project))
    assert payload["repo_map"]["scan_limit"]["possibly_truncated"] is False

    replacement = tmp_path / "project-replacement"
    parked = tmp_path / "project-original"
    shutil.copytree(project, replacement)
    (replacement / "consumer.py").write_text(
        "from billing import calculate_late_fee, apply_late_fee, process_billing_cycle\n\n"
        "def temporary_consumer(account):\n"
        "    calculate_late_fee(account['balance'], account['days_late'])\n"
        "    apply_late_fee(account)\n"
        "    return process_billing_cycle([account])\n",
        encoding="utf-8",
    )

    real_build = service.build_prepare_snapshot

    def build(**kwargs):
        input_validator = kwargs.pop("_input_validator")
        project.rename(parked)
        replacement.rename(project)
        try:
            snapshot = real_build(**kwargs, _input_validator=None)
            assert any(
                Path(str(caller.get("file"))).name == "consumer.py"
                for caller in snapshot.blast_radius_floor.get("top_callers", [])
            ) is (original_consumer is not None), snapshot.blast_radius_floor
        finally:
            project.rename(replacement)
            parked.rename(project)
        input_validator()
        return snapshot

    monkeypatch.setattr(service, "build_prepare_snapshot", build)

    if original_consumer is None:
        prepared = service.session_prepare(
            opened.session_id,
            "process_billing_cycle",
            str(project),
        )
        assert not any(
            Path(str(caller.get("file"))).name == "consumer.py"
            for caller in prepared["blast_radius_floor"].get("top_callers", [])
        )
    else:
        with pytest.raises(SessionStaleError, match="project contents changed while preparing"):
            service.session_prepare(
                opened.session_id,
                "process_billing_cycle",
                str(project),
            )
        assert "last_prepare" not in get_session(opened.session_id, str(project))

    monkeypatch.setattr(service, "build_prepare_snapshot", real_build)
    if original_consumer is None:
        shutil.copy(replacement / "consumer.py", project / "consumer.py")
        added = service.session_prepare(opened.session_id, "process_billing_cycle", str(project))
        assert not any(
            Path(str(caller.get("file"))).name == "consumer.py"
            for caller in added["blast_radius_floor"].get("top_callers", [])
        )
        (project / "consumer.py").unlink()

    stable = service.session_prepare(
        opened.session_id,
        "process_billing_cycle",
        str(project),
    )
    assert stable["session_id"] == opened.session_id
    assert not any(
        Path(str(caller.get("file"))).name == "consumer.py"
        for caller in stable["blast_radius_floor"].get("top_callers", [])
    )


@pytest.mark.parametrize("replacement_mode", ["changed", "missing"])
def test_session_prepare_authenticates_direct_source_reads_and_cache_hits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_mode: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "billing.py"
    source.write_text(
        "def process_billing_cycle(accounts):\n    return list(accounts)\n",
        encoding="utf-8",
    )
    opened = open_session(str(project), max_repo_files=10)

    replacement = tmp_path / "project-replacement"
    parked = tmp_path / "project-original"
    shutil.copytree(project, replacement)
    if replacement_mode == "changed":
        (replacement / "billing.py").write_text(
            "def process_billing_cycle(accounts):\n"
            "    transient_source_marker = True\n"
            "    return list(accounts)\n",
            encoding="utf-8",
        )
    else:
        (replacement / "billing.py").unlink()
    real_build = service.build_prepare_snapshot
    from tensor_grep.cli import lang_registry, repo_map
    from tensor_grep.cli.symbols_cache import content_receipt_key

    # Prime the process-global source cache with the signed bytes. Receipt collection must
    # bypass this cached value or the changed replacement would be invisible.
    repo_map._clear_all_source_caches()
    assert "process_billing_cycle" in repo_map._read_source_text_cached(str(source))

    real_read_source = lang_registry.read_source_text
    transient_reads: list[str] = []

    def read_source(path: Path) -> str:
        text = real_read_source(path)
        if "transient_source_marker" in text:
            transient_reads.append(str(path))
        return text

    monkeypatch.setattr(lang_registry, "read_source_text", read_source)
    captured_receipts: dict[str, str] = {}
    real_require_inputs = service._require_authenticated_prepare_inputs

    def require_inputs(snapshot, receipts, **kwargs):
        captured_receipts.update(receipts)
        return real_require_inputs(snapshot, receipts, **kwargs)

    monkeypatch.setattr(service, "_require_authenticated_prepare_inputs", require_inputs)

    def build(**kwargs):
        input_validator = kwargs.pop("_input_validator")
        project.rename(parked)
        replacement.rename(project)
        try:
            snapshot = real_build(**kwargs, _input_validator=None)
            if replacement_mode == "changed":
                assert transient_reads
                assert set(transient_reads) == {str(project / "billing.py")}
        finally:
            project.rename(replacement)
            parked.rename(project)
        input_validator()
        return snapshot

    monkeypatch.setattr(service, "build_prepare_snapshot", build)

    with pytest.raises(SessionStaleError, match="project contents changed while preparing"):
        service.session_prepare(
            opened.session_id,
            "change the monthly billing behavior",
            str(project),
        )

    assert captured_receipts[content_receipt_key(source)] == ""


def _write_javascript_fixture(project: Path, *, pnpm_lock: bool = False) -> None:
    project.mkdir()
    (project / "calc.ts").write_text(
        "export function calculateTotal(value: number): number { return value + 1; }\n",
        encoding="utf-8",
    )
    (project / "calc.test.ts").write_text(
        "import { calculateTotal } from './calc';\n"
        "test('calculate total', () => expect(calculateTotal(1)).toBe(2));\n",
        encoding="utf-8",
    )
    (project / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}, "devDependencies": {"jest": "1"}}),
        encoding="utf-8",
    )
    if pnpm_lock:
        (project / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")


def test_session_prepare_accepts_stable_javascript_inputs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_javascript_fixture(project)
    opened = open_session(str(project), max_repo_files=20)

    prepared = service.session_prepare(opened.session_id, "calculateTotal", str(project))

    commands = {step.get("command") for step in prepared["raw"]["validation_plan"]}
    assert "npm test" in commands
    assert get_session(opened.session_id, str(project))["last_prepare"]["query"] == "calculateTotal"


@pytest.mark.parametrize(
    ("marker_name", "marker_text", "source_name", "source_text", "query"),
    [
        (
            "go.mod",
            "module example.com/demo\n",
            "main.go",
            "package main\nfunc CalculateTotal(x int) int { return x + 1 }\n",
            "CalculateTotal",
        ),
        (
            ".gitignore",
            "*.log\n",
            "billing.py",
            "def process_billing_cycle(accounts):\n    return list(accounts)\n",
            "process_billing_cycle",
        ),
    ],
    ids=("go-mod", "gitignore"),
)
def test_session_prepare_accepts_stable_observed_marker_inputs(
    tmp_path: Path,
    marker_name: str,
    marker_text: str,
    source_name: str,
    source_text: str,
    query: str,
) -> None:
    from tensor_grep.cli.symbols_cache import content_receipt_key

    project = tmp_path / "project"
    project.mkdir()
    marker = project / marker_name
    marker.write_text(marker_text, encoding="utf-8")
    (project / source_name).write_text(source_text, encoding="utf-8")

    opened = open_session(str(project), max_repo_files=20)
    payload = get_session(opened.session_id, str(project))
    assert content_receipt_key(marker) in {
        content_receipt_key(entry["path"]) for entry in payload["snapshot"]
    }

    prepared = service.session_prepare(opened.session_id, query, str(project))

    assert prepared["session_id"] == opened.session_id
    assert payload["snapshot"]


def test_session_prepare_rejects_transient_observed_go_module_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")
    (project / "main.go").write_text(
        "package main\nfunc CalculateTotal(x int) int { return x + 1 }\n", encoding="utf-8"
    )
    opened = open_session(str(project), max_repo_files=20)

    replacement_project = tmp_path / "project-replacement"
    parked = tmp_path / "project-original"
    shutil.copytree(project, replacement_project)
    (replacement_project / "go.mod").write_text("module example.com/transient\n", encoding="utf-8")
    real_build = service.build_prepare_snapshot

    def build(**kwargs):
        input_validator = kwargs.pop("_input_validator")
        project.rename(parked)
        replacement_project.rename(project)
        try:
            snapshot = real_build(**kwargs, _input_validator=None)
        finally:
            project.rename(replacement_project)
            parked.rename(project)
        input_validator()
        return snapshot

    monkeypatch.setattr(service, "build_prepare_snapshot", build)

    with pytest.raises(SessionStaleError, match="project contents changed while preparing"):
        service.session_prepare(opened.session_id, "CalculateTotal", str(project))

    assert "last_prepare" not in get_session(opened.session_id, str(project))


def test_session_prepare_ignores_transient_gitignore_change_outside_signed_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (project / "billing.py").write_text(
        "def process_billing_cycle(accounts):\n    return list(accounts)\n", encoding="utf-8"
    )
    opened = open_session(str(project), max_repo_files=20)
    stable = service.session_prepare(opened.session_id, "process_billing_cycle", str(project))

    replacement_project = tmp_path / "project-replacement"
    parked = tmp_path / "project-original"
    shutil.copytree(project, replacement_project)
    (replacement_project / ".gitignore").write_text("billing.py\n", encoding="utf-8")
    real_build = service.build_prepare_snapshot

    def build(**kwargs):
        input_validator = kwargs.pop("_input_validator")
        project.rename(parked)
        replacement_project.rename(project)
        try:
            snapshot = real_build(**kwargs, _input_validator=None)
        finally:
            project.rename(replacement_project)
            parked.rename(project)
        input_validator()
        return snapshot

    monkeypatch.setattr(service, "build_prepare_snapshot", build)

    prepared = service.session_prepare(opened.session_id, "process_billing_cycle", str(project))

    assert prepared == stable
    assert prepared["blast_radius_floor"] == stable["blast_radius_floor"]


def test_session_prepare_authenticates_javascript_reexport_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    source = project / "src"
    source.mkdir(parents=True)
    (source / "payments.ts").write_text(
        "export function createInvoice(total: number): number { return total + 1; }\n",
        encoding="utf-8",
    )
    barrel = source / "barrel.ts"
    barrel.write_text(
        'export { createInvoice as makeInvoice } from "./payments";\n',
        encoding="utf-8",
    )
    service_path = source / "service.ts"
    service_path.write_text(
        'import { makeInvoice } from "./barrel";\n\n'
        "export function buildReceipt(total: number): number {\n"
        "  return makeInvoice(total);\n"
        "}\n",
        encoding="utf-8",
    )
    opened = open_session(str(project), max_repo_files=20)
    repo_map._clear_all_source_caches()
    real_read_text = Path.read_text
    direct_reads = 0

    def read_text(path: Path, *args, **kwargs) -> str:
        nonlocal direct_reads
        if path.resolve() == barrel.resolve():
            direct_reads += 1
            return 'export { unrelated as makeInvoice } from "./unrelated";\n'
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    stable = service.session_prepare(opened.session_id, "createInvoice", str(project))

    assert direct_reads == 0
    assert any(
        Path(str(caller.get("file"))) == service_path
        for caller in stable["blast_radius_floor"].get("top_callers", [])
    )
    assert get_session(opened.session_id, str(project))["last_prepare"]["query"] == "createInvoice"


def test_session_prepare_authenticates_file_size_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    project.mkdir()
    source = project / "billing.py"
    original = b"def process_billing_cycle(accounts):\n    return list(accounts)\n"
    source.write_bytes(original)
    opened = open_session(str(project), max_repo_files=10)

    stable = service.session_prepare(
        opened.session_id,
        "process_billing_cycle",
        str(project),
    )
    assert stable["session_id"] == opened.session_id

    signed_stat = source.stat()
    real_file_size = repo_map._file_size_with_receipt
    mutated = False

    def file_size(path: Path) -> int:
        nonlocal mutated
        if not mutated and path.resolve() == source.resolve():
            mutated = True
            source.write_bytes(b"#" * (repo_map._max_parse_bytes() + 1))
            try:
                return real_file_size(path)
            finally:
                source.write_bytes(original)
                os.utime(source, ns=(signed_stat.st_atime_ns, signed_stat.st_mtime_ns))
        return real_file_size(path)

    monkeypatch.setattr(repo_map, "_file_size_with_receipt", file_size)

    transient = service.session_prepare(
        opened.session_id,
        "process_billing_cycle",
        str(project),
    )

    assert mutated is True
    assert transient["blast_radius_floor"] == stable["blast_radius_floor"]
    assert get_session(opened.session_id, str(project))["last_prepare"]["query"] == (
        "process_billing_cycle"
    )


def test_session_prepare_derives_python_sys_path_directory_from_signed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli import agent_capsule_builder

    project = tmp_path / "project"
    lib = project / "lib"
    lib.mkdir(parents=True)
    (lib / "mymod.py").write_text(
        "def calculate_total(x):\n    return x + 1\n",
        encoding="utf-8",
    )
    (project / "app.py").write_text(
        "import sys, os\n"
        'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))\n'
        "from mymod import calculate_total\n\n"
        "def run(x):\n    return calculate_total(x)\n",
        encoding="utf-8",
    )
    opened = open_session(str(project), max_repo_files=20)
    stable = service.session_prepare(opened.session_id, "calculate_total", str(project))

    real_collect = agent_capsule_builder._collect_capsule_call_site_evidence
    real_is_dir = Path.is_dir

    def collect(*args, **kwargs):
        def is_dir(path: Path) -> bool:
            if path.resolve() == lib.resolve():
                return False
            return real_is_dir(path)

        with monkeypatch.context() as nested:
            nested.setattr(Path, "is_dir", is_dir)
            return real_collect(*args, **kwargs)

    monkeypatch.setattr(agent_capsule_builder, "_collect_capsule_call_site_evidence", collect)

    transient = service.session_prepare(opened.session_id, "calculate_total", str(project))

    assert transient["blast_radius_floor"] == stable["blast_radius_floor"]

    alternate = tmp_path / "alternate"
    alternate.mkdir()
    real_resolve = Path.resolve
    lib_abs = lib.absolute()
    alternate_abs = alternate.absolute()

    def collect_with_redirect(*args, **kwargs):
        def resolve(path: Path, *resolve_args, **resolve_kwargs) -> Path:
            if path.absolute() == lib_abs:
                return alternate_abs
            return real_resolve(path, *resolve_args, **resolve_kwargs)

        with monkeypatch.context() as nested:
            nested.setattr(Path, "resolve", resolve)
            return real_collect(*args, **kwargs)

    monkeypatch.setattr(
        agent_capsule_builder,
        "_collect_capsule_call_site_evidence",
        collect_with_redirect,
    )

    redirected = service.session_prepare(opened.session_id, "calculate_total", str(project))

    assert redirected["blast_radius_floor"] == stable["blast_radius_floor"]


def test_unknown_authenticated_file_size_poison_receipt(tmp_path: Path) -> None:
    from tensor_grep.cli.repo_map_input_provenance import file_size_with_receipt
    from tensor_grep.cli.symbols_cache import collect_content_receipts, content_receipt_key

    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")

    with collect_content_receipts(authenticated_sizes={}) as receipts:
        assert file_size_with_receipt(source) == source.stat().st_size

    assert receipts[content_receipt_key(source)] == ""


def test_session_prepare_rejects_transient_signed_source_disappearance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli import agent_capsule_builder

    project = tmp_path / "project"
    project.mkdir()
    (project / "billing.py").write_text(
        "def process_billing_cycle(accounts):\n    return list(accounts)\n",
        encoding="utf-8",
    )
    caller = project / "service.py"
    caller.write_text(
        "from billing import process_billing_cycle\n\n"
        "def run_cycle(accounts):\n    return process_billing_cycle(accounts)\n",
        encoding="utf-8",
    )
    opened = open_session(str(project), max_repo_files=20)

    stable = service.session_prepare(
        opened.session_id,
        "process_billing_cycle",
        str(project),
    )
    assert stable["session_id"] == opened.session_id

    parked = tmp_path / "service.py.parked"
    real_collect = agent_capsule_builder._collect_capsule_call_site_evidence
    disappeared = False

    def collect(*args, **kwargs):
        nonlocal disappeared
        if not disappeared:
            disappeared = True
            caller.rename(parked)
            try:
                return real_collect(*args, **kwargs)
            finally:
                parked.rename(caller)
        return real_collect(*args, **kwargs)

    monkeypatch.setattr(agent_capsule_builder, "_collect_capsule_call_site_evidence", collect)

    with pytest.raises(SessionStaleError, match="project contents changed while preparing"):
        service.session_prepare(
            opened.session_id,
            "process_billing_cycle",
            str(project),
        )

    assert disappeared is True
    assert get_session(opened.session_id, str(project))["last_prepare"]["query"] == (
        "process_billing_cycle"
    )


def test_session_prepare_does_not_read_parent_manifest_for_subtree_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli import repo_map

    project = tmp_path / "project"
    scope = project / "src"
    scope.mkdir(parents=True)
    (project / ".git").mkdir()
    parent_manifest = project / "package.json"
    parent_manifest.write_text(
        json.dumps({"packageManager": "yarn@4.0.0", "scripts": {"test": "jest"}}),
        encoding="utf-8",
    )
    (scope / "calc.ts").write_text(
        "export function calculateTotal(value: number): number { return value + 1; }\n",
        encoding="utf-8",
    )
    opened = open_session(str(scope), max_repo_files=20)
    read_paths: list[Path] = []
    real_read_text = repo_map._read_text_with_receipt

    def read_text(path: Path, *, encoding: str = "utf-8") -> str:
        read_paths.append(path)
        return real_read_text(path, encoding=encoding)

    monkeypatch.setattr(repo_map, "_read_text_with_receipt", read_text)

    prepared = service.session_prepare(opened.session_id, "calculateTotal", str(project))

    assert parent_manifest not in read_paths
    commands = {step.get("command") for step in prepared["raw"]["validation_plan"]}
    assert "yarn test" not in commands


def test_session_prepare_authenticates_package_json_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _write_javascript_fixture(project)
    opened = open_session(str(project), max_repo_files=20)
    payload = get_session(opened.session_id, str(project))
    assert str(project / "package.json") in {entry["path"] for entry in payload["snapshot"]}

    replacement = tmp_path / "project-replacement"
    parked = tmp_path / "project-original"
    shutil.copytree(project, replacement)
    (replacement / "package.json").write_text(
        json.dumps({
            "packageManager": "yarn@4.0.0",
            "scripts": {"test": "jest"},
            "devDependencies": {"jest": "1"},
        }),
        encoding="utf-8",
    )
    real_build = service.build_prepare_snapshot

    def build(**kwargs):
        input_validator = kwargs.pop("_input_validator")
        project.rename(parked)
        replacement.rename(project)
        try:
            snapshot = real_build(**kwargs, _input_validator=None)
            commands = {step.get("command") for step in snapshot.raw["validation_plan"]}
            assert "yarn test" in commands
        finally:
            project.rename(replacement)
            parked.rename(project)
        input_validator()
        return snapshot

    monkeypatch.setattr(service, "build_prepare_snapshot", build)

    with pytest.raises(SessionStaleError, match="project contents changed while preparing"):
        service.session_prepare(opened.session_id, "calculateTotal", str(project))

    assert "last_prepare" not in get_session(opened.session_id, str(project))


@pytest.mark.parametrize(
    ("original_lock", "replacement_lock", "expected_command"),
    [(False, True, "pnpm test"), (True, False, "npm test")],
    ids=("transient-add", "transient-removal"),
)
def test_session_prepare_authenticates_package_manager_lock_presence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original_lock: bool,
    replacement_lock: bool,
    expected_command: str,
) -> None:
    project = tmp_path / "project"
    _write_javascript_fixture(project, pnpm_lock=original_lock)
    opened = open_session(str(project), max_repo_files=20)
    payload = get_session(opened.session_id, str(project))
    snapshot_paths = {entry["path"] for entry in payload["snapshot"]}
    assert (str(project / "pnpm-lock.yaml") in snapshot_paths) is original_lock

    replacement = tmp_path / "project-replacement"
    parked = tmp_path / "project-original"
    shutil.copytree(project, replacement)
    replacement_lock_path = replacement / "pnpm-lock.yaml"
    if replacement_lock:
        replacement_lock_path.write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    else:
        replacement_lock_path.unlink()
    real_build = service.build_prepare_snapshot

    def build(**kwargs):
        input_validator = kwargs.pop("_input_validator")
        project.rename(parked)
        replacement.rename(project)
        try:
            snapshot = real_build(**kwargs, _input_validator=None)
            commands = {step.get("command") for step in snapshot.raw["validation_plan"]}
            assert expected_command in commands
        finally:
            project.rename(replacement)
            parked.rename(project)
        input_validator()
        return snapshot

    monkeypatch.setattr(service, "build_prepare_snapshot", build)

    with pytest.raises(SessionStaleError, match="project contents changed while preparing"):
        service.session_prepare(opened.session_id, "calculateTotal", str(project))

    assert "last_prepare" not in get_session(opened.session_id, str(project))


def test_authenticated_prepare_receipts_fail_closed_on_truncated_absence(tmp_path: Path) -> None:
    from tensor_grep.cli.symbols_cache import CONTENT_ABSENT_RECEIPT, content_receipt_key

    signed_path = tmp_path / "signed.py"
    optional_path = tmp_path / "optional.lock"
    digest = "a" * 64
    snapshot = [{"path": str(signed_path), "content_sha256": digest}]
    receipts = {
        content_receipt_key(signed_path): digest,
        content_receipt_key(optional_path): CONTENT_ABSENT_RECEIPT,
    }

    with pytest.raises(SessionStaleError, match="project contents changed while preparing"):
        service._require_authenticated_prepare_inputs(
            snapshot,
            receipts,
            snapshot_complete=False,
        )

    service._require_authenticated_prepare_inputs(
        snapshot,
        receipts,
        snapshot_complete=True,
    )


def test_content_receipts_preserve_conflicts_and_merge_presence(tmp_path: Path) -> None:
    from tensor_grep.cli.symbols_cache import (
        collect_content_receipts,
        content_receipt_key,
        read_content_bytes,
        record_content_presence,
    )

    source = tmp_path / "source.py"
    source.write_bytes(b"first\n")
    with collect_content_receipts() as receipts:
        record_content_presence(source)
        assert read_content_bytes(source) == b"first\n"
        first_digest = receipts[content_receipt_key(source)]
        record_content_presence(source)
        assert receipts[content_receipt_key(source)] == first_digest
        source.write_bytes(b"second\n")
        assert read_content_bytes(source) == b"second\n"

    assert receipts[content_receipt_key(source)] == ""


def test_read_content_bytes_rejects_oversized_authenticated_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli import symbols_cache

    target = tmp_path / "pyproject.toml"
    target.write_bytes(b"a" * 1024)
    key = symbols_cache.content_receipt_key(target)
    monkeypatch.setattr(symbols_cache, "_MAX_AUTHENTICATED_INPUT_BYTES", 8)
    with symbols_cache.collect_content_receipts(authenticated_sizes={key: 8}) as receipts:
        with pytest.raises(OSError, match="exceeded 8 bytes"):
            symbols_cache.read_content_bytes(target)
    assert receipts[key] == ""


def test_require_authenticated_prepare_inputs_rejects_absent_receipt_when_file_exists_on_disk(
    tmp_path: Path,
) -> None:
    from tensor_grep.cli.session_resume_service import (
        SessionStaleError,
        _require_authenticated_prepare_inputs,
    )
    from tensor_grep.cli.symbols_cache import CONTENT_ABSENT_RECEIPT, content_receipt_key

    uv_lock = tmp_path / "uv.lock"
    uv_lock.write_text("version = 1\n", encoding="utf-8")
    with pytest.raises(SessionStaleError):
        _require_authenticated_prepare_inputs(
            [],
            {content_receipt_key(uv_lock): CONTENT_ABSENT_RECEIPT},
            snapshot_complete=True,
        )
