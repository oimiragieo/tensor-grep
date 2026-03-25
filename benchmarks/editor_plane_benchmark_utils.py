from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
for candidate in (SRC_DIR, BENCHMARKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

_FIXTURE_SPECS: dict[str, int] = {
    "small": 16,
    "medium": 64,
    "large": 224,
}
_FIXTURE_MANIFEST = "fixture_manifest.json"
_FIXTURE_LAYOUT_VERSION = 3
_DEFAULT_QUERY = "create invoice workflow"
_TARGET_SYMBOL = "create_invoice"


def resolve_editor_plane_bench_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_EDITOR_PLANE_BENCH_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "editor_plane_bench"


def load_bench_data_snippets(limit: int = 8) -> list[str]:
    bench_data_dir = ROOT_DIR / "bench_data"
    snippets: list[str] = []
    if bench_data_dir.is_dir():
        for path in sorted(bench_data_dir.iterdir()):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                normalized = line.strip()
                if normalized:
                    snippets.append(normalized)
                    break
            if len(snippets) >= limit:
                break
    if snippets:
        return snippets[:limit]
    return [
        "ERROR alpha timeout marker critical path",
        "WARN retry budget exhausted",
        "INFO invoice pipeline keepalive",
    ]


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _module_comment(snippets: list[str], index: int) -> str:
    return snippets[index % len(snippets)].replace('"', "'")


def _bulk_helper_functions(prefix: str, count: int, *, base_value: int = 0) -> str:
    blocks: list[str] = []
    for index in range(count):
        blocks.append(
            f"def {prefix}_aux_{index:02d}(value: int) -> int:\n"
            f"    return value + {base_value + index}\n"
        )
    return "\n".join(blocks) + "\n"


def _base_module_sources(snippets: list[str]) -> dict[str, str]:
    comment = _module_comment(snippets, 0)
    return {
        "core.py": (
            f'"""Editor benchmark fixture seeded from bench_data: {comment}."""\n\n'
            f"{_bulk_helper_functions('core', 10)}\n"
            "def normalize_invoice_total(total: int) -> int:\n"
            "    baseline = core_aux_00(total) + 1\n"
            "    return baseline\n\n"
            "def apply_invoice_fee(total: int) -> int:\n"
            "    adjusted = normalize_invoice_total(total)\n"
            "    return core_aux_01(adjusted) + 2\n\n"
            "def create_invoice(total: int) -> int:\n"
            "    subtotal = apply_invoice_fee(total)\n"
            "    audit_total = subtotal + 3\n"
            "    review_total = audit_total + 4\n"
            "    return review_total\n\n"
            "class InvoiceFormatter:\n"
            "    def build(self, total: int) -> int:\n"
            "        return create_invoice(total)\n\n"
            "def summarize_invoice(total: int) -> int:\n"
            "    formatter = InvoiceFormatter()\n"
            "    return formatter.build(total)\n"
        ),
        "service.py": (
            "from src.core import create_invoice, summarize_invoice\n\n"
            f"{_bulk_helper_functions('service', 8, base_value=10)}\n"
            "def build_invoice(total: int) -> int:\n"
            "    return service_aux_00(create_invoice(total)) + 3\n\n"
            "def build_invoice_summary(total: int) -> int:\n"
            "    return summarize_invoice(total)\n\n"
            "def audit_invoice(total: int) -> int:\n"
            "    return build_invoice(total) + build_invoice_summary(total)\n"
        ),
        "api.py": (
            "from src.service import audit_invoice, build_invoice\n\n"
            f"{_bulk_helper_functions('api', 8, base_value=20)}\n"
            "def present_invoice(total: int) -> int:\n"
            "    return api_aux_00(build_invoice(total))\n\n"
            "def present_invoice_audit(total: int) -> int:\n"
            "    return audit_invoice(total)\n"
        ),
        "ui.py": (
            "from src.api import present_invoice, present_invoice_audit\n\n"
            f"{_bulk_helper_functions('ui', 8, base_value=30)}\n"
            "def render_invoice(total: int) -> int:\n"
            "    return ui_aux_00(present_invoice(total))\n\n"
            "def render_invoice_with_audit(total: int) -> int:\n"
            "    return present_invoice_audit(total)\n"
        ),
    }


def _consumer_module_sources(group_index: int, snippets: list[str]) -> dict[str, str]:
    comment = _module_comment(snippets, group_index + 1)
    direct_name = f"consumer_{group_index:03d}"
    workflow_name = f"workflow_{group_index:03d}"
    view_name = f"view_{group_index:03d}"
    return {
        f"{direct_name}.py": (
            "from src.core import create_invoice\n\n"
            f"{_bulk_helper_functions(direct_name, 10, base_value=40 + group_index)}\n"
            f"def normalize_{direct_name}(total: int) -> int:\n"
            f"    return {direct_name}_aux_00(total) + 1\n\n"
            f"def {direct_name}(total: int) -> int:\n"
            f'    note = "{comment}"\n'
            f"    normalized = normalize_{direct_name}(total)\n"
            "    if note:\n"
            f"        normalized = {direct_name}_aux_01(normalized)\n"
            "    return create_invoice(normalized)\n\n"
            f"class {direct_name.title().replace('_', '')}Runner:\n"
            "    def run(self, total: int) -> int:\n"
            f"        return {direct_name}(total)\n\n"
            f"def audit_{direct_name}(total: int) -> int:\n"
            f"    runner = {direct_name.title().replace('_', '')}Runner()\n"
            "    return runner.run(total)\n"
        ),
        f"{workflow_name}.py": (
            f"from src.{direct_name} import audit_{direct_name}, {direct_name}\n\n"
            f"{_bulk_helper_functions(workflow_name, 8, base_value=80 + group_index)}\n"
            f"def {workflow_name}(total: int) -> int:\n"
            f"    return {workflow_name}_aux_00({direct_name}(total)) + 1\n\n"
            f"def review_{workflow_name}(total: int) -> int:\n"
            f"    return audit_{direct_name}(total) + {workflow_name}(total)\n"
        ),
        f"{view_name}.py": (
            f"from src.{workflow_name} import {workflow_name}, review_{workflow_name}\n\n"
            f"{_bulk_helper_functions(view_name, 8, base_value=120 + group_index)}\n"
            f"def {view_name}(total: int) -> int:\n"
            f"    return {view_name}_aux_00({workflow_name}(total))\n\n"
            f"def inspect_{view_name}(total: int) -> int:\n"
            f"    return review_{workflow_name}(total) + {view_name}(total)\n"
        ),
    }


def _test_module_source(group_index: int) -> str:
    view_name = f"view_{group_index:03d}"
    return (
        f"from src.{view_name} import inspect_{view_name}, {view_name}\n\n"
        f"def test_{view_name}() -> None:\n"
        f"    assert {view_name}(1) >= 0\n\n"
        f"def test_inspect_{view_name}() -> None:\n"
        f"    assert inspect_{view_name}(1) >= {view_name}(1)\n"
    )


def _extra_module_source(index: int, snippets: list[str]) -> str:
    comment = _module_comment(snippets, index + 3)
    return (
        f'"""Additional editor benchmark fixture content: {comment}."""\n\n'
        f"{_bulk_helper_functions(f'helper_{index:03d}', 12, base_value=160 + index)}\n"
        f"def helper_{index:03d}(value: int) -> int:\n"
        f"    return helper_{index:03d}_aux_00(value) + {index}\n\n"
        f"def helper_{index:03d}_double(value: int) -> int:\n"
        f"    return helper_{index:03d}(value) * 2\n\n"
        f"def helper_{index:03d}_triple(value: int) -> int:\n"
        f"    return helper_{index:03d}_double(value) + helper_{index:03d}(value)\n"
    )

def _materialize_editor_plane_fixture(root: Path, *, file_count: int, snippets: list[str]) -> dict[str, Any]:
    if root.exists():
        shutil.rmtree(root)
    src_dir = root / "src"
    tests_dir = root / "tests"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    created_files: list[str] = []
    mutable_files: list[str] = []
    blast_radius_symbols = [
        {"symbol": _TARGET_SYMBOL, "depth": 1},
        {"symbol": _TARGET_SYMBOL, "depth": 2},
        {"symbol": _TARGET_SYMBOL, "depth": 3},
    ]

    for relative_path, content in _base_module_sources(snippets).items():
        created_files.append(str(_write(src_dir / relative_path, content).resolve()))

    group_index = 0
    while len(created_files) + max(1, file_count // 16) + 3 <= file_count:
        for relative_path, content in _consumer_module_sources(group_index, snippets).items():
            created_files.append(str(_write(src_dir / relative_path, content).resolve()))
            mutable_files.append(str((src_dir / relative_path).resolve()))
        if group_index % 4 == 0:
            created_files.append(
                str(_write(tests_dir / f"test_view_{group_index:03d}.py", _test_module_source(group_index)).resolve())
            )
        group_index += 1

    extra_index = 0
    while len(created_files) < file_count:
        created_files.append(
            str(
                _write(
                    src_dir / f"helper_{extra_index:03d}.py",
                    _extra_module_source(extra_index, snippets),
                ).resolve()
            )
        )
        mutable_files.append(str((src_dir / f"helper_{extra_index:03d}.py").resolve()))
        extra_index += 1

    manifest = {
        "version": _FIXTURE_LAYOUT_VERSION,
        "name": root.name,
        "root": str(root.resolve()),
        "file_count": len(created_files),
        "query": _DEFAULT_QUERY,
        "target_symbol": _TARGET_SYMBOL,
        "blast_radius_symbols": blast_radius_symbols,
        "mutable_files": mutable_files[: max(5, min(len(mutable_files), 24))],
        "created_files": created_files,
    }
    (root / _FIXTURE_MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def ensure_editor_plane_fixture(root: Path, *, file_count: int) -> dict[str, Any]:
    manifest_path = root / _FIXTURE_MANIFEST
    if manifest_path.exists():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            isinstance(payload, dict)
            and int(payload.get("version", 0)) == _FIXTURE_LAYOUT_VERSION
            and int(payload.get("file_count", 0)) >= file_count
        ):
            payload["name"] = str(payload.get("name") or root.name)
            payload["root"] = str(root.resolve())
            return payload
    return _materialize_editor_plane_fixture(
        root,
        file_count=file_count,
        snippets=load_bench_data_snippets(),
    )


def ensure_editor_plane_fixture_set(bench_dir: Path) -> dict[str, dict[str, Any]]:
    bench_dir.mkdir(parents=True, exist_ok=True)
    fixtures: dict[str, dict[str, Any]] = {}
    for name, file_count in _FIXTURE_SPECS.items():
        fixture = ensure_editor_plane_fixture(bench_dir / name, file_count=file_count)
        fixture["name"] = name
        fixtures[name] = fixture
    return fixtures
