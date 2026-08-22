"""`tg find --json` must populate the routing fields its envelope declares REQUIRED.

`tg find` reuses the `tg search` JSON envelope (same `version`, `routing_backend`,
`routing_reason`, `total_matches`, `matches`, ... plus `schema_version` and
`rank_fallback_reason`), but emitted `routing_backend: null` and `routing_reason: null`. Both are
listed in `required` in `tests/schemas/tg_output.schema.json` and typed
`{"type": "string", "minLength": 1}`, so a schema-validating consumer REJECTS a real `tg find`
payload with `None is not of type 'string'`.

Measured against installed `tg 1.110.16`, with `tg search` on the same tree as the control proving
the fields can be populated:

    tg search --json  ->  routing_backend "NativeCpuBackend"  routing_reason "json_output"
    tg find   --json  ->  routing_backend null                routing_reason null

The fix populates them HONESTLY -- naming which retrieval route actually ran, which the code
already knows because the dense leg either loaded or did not. `rank_fallback_reason` explains WHY
the dense leg is absent; `routing_backend` says WHAT ran. Relaxing the schema instead would have
degraded the contract for `tg search`, which is correct today.

Both arms force the dense seam explicitly rather than reading ambient state (A85): whether a dev
box happens to have `model2vec` installed must not decide what this test measures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app

jsonschema = pytest.importorskip("jsonschema")

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "tg_output.schema.json"


def _schema() -> dict:
    with _SCHEMA_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture
def _force_dense(monkeypatch):
    """Force the dense leg's availability so neither arm depends on this machine's extras."""

    def _set(available: bool) -> None:
        from tensor_grep.cli import main as cli_main

        if available:
            monkeypatch.setattr(cli_main, "dense_available", lambda: (True, None), raising=False)
        else:
            monkeypatch.setattr(
                cli_main,
                "dense_available",
                lambda: (False, "semantic ranking unavailable: forced off for this test"),
                raising=False,
            )

    return _set


def _find_payload(tmp_path: Path) -> dict:
    (tmp_path / "auth.py").write_text(
        "def authenticate_user(name):\n    return name\n", encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["find", "authenticate user", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_find_populates_the_required_routing_fields(tmp_path: Path, _force_dense):
    """The defect, directly: both fields were None on every `tg find --json` payload."""
    _force_dense(False)
    payload = _find_payload(tmp_path)

    assert isinstance(payload.get("routing_backend"), str) and payload["routing_backend"], (
        "routing_backend is REQUIRED and typed minLength 1 by tg_output.schema.json; a null here "
        f"breaks any contract-aware consumer. payload={ {k: payload[k] for k in sorted(payload)[:8]} }"
    )
    assert isinstance(payload.get("routing_reason"), str) and payload["routing_reason"], payload


def test_find_payload_validates_against_the_search_schema(tmp_path: Path, _force_dense):
    """End-to-end: the real emitted payload must satisfy the envelope it reuses."""
    _force_dense(False)
    payload = _find_payload(tmp_path)

    # Control: an empty result would validate trivially and prove nothing.
    assert payload["total_matches"] >= 1, f"no matches; arm cannot discriminate. {payload}"
    jsonschema.validate(instance=payload, schema=_schema())


def test_bm25_only_route_is_named_distinctly_from_the_hybrid_route(tmp_path: Path, _force_dense):
    """The value must describe WHAT RAN, not be a constant.

    Without this, hardcoding a single string would satisfy the tests above while telling every
    consumer the same thing regardless of which retrieval legs actually executed.
    """
    _force_dense(False)
    bm25_only = _find_payload(tmp_path)

    assert "bm25" in bm25_only["routing_reason"].lower(), bm25_only["routing_reason"]
    assert "dense" not in bm25_only["routing_reason"].lower(), (
        "the dense leg did not run in this arm, so the route must not claim it did: "
        f"{bm25_only['routing_reason']!r}"
    )
    # `rank_fallback_reason` explains WHY the dense leg is absent; the routing fields say WHAT ran.
    # Both must be present and they must not be the same signal.
    assert bm25_only.get("rank_fallback_reason"), bm25_only
    assert bm25_only["rank_fallback_reason"] != bm25_only["routing_reason"]
