import json
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
def test_session_prepare_refuses_evidence_built_from_temporary_project_contents(
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
            ), snapshot.blast_radius_floor
        finally:
            project.rename(replacement)
            parked.rename(project)
        input_validator()
        return snapshot

    monkeypatch.setattr(service, "build_prepare_snapshot", build)

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
        with pytest.raises(SessionStaleError, match="project contents changed while preparing"):
            service.session_prepare(opened.session_id, "process_billing_cycle", str(project))
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
