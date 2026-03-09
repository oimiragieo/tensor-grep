from __future__ import annotations

import tomllib
from pathlib import Path


def _optional_dependencies() -> dict[str, list[str]]:
    root = Path(__file__).resolve().parents[2]
    payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return payload["project"]["optional-dependencies"]


def test_nlp_extra_should_use_http_triton_client_not_all() -> None:
    deps = _optional_dependencies()["nlp"]
    assert "transformers>=4.40" in deps
    assert "tritonclient[http]" in deps
    assert "tritonclient[all]" not in deps
