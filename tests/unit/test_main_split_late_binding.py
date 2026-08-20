"""The moved code must still resolve patched names through ``cli/main.py``'s globals.

`cli/main.py` was split on 2026-08-20 into `ast_scan`, `doctor_report`, `doctor_payload`,
`native_frontdoor` and `windows_launcher` (see
`docs/design/2026-08-19-split-floor-escape.md`). The whole reason that split was blocked until
PR #1042 is that a bare name resolves through the DEFINING module's globals: move a function
that bare-calls a monkeypatched name and **the test still passes while production runs the
unpatched original.** That failure is silent, so it needs a check that would go red if the
mechanism regressed -- not an assertion that the modules merely import.

Each test below is a PAIR. The control arm asserts the un-patched value so a patch that does
nothing cannot look like a pass, and the treatment arm asserts the patched value is what the
MOVED function observes. `scripts/bare_call_ratchet.py` covers the same property statically for
`main.py` itself; this covers it dynamically, across the module boundary the ratchet cannot see.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from tensor_grep.cli import (
    ast_scan,
    doctor_payload,
    doctor_report,
    native_frontdoor,
    windows_launcher,
)
from tensor_grep.cli import (
    main as cli_main,
)
from tensor_grep.cli._main_binding import _self


def test_self_resolves_to_the_main_module() -> None:
    assert _self.__name__ == "tensor_grep.cli.main"
    assert _self.app is cli_main.app


@pytest.mark.parametrize(
    "module",
    [ast_scan, doctor_report, doctor_payload, native_frontdoor, windows_launcher],
)
def test_every_split_module_shares_one_self(module: ModuleType) -> None:
    assert module._self is _self


def test_moved_doctor_payload_sees_a_patch_applied_to_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_build_doctor_payload` lives in cli/doctor_payload.py; the probe it calls is patched
    on `main`. Control arm first, so a no-op patch cannot pass."""
    real = doctor_report._doctor_installed_version()
    control = doctor_payload._build_doctor_payload(str(tmp_path), with_lsp=False)
    assert control["version"] == real
    assert real != "9.9.9-probe"

    monkeypatch.setattr(cli_main, "_doctor_installed_version", lambda: "9.9.9-probe")
    payload = doctor_payload._build_doctor_payload(str(tmp_path), with_lsp=False)
    assert payload["version"] == "9.9.9-probe"


def test_moved_native_frontdoor_sees_a_patched_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_MAX_NATIVE_ASSET_DOWNLOAD_BYTES` deliberately stayed in `main.py` because tests patch
    it; the download cap that reads it moved to cli/native_frontdoor.py."""
    assert native_frontdoor._self._MAX_NATIVE_ASSET_DOWNLOAD_BYTES == 512 * 1024 * 1024
    monkeypatch.setattr(cli_main, "_MAX_NATIVE_ASSET_DOWNLOAD_BYTES", 1)
    assert native_frontdoor._self._MAX_NATIVE_ASSET_DOWNLOAD_BYTES == 1


def test_moved_ast_scan_sees_a_patched_main_helper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`_load_sg_project_config` moved to cli/ast_scan.py but still calls
    `_normalize_string_list`, which stayed in `main.py`."""
    config_path = tmp_path / "sgconfig.yml"
    config_path.write_text("language: python\nruleDirs:\n  - rules\n", encoding="utf-8")

    control = ast_scan._load_sg_project_config(str(config_path))
    assert control["rule_dirs"] == ["rules"]

    sentinel = ["sentinel-dir"]
    monkeypatch.setattr(cli_main, "_normalize_string_list", lambda raw, default: sentinel)
    resolved = ast_scan._load_sg_project_config(str(config_path))
    assert resolved["rule_dirs"] is sentinel
    assert resolved["test_dirs"] is sentinel
