"""RED/GREEN tests for the bounded MCP follow-up reference prototype (MCP-SURFACE ext,
Task 10 checkbox: "Prototype bounded follow-up references for omitted source").
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli.mcp_followup_ref import (
    FollowupRefError,
    mint_followup_ref,
    resolve_followup_ref,
)

_SECRET = b"test-secret"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src.py").write_text("x" * 200, encoding="utf-8")
    return tmp_path


def test_valid_reference_resolves(repo: Path) -> None:
    ref = mint_followup_ref(
        root=repo,
        rel_path="src.py",
        params={"q": "foo"},
        byte_range=(0, 100),
        ttl_seconds=60,
        secret=_SECRET,
    )
    payload = resolve_followup_ref(ref, current_root=repo, params={"q": "foo"}, secret=_SECRET)
    assert payload["range"] == [0, 100]
    assert payload["path"] == "src.py"


def test_expired_reference_fails_closed(repo: Path) -> None:
    ref = mint_followup_ref(
        root=repo,
        rel_path="src.py",
        params={},
        byte_range=(0, 10),
        ttl_seconds=1,
        secret=_SECRET,
        now=1000.0,
    )
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(ref, current_root=repo, params={}, secret=_SECRET, now=1002.0)
    assert exc_info.value.reason == "expired"


def test_cross_root_reference_fails_closed(
    repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    other_root = tmp_path_factory.mktemp("other")
    ref = mint_followup_ref(
        root=repo,
        rel_path="src.py",
        params={},
        byte_range=(0, 10),
        ttl_seconds=60,
        secret=_SECRET,
    )
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(ref, current_root=other_root, params={}, secret=_SECRET)
    assert exc_info.value.reason == "cross_root"


def test_tampered_reference_fails_closed(repo: Path) -> None:
    ref = mint_followup_ref(
        root=repo,
        rel_path="src.py",
        params={},
        byte_range=(0, 10),
        ttl_seconds=60,
        secret=_SECRET,
    )
    import json

    envelope = json.loads(ref.token)
    envelope["payload"]["range"] = [0, 999999]
    from tensor_grep.cli.mcp_followup_ref import FollowupRef

    tampered = FollowupRef(token=json.dumps(envelope))
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(tampered, current_root=repo, params={}, secret=_SECRET)
    assert exc_info.value.reason == "tampered"


def test_stale_snapshot_fails_closed(repo: Path) -> None:
    ref = mint_followup_ref(
        root=repo,
        rel_path="src.py",
        params={},
        byte_range=(0, 10),
        ttl_seconds=60,
        secret=_SECRET,
    )
    time.sleep(0.01)
    (repo / "src.py").write_text("changed content", encoding="utf-8")
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(ref, current_root=repo, params={}, secret=_SECRET)
    assert exc_info.value.reason == "stale_snapshot"


def test_params_mismatch_fails_closed(repo: Path) -> None:
    ref = mint_followup_ref(
        root=repo,
        rel_path="src.py",
        params={"q": "foo"},
        byte_range=(0, 10),
        ttl_seconds=60,
        secret=_SECRET,
    )
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(ref, current_root=repo, params={"q": "bar"}, secret=_SECRET)
    assert exc_info.value.reason == "params_mismatch"


def test_malformed_token_fails_closed(repo: Path) -> None:
    from tensor_grep.cli.mcp_followup_ref import FollowupRef

    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(
            FollowupRef(token="not json"), current_root=repo, params={}, secret=_SECRET
        )
    assert exc_info.value.reason == "malformed"


def test_invalid_byte_range_rejected(repo: Path) -> None:
    with pytest.raises(ValueError):
        mint_followup_ref(
            root=repo,
            rel_path="src.py",
            params={},
            byte_range=(50, 10),
            ttl_seconds=60,
            secret=_SECRET,
        )


def test_nonpositive_ttl_rejected(repo: Path) -> None:
    with pytest.raises(ValueError):
        mint_followup_ref(
            root=repo,
            rel_path="src.py",
            params={},
            byte_range=(0, 10),
            ttl_seconds=0,
            secret=_SECRET,
        )


def test_path_traversal_rejected_at_mint(repo: Path) -> None:
    with pytest.raises(ValueError):
        mint_followup_ref(
            root=repo,
            rel_path="../outside.txt",
            params={},
            byte_range=(0, 10),
            ttl_seconds=60,
            secret=_SECRET,
        )


def test_absolute_path_rejected_at_mint(repo: Path) -> None:
    with pytest.raises(ValueError):
        mint_followup_ref(
            root=repo,
            rel_path=str(repo / "src.py"),
            params={},
            byte_range=(0, 10),
            ttl_seconds=60,
            secret=_SECRET,
        )


def test_range_beyond_eof_rejected_at_mint(repo: Path) -> None:
    with pytest.raises(ValueError):
        mint_followup_ref(
            root=repo,
            rel_path="src.py",
            params={},
            byte_range=(0, 10_000),
            ttl_seconds=60,
            secret=_SECRET,
        )


def test_same_size_same_mtime_replacement_detected_as_stale(repo: Path) -> None:
    """A same-size overwrite must still be caught -- snapshot identity is a content hash,
    not mtime+size metadata that a same-size replacement could pass through unnoticed."""
    ref = mint_followup_ref(
        root=repo,
        rel_path="src.py",
        params={},
        byte_range=(0, 10),
        ttl_seconds=60,
        secret=_SECRET,
    )
    original_stat = (repo / "src.py").stat()
    replacement = "y" * 200
    assert len(replacement) == original_stat.st_size
    (repo / "src.py").write_text(replacement, encoding="utf-8")
    import os

    os.utime(repo / "src.py", ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(ref, current_root=repo, params={}, secret=_SECRET)
    assert exc_info.value.reason == "stale_snapshot"


def test_expiry_boundary_is_exclusive(repo: Path) -> None:
    ref = mint_followup_ref(
        root=repo,
        rel_path="src.py",
        params={},
        byte_range=(0, 10),
        ttl_seconds=10,
        secret=_SECRET,
        now=1000.0,
    )
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(ref, current_root=repo, params={}, secret=_SECRET, now=1010.0)
    assert exc_info.value.reason == "expired"


def test_malformed_payload_missing_field_fails_closed(repo: Path) -> None:
    import json

    from tensor_grep.cli.mcp_followup_ref import FollowupRef

    broken = FollowupRef(token=json.dumps({"payload": {"v": "fr1"}, "sig": "x"}))
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(broken, current_root=repo, params={}, secret=_SECRET)
    assert exc_info.value.reason == "malformed"


def test_non_dict_payload_fails_closed(repo: Path) -> None:
    import json

    from tensor_grep.cli.mcp_followup_ref import FollowupRef

    broken = FollowupRef(token=json.dumps({"payload": "not-a-dict", "sig": "x"}))
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(broken, current_root=repo, params={}, secret=_SECRET)
    assert exc_info.value.reason == "malformed"


def _tampered_but_signed(repo: Path, mutate) -> Any:
    """Build a token with a mutated (but still schema-complete) payload, RE-SIGNED so the
    test exercises schema validation specifically, not the (already-covered) signature check.
    """
    import json

    from tensor_grep.cli.mcp_followup_ref import FollowupRef, _sign

    ref = mint_followup_ref(
        root=repo,
        rel_path="src.py",
        params={},
        byte_range=(0, 10),
        ttl_seconds=60,
        secret=_SECRET,
    )
    envelope = json.loads(ref.token)
    mutate(envelope["payload"])
    envelope["sig"] = _sign(envelope["payload"], _SECRET)
    return FollowupRef(token=json.dumps(envelope))


def test_non_integer_range_component_fails_closed(repo: Path) -> None:
    broken = _tampered_but_signed(repo, lambda p: p.__setitem__("range", [0, "10"]))
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(broken, current_root=repo, params={}, secret=_SECRET)
    assert exc_info.value.reason == "malformed"


def test_bool_expires_at_fails_closed(repo: Path) -> None:
    """bool is a subclass of int in Python -- must be explicitly excluded, not merely
    isinstance(x, (int, float)) checked."""
    broken = _tampered_but_signed(repo, lambda p: p.__setitem__("expires_at", True))
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(broken, current_root=repo, params={}, secret=_SECRET)
    assert exc_info.value.reason == "malformed"


def test_non_string_snapshot_id_fails_closed(repo: Path) -> None:
    broken = _tampered_but_signed(repo, lambda p: p.__setitem__("snapshot_id", 12345))
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(broken, current_root=repo, params={}, secret=_SECRET)
    assert exc_info.value.reason == "malformed"


def test_nan_ttl_rejected(repo: Path) -> None:
    with pytest.raises(ValueError):
        mint_followup_ref(
            root=repo,
            rel_path="src.py",
            params={},
            byte_range=(0, 10),
            ttl_seconds=float("nan"),
            secret=_SECRET,
        )


def test_infinite_ttl_rejected(repo: Path) -> None:
    with pytest.raises(ValueError):
        mint_followup_ref(
            root=repo,
            rel_path="src.py",
            params={},
            byte_range=(0, 10),
            ttl_seconds=float("inf"),
            secret=_SECRET,
        )


def test_nan_expires_at_fails_closed(repo: Path) -> None:
    """A tampered-but-resigned payload with a NaN expires_at must never bypass expiry via
    NaN's all-comparisons-false semantics."""
    broken = _tampered_but_signed(repo, lambda p: p.__setitem__("expires_at", float("nan")))
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(broken, current_root=repo, params={}, secret=_SECRET)
    assert exc_info.value.reason == "malformed"


def test_missing_issued_at_fails_closed(repo: Path) -> None:
    broken = _tampered_but_signed(repo, lambda p: p.pop("issued_at"))
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(broken, current_root=repo, params={}, secret=_SECRET)
    assert exc_info.value.reason == "malformed"


def test_bool_ttl_seconds_rejected_at_mint(repo: Path) -> None:
    with pytest.raises(ValueError):
        mint_followup_ref(
            root=repo,
            rel_path="src.py",
            params={},
            byte_range=(0, 10),
            ttl_seconds=True,
            secret=_SECRET,
        )


def test_bool_byte_range_component_rejected_at_mint(repo: Path) -> None:
    with pytest.raises(ValueError):
        mint_followup_ref(
            root=repo,
            rel_path="src.py",
            params={},
            byte_range=(False, 10),
            ttl_seconds=60,
            secret=_SECRET,
        )
