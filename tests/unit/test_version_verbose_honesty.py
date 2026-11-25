from __future__ import annotations

import importlib
import importlib.metadata as importlib_metadata
import importlib.util
import sys
from types import SimpleNamespace

import pytest

from tensor_grep.cli import bootstrap, version_info
from tensor_grep.cli import main as cli_main


def _fake_find_spec(present: set[str]):
    def _probe(name: str):
        if name in present:
            return SimpleNamespace(name=name)
        return None

    return _probe


def _fake_import_module(modules: dict[str, object], failing: set[str] | None = None):
    failing_names = failing or set()

    def _importer(name: str):
        if name in failing_names or name not in modules:
            raise ImportError(f"cannot import {name}")
        return modules[name]

    return _importer


def test_version_detail_lines_when_optional_deps_absent(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec(set()))

    lines = version_info.version_detail_lines()

    assert "features:-gpu-cudf,-gpu-torch,-rust-core" in lines
    assert "Arrow Zero-Copy IPC: unavailable" in lines
    assert "torch-cpu" not in lines
    joined = "\n".join(lines)
    assert "+gpu-cudf" not in joined
    assert "+gpu-torch" not in joined
    assert "Arrow Zero-Copy IPC is available" not in joined


def test_version_detail_lines_when_optional_deps_present_with_cuda(monkeypatch):
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        _fake_find_spec({"cudf", "torch", "pyarrow", "tensor_grep.rust_core"}),
    )
    monkeypatch.setattr(
        importlib,
        "import_module",
        _fake_import_module({
            "cudf": SimpleNamespace(),
            "torch": fake_torch,
            "pyarrow": SimpleNamespace(),
            "tensor_grep.rust_core": SimpleNamespace(),
        }),
    )

    lines = version_info.version_detail_lines()

    assert "features:+gpu-cudf,+gpu-torch,+rust-core" in lines
    assert "Arrow Zero-Copy IPC: available (cudf+pyarrow)" in lines
    assert "torch-cpu" not in lines


def test_version_detail_lines_cpu_only_torch_and_pyarrow_without_cudf(monkeypatch):
    cpu_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        _fake_find_spec({"torch", "pyarrow", "tensor_grep.rust_core"}),
    )
    monkeypatch.setattr(
        importlib,
        "import_module",
        _fake_import_module({
            "torch": cpu_torch,
            "pyarrow": SimpleNamespace(),
            "tensor_grep.rust_core": SimpleNamespace(),
        }),
    )

    lines = version_info.version_detail_lines()

    assert "features:-gpu-cudf,-gpu-torch,+rust-core" in lines
    assert "torch-cpu" in lines
    assert "Arrow Zero-Copy IPC: unavailable" in lines
    joined = "\n".join(lines)
    assert "+gpu-torch" not in joined


def test_version_detail_lines_handles_unimportable_specs_honestly(monkeypatch):
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        _fake_find_spec({"cudf", "torch", "pyarrow", "tensor_grep.rust_core"}),
    )
    monkeypatch.setattr(
        importlib,
        "import_module",
        _fake_import_module({}, failing={"cudf", "torch", "pyarrow", "tensor_grep.rust_core"}),
    )

    lines = version_info.version_detail_lines()

    assert "features:-gpu-cudf,-gpu-torch,-rust-core" in lines
    assert "torch-cpu" not in lines
    assert "Arrow Zero-Copy IPC: unavailable" in lines


def test_bootstrap_version_verbose_honors_absent_and_present_deps(monkeypatch, capsys):
    def _raise_version(_dist_name: str) -> str:
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(importlib_metadata, "version", _raise_version)
    monkeypatch.setattr(bootstrap, "_read_project_version_fallback", lambda: "9.9.9")
    monkeypatch.setattr(bootstrap, "_run_full_cli", lambda: pytest.fail("full cli should not run"))

    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec(set()))
    monkeypatch.setattr(sys, "argv", ["tg", "--version", "--verbose"])
    with pytest.raises(SystemExit) as exc_absent:
        bootstrap.main_entry()
    assert exc_absent.value.code == 0
    out_absent = capsys.readouterr().out
    assert "tensor-grep 9.9.9\n\n" in out_absent
    assert "features:-gpu-cudf,-gpu-torch,-rust-core" in out_absent
    assert "Arrow Zero-Copy IPC: unavailable" in out_absent

    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        _fake_find_spec({"cudf", "torch", "pyarrow", "tensor_grep.rust_core"}),
    )
    monkeypatch.setattr(
        importlib,
        "import_module",
        _fake_import_module({
            "cudf": SimpleNamespace(),
            "torch": fake_torch,
            "pyarrow": SimpleNamespace(),
            "tensor_grep.rust_core": SimpleNamespace(),
        }),
    )
    monkeypatch.setattr(sys, "argv", ["tg", "--verbose", "--version"])
    with pytest.raises(SystemExit) as exc_present:
        bootstrap.main_entry()
    assert exc_present.value.code == 0
    out_present = capsys.readouterr().out
    assert "features:+gpu-cudf,+gpu-torch,+rust-core" in out_present
    assert "Arrow Zero-Copy IPC: available (cudf+pyarrow)" in out_present


def test_main_version_verbose_honors_absent_and_present_deps(monkeypatch, capsys):
    def _raise_version(_dist_name: str) -> str:
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(importlib_metadata, "version", _raise_version)
    monkeypatch.setattr(cli_main, "_read_project_version_fallback", lambda: "9.9.9")

    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec(set()))
    monkeypatch.setattr(sys, "argv", ["tg", "--version", "--verbose"])
    with pytest.raises(SystemExit) as exc_absent:
        cli_main.main_entry()
    assert exc_absent.value.code == 0
    out_absent = capsys.readouterr().out
    assert "features:-gpu-cudf,-gpu-torch,-rust-core" in out_absent
    assert "Arrow Zero-Copy IPC: unavailable" in out_absent

    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        _fake_find_spec({"cudf", "torch", "pyarrow", "tensor_grep.rust_core"}),
    )
    monkeypatch.setattr(
        importlib,
        "import_module",
        _fake_import_module({
            "cudf": SimpleNamespace(),
            "torch": fake_torch,
            "pyarrow": SimpleNamespace(),
            "tensor_grep.rust_core": SimpleNamespace(),
        }),
    )
    monkeypatch.setattr(sys, "argv", ["tg", "-V", "-v"])
    with pytest.raises(SystemExit) as exc_present:
        cli_main.main_entry()
    assert exc_present.value.code == 0
    out_present = capsys.readouterr().out
    assert "features:+gpu-cudf,+gpu-torch,+rust-core" in out_present
    assert "Arrow Zero-Copy IPC: available (cudf+pyarrow)" in out_present


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--version"], False),
        (["-V"], False),
        (["--version", "--verbose"], True),
        (["-V", "-v"], True),
        (["--verbose", "--version"], True),
        (["-v", "-V"], True),
        (["search", "--version"], False),
        (["search", "--verbose", "-V"], True),
        (["--version", "-V"], None),
        (["--version", "extra"], None),
        (["--version", "--verbose", "extra"], None),
        (["--version", "needle", "-v"], None),
        (["search", "--version", "extra"], None),
        (["--verbose"], None),
    ],
)
def test_version_invocation_verbose_matches_rust_contract(argv, expected):
    assert version_info.version_invocation_verbose(argv) is expected


@pytest.mark.parametrize(
    "invalid_argv",
    [
        ["tg", "--version", "-V"],
        ["tg", "--version", "extra"],
        ["tg", "--version", "needle", "-v"],
    ],
)
def test_bootstrap_and_main_reject_invalid_version_arg_combinations(monkeypatch, invalid_argv):
    bootstrap_seen = {"full_cli": False}
    main_seen: dict[str, object] = {}

    monkeypatch.setattr(
        bootstrap,
        "_print_version",
        lambda **_kwargs: pytest.fail("bootstrap _print_version should not run"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_full_cli",
        lambda: bootstrap_seen.__setitem__("full_cli", True),
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_rg_passthrough",
        lambda _args: pytest.fail("bootstrap rg passthrough should not run"),
    )
    monkeypatch.setattr(sys, "argv", invalid_argv)
    # Invalid --version combos are not intercepted by the version fast-path;
    # they route onward (e.g., to _run_full_cli or native dispatch).
    monkeypatch.setattr(bootstrap, "_requires_full_cli", lambda _args: True)
    bootstrap.main_entry()
    assert bootstrap_seen["full_cli"] is True

    monkeypatch.setattr(
        cli_main,
        "_print_version",
        lambda **_kwargs: pytest.fail("main _print_version should not run"),
    )
    monkeypatch.setattr(
        cli_main._self,
        "app",
        lambda **kwargs: main_seen.setdefault("app_kwargs", kwargs),
    )
    monkeypatch.setattr(sys, "argv", list(invalid_argv))
    cli_main.main_entry()
    assert "app_kwargs" in main_seen
