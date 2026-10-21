"""Shared helpers for GPU benchmark script tests."""

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_script_module(name: str, rel_path: str) -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    module_path = root / rel_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
