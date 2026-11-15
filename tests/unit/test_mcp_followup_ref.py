"""RED/GREEN tests for the bounded MCP follow-up reference prototype (MCP-SURFACE ext,
Task 10 checkbox: "Prototype bounded follow-up references for omitted source").
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli.mcp_followup_ref import (
    FollowupRef,
    FollowupRefError,
    mint_followup_ref,
    resolve_followup_ref,
)

_SECRET = b"test-secret"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src.py").write_text("x" * 200, encoding="utf-8")
    return tmp_path


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


def test_non_ascii_signature_fails_closed_as_tampered(repo: Path) -> None:
    ref = mint_followup_ref(
        root=repo,
        rel_path="src.py",
        params={},
        byte_range=(0, 10),
        ttl_seconds=60,
        secret=_SECRET,
    )
    envelope = json.loads(ref.token)
    envelope["sig"] = "é" * 64
    with pytest.raises(FollowupRefError) as exc_info:
        resolve_followup_ref(
            FollowupRef(token=json.dumps(envelope)),
            current_root=repo,
            params={},
            secret=_SECRET,
        )
    assert exc_info.value.reason == "tampered"


def test_equivalent_spelling_of_the_same_root_resolves(repo: Path) -> None:
    (repo / "sub").mkdir()
    ref = mint_followup_ref(
        root=repo,
        rel_path="src.py",
        params={},
        byte_range=(0, 10),
        ttl_seconds=60,
        secret=_SECRET,
    )
    payload = resolve_followup_ref(ref, current_root=repo / "sub" / "..", params={}, secret=_SECRET)
    assert payload["path"] == "src.py"


def test_same_root_is_filesystem_identity_and_fails_closed(
    repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # council wave-2b r6: an AUTHORIZATION decision -- identity, never a lexical normcase.
    from tensor_grep.cli import mcp_followup_ref

    (repo / "sub").mkdir()
    assert mcp_followup_ref._same_root(str(repo), repo / "sub" / "..") is True
    if sys.platform == "win32":
        assert mcp_followup_ref._same_root(str(repo).upper(), repo) is True
    # a missing minted root fails closed
    assert mcp_followup_ref._same_root(str(repo / "gone"), repo) is False
    # a different directory is not the same root
    other = tmp_path_factory.mktemp("other")
    assert mcp_followup_ref._same_root(str(repo), other) is False


def test_same_root_distinguishes_directories_that_differ_only_by_case(tmp_path: Path) -> None:
    from tensor_grep.cli import mcp_followup_ref

    upper = tmp_path / "Repo"
    lower = tmp_path / "repo"
    upper.mkdir()
    try:
        lower.mkdir()
    except FileExistsError:
        # case-insensitive filesystem; on Windows try a per-directory case-sensitive pair
        if sys.platform != "win32":
            pytest.skip("filesystem is case-insensitive; cannot create a case-only pair")
        import shutil
        import subprocess

        if shutil.which("fsutil") is None:
            pytest.skip("fsutil is not on PATH")
        shutil.rmtree(upper)
        enabled = subprocess.run(
            ["fsutil", "file", "setCaseSensitiveInfo", str(tmp_path), "enable"],
            capture_output=True,
            check=False,
        )
        if enabled.returncode != 0:
            pytest.skip("cannot enable per-directory case sensitivity here (needs privileges)")
        upper.mkdir()
        lower.mkdir()
    assert mcp_followup_ref._same_root(str(upper), lower) is False
    assert mcp_followup_ref._same_root(str(upper), upper) is True
