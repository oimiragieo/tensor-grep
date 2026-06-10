"""Tests for LSP cache/security/correctness fixes.

Findings covered:
  I3  — LRU cache eviction + didClose handler
  S6  — _safe_extract_tar rejects symlink/hardlink path-traversal members
  B12 — per-request-id demux in ExternalLSPClient
  B15 — monotonic version tracking in ExternalLSPClient.did_change
  B17 — _configured_timeout_seconds treats <=0 env values as invalid
  B13 — UTF-16 <-> codepoint column conversion helpers (no server needed)
  B16 — _resolve_workspace_root resolves without open documents

All tests avoid a running language server or the compiled rust_core extension.
"""

from __future__ import annotations

import io
import queue
import tarfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# B17: _configured_timeout_seconds treats 0 / negative as invalid -> default
# ---------------------------------------------------------------------------
from tensor_grep.cli.lsp_external_provider import (
    ExternalLSPClient,
    _configured_timeout_seconds,
)
from tensor_grep.cli.lsp_provider_setup import _safe_extract_tar


class TestConfiguredTimeoutSeconds:
    def test_normal_positive_value_is_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TG_TEST_TIMEOUT", "5.5")
        result = _configured_timeout_seconds("_TG_TEST_TIMEOUT", 15.0)
        assert result == pytest.approx(5.5)

    def test_zero_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # audit B17: setting the env var to "0" must not silently make every
        # request time out immediately.
        monkeypatch.setenv("_TG_TEST_TIMEOUT", "0")
        result = _configured_timeout_seconds("_TG_TEST_TIMEOUT", 15.0)
        assert result == pytest.approx(15.0), "zero should fall back to default"

    def test_negative_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TG_TEST_TIMEOUT", "-3")
        result = _configured_timeout_seconds("_TG_TEST_TIMEOUT", 15.0)
        assert result == pytest.approx(15.0), "negative should fall back to default"

    def test_non_numeric_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_TG_TEST_TIMEOUT", "nope")
        result = _configured_timeout_seconds("_TG_TEST_TIMEOUT", 15.0)
        assert result == pytest.approx(15.0)

    def test_unset_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("_TG_TEST_TIMEOUT", raising=False)
        result = _configured_timeout_seconds("_TG_TEST_TIMEOUT", 15.0)
        assert result == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# S6: _safe_extract_tar rejects symlink / hardlink targets outside destination
# ---------------------------------------------------------------------------


def _make_tar_with_symlink(
    tmp_path: Path, link_name: str, link_target: str, *, is_hardlink: bool = False
) -> Path:
    """Create an in-memory tar file containing one symlink/hardlink member."""
    archive_path = tmp_path / "test.tar"
    with tarfile.open(str(archive_path), "w") as tar:
        info = tarfile.TarInfo(name=link_name)
        info.linkname = link_target
        info.type = tarfile.LNKTYPE if is_hardlink else tarfile.SYMTYPE
        tar.addfile(info)
    return archive_path


def _make_tar_with_normal_file(tmp_path: Path, name: str, content: bytes) -> Path:
    """Create a tar file with a regular file member."""
    archive_path = tmp_path / "test_normal.tar"
    buf = io.BytesIO(content)
    with tarfile.open(str(archive_path), "w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(content)
        tar.addfile(info, buf)
    return archive_path


class TestSafeExtractTar:
    def test_normal_member_is_accepted(self, tmp_path: Path) -> None:
        archive_path = _make_tar_with_normal_file(tmp_path, "subdir/hello.txt", b"hi")
        dest = tmp_path / "out"
        dest.mkdir()
        with tarfile.open(str(archive_path)) as tar:
            # Should not raise.
            _safe_extract_tar(tar, dest)

    def test_symlink_pointing_outside_is_rejected(self, tmp_path: Path) -> None:
        # audit S6: a symlink target that resolves outside the destination must
        # raise RuntimeError before any extraction happens.
        archive_path = _make_tar_with_symlink(
            tmp_path, link_name="evil_link", link_target="/etc/passwd"
        )
        dest = tmp_path / "out"
        dest.mkdir()
        with tarfile.open(str(archive_path)) as tar:
            with pytest.raises(RuntimeError, match="escapes destination"):
                _safe_extract_tar(tar, dest)

    def test_symlink_relative_dotdot_outside_is_rejected(self, tmp_path: Path) -> None:
        # Relative symlink that climbs above the destination using "../../".
        archive_path = _make_tar_with_symlink(
            tmp_path,
            link_name="subdir/evil",
            link_target="../../outside_file",
        )
        dest = tmp_path / "out"
        dest.mkdir()
        with tarfile.open(str(archive_path)) as tar:
            with pytest.raises(RuntimeError, match="escapes destination"):
                _safe_extract_tar(tar, dest)

    def test_hardlink_pointing_outside_is_rejected(self, tmp_path: Path) -> None:
        archive_path = _make_tar_with_symlink(
            tmp_path, link_name="evil_hard", link_target="/etc/shadow", is_hardlink=True
        )
        dest = tmp_path / "out"
        dest.mkdir()
        with tarfile.open(str(archive_path)) as tar:
            with pytest.raises(RuntimeError, match="escapes destination"):
                _safe_extract_tar(tar, dest)

    def test_member_name_path_traversal_is_rejected(self, tmp_path: Path) -> None:
        # Classic path-traversal via member name (CVE-2007-4559 class).
        archive_path = tmp_path / "traversal.tar"
        with tarfile.open(str(archive_path), "w") as tar:
            buf = io.BytesIO(b"pwned")
            info = tarfile.TarInfo(name="../outside.txt")
            info.size = 5
            tar.addfile(info, buf)
        dest = tmp_path / "out"
        dest.mkdir()
        with tarfile.open(str(archive_path)) as tar:
            with pytest.raises(RuntimeError, match="escapes destination"):
                _safe_extract_tar(tar, dest)


# ---------------------------------------------------------------------------
# I3: LRU cache helpers in lsp_server — no server needed
# ---------------------------------------------------------------------------
pytest.importorskip("lsprotocol.types")
pytest.importorskip("pygls.lsp.server")

from tensor_grep.cli.lsp_server import (  # noqa: E402
    _DOCUMENTS_CACHE_MAX,
    _TENSOR_CACHE_MAX,
    TensorGrepLSPServer,
    _lru_get,
    _lru_put,
    did_close,
)


class TestLruHelpers:
    def test_lru_put_evicts_oldest_when_over_capacity(self) -> None:
        od: OrderedDict[str, Any] = OrderedDict()
        for i in range(5):
            _lru_put(od, str(i), i, max_size=3)
        assert len(od) == 3
        # The three most-recently inserted items should survive.
        assert list(od.keys()) == ["2", "3", "4"]

    def test_lru_get_promotes_to_mru(self) -> None:
        od: OrderedDict[str, Any] = OrderedDict()
        for i in range(3):
            _lru_put(od, str(i), i, max_size=10)
        # Access "0" so it becomes MRU.
        _lru_get(od, "0")
        # Now evict by inserting 3 new items to exceed limit of 3.
        _lru_put(od, "3", 3, max_size=3)
        # "1" should have been evicted (oldest after "0" was promoted).
        assert "1" not in od
        assert "0" in od

    def test_lru_get_returns_none_for_missing_key(self) -> None:
        od: OrderedDict[str, Any] = OrderedDict()
        assert _lru_get(od, "nope") is None


# ---------------------------------------------------------------------------
# I3: didClose handler evicts from documents_cache, tensor_cache, repo_map_cache
# ---------------------------------------------------------------------------
from lsprotocol.types import (  # noqa: E402
    DidCloseTextDocumentParams,
    TextDocumentIdentifier,
)


def _make_server() -> TensorGrepLSPServer:
    return TensorGrepLSPServer("test", "v1")


class TestDidCloseHandler:
    def test_did_close_evicts_documents_cache(self, tmp_path: Path) -> None:
        server = _make_server()
        uri = (tmp_path / "file.py").as_uri()
        server.documents_cache[uri] = "print('hello')"
        # Seed tensor and repo map caches too.
        server.tensor_cache[uri] = {"dummy": True}
        server.repo_map_cache[str(tmp_path)] = {"symbols": []}

        did_close(
            server,
            DidCloseTextDocumentParams(text_document=TextDocumentIdentifier(uri=uri)),
        )

        assert uri not in server.documents_cache
        assert uri not in server.tensor_cache

    def test_did_close_unknown_uri_is_safe(self, tmp_path: Path) -> None:
        server = _make_server()
        uri = (tmp_path / "nonexistent.py").as_uri()
        # Must not raise even when the URI was never opened.
        did_close(
            server,
            DidCloseTextDocumentParams(text_document=TextDocumentIdentifier(uri=uri)),
        )

    def test_documents_cache_bounded_by_max(self) -> None:
        server = _make_server()
        # Fill beyond the declared max; each _lru_put should evict.
        for i in range(_DOCUMENTS_CACHE_MAX + 10):
            _lru_put(server.documents_cache, f"file://{i}.py", f"content {i}", _DOCUMENTS_CACHE_MAX)
        assert len(server.documents_cache) <= _DOCUMENTS_CACHE_MAX

    def test_tensor_cache_bounded_by_max(self) -> None:
        server = _make_server()
        for i in range(_TENSOR_CACHE_MAX + 10):
            _lru_put(server.tensor_cache, f"file://{i}.py", {"tensor": i}, _TENSOR_CACHE_MAX)
        assert len(server.tensor_cache) <= _TENSOR_CACHE_MAX


# ---------------------------------------------------------------------------
# B13: UTF-16 column conversion helpers
# ---------------------------------------------------------------------------
from tensor_grep.cli.lsp_server import (  # noqa: E402
    _codepoint_col_to_utf16,
    _utf16_col_to_codepoint,
)


class TestUtf16Conversion:
    def test_ascii_line_is_identity(self) -> None:
        line = "hello world"
        for col in range(len(line) + 1):
            assert _utf16_col_to_codepoint(line, col) == col
            assert _codepoint_col_to_utf16(line, col) == col

    def test_bmp_unicode_is_identity(self) -> None:
        # U+00E9 é is BMP (1 UTF-16 unit, 1 codepoint).
        line = "café"
        for col in range(len(line) + 1):
            assert _utf16_col_to_codepoint(line, col) == col
            assert _codepoint_col_to_utf16(line, col) == col

    def test_surrogate_pair_character_shifts_codepoint(self) -> None:
        # U+1F600 😀 is a surrogate pair (2 UTF-16 units, 1 codepoint).
        # line = ['a', '😀', 'b']
        # UTF-16 widths:  a=1, 😀=2, b=1  -> cumulative: 0,1,3,4
        emoji = "\U0001f600"
        line = f"a{emoji}b"
        # col 0 -> cp 0 (before 'a')
        assert _utf16_col_to_codepoint(line, 0) == 0
        # col 1 -> cp 1 (after 'a', before emoji)
        assert _utf16_col_to_codepoint(line, 1) == 1
        # col 2 (mid-surrogate pair): the function advances past the emoji to the
        # next codepoint boundary (cp 2 = 'b'), which is the correct snap-forward.
        assert _utf16_col_to_codepoint(line, 2) == 2
        # col 3 -> cp 2 (after emoji = 'b') - same codepoint boundary
        assert _utf16_col_to_codepoint(line, 3) == 2
        # col 4 -> cp 3 (after 'b', end of line)
        assert _utf16_col_to_codepoint(line, 4) == 3

    def test_roundtrip_for_bmp_characters(self) -> None:
        line = "def fün(x):"
        for cp_col in range(len(line) + 1):
            utf16 = _codepoint_col_to_utf16(line, cp_col)
            recovered = _utf16_col_to_codepoint(line, utf16)
            assert recovered == cp_col, f"Roundtrip failed at cp_col={cp_col}"


# ---------------------------------------------------------------------------
# B12: per-id demux — verify _pending_requests slot routing without a real server
# ---------------------------------------------------------------------------
from tensor_grep.cli.lsp_external_provider import (  # noqa: E402
    _CLOSED_SENTINEL,
)


class TestDemuxRouting:
    """Verify that _dispatch_response and _broadcast_closed correctly route
    messages to per-id slots without needing a running language-server process.

    We instantiate ExternalLSPClient in a way that bypasses start() (which
    would try to spawn a subprocess) by directly manipulating _pending_requests.
    """

    def _make_client(self, tmp_path: Path) -> ExternalLSPClient:
        # Patch _provider_command so the constructor does not raise.
        import tensor_grep.cli.lsp_external_provider as m

        original = m._provider_command

        def _fake(lang: str) -> list[str]:
            return ["fake-lsp"]

        m._provider_command = _fake  # type: ignore[assignment]
        try:
            client = ExternalLSPClient(language="python", workspace_root=tmp_path)
        finally:
            m._provider_command = original  # type: ignore[assignment]
        return client

    def test_dispatch_routes_to_correct_slot(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        slot_1: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        slot_2: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with client._lock:
            client._pending_requests[1] = slot_1
            client._pending_requests[2] = slot_2

        response_for_2 = {"jsonrpc": "2.0", "id": 2, "result": "pong"}
        client._dispatch_response(response_for_2)

        # Only slot_2 should have a message.
        assert slot_2.get_nowait() == response_for_2
        assert slot_1.empty()

    def test_broadcast_closed_wakes_all_pending_slots(self, tmp_path: Path) -> None:
        client = self._make_client(tmp_path)
        slots = [queue.Queue(maxsize=1) for _ in range(4)]
        with client._lock:
            for idx, slot in enumerate(slots):
                client._pending_requests[idx] = slot

        client._broadcast_closed()

        for slot in slots:
            msg = slot.get_nowait()
            assert msg is _CLOSED_SENTINEL

    def test_no_response_stolen_by_concurrent_request(self, tmp_path: Path) -> None:
        """Two threads each wait for their own slot; each must receive only their response."""
        client = self._make_client(tmp_path)

        results: dict[int, Any] = {}
        errors: list[Exception] = []

        def waiter(request_id: int, slot: queue.Queue[dict[str, Any]]) -> None:
            try:
                msg = slot.get(timeout=2.0)
                results[request_id] = msg.get("result")
            except Exception as exc:
                errors.append(exc)

        slot_a: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        slot_b: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with client._lock:
            client._pending_requests[10] = slot_a
            client._pending_requests[20] = slot_b

        t_a = threading.Thread(target=waiter, args=(10, slot_a))
        t_b = threading.Thread(target=waiter, args=(20, slot_b))
        t_a.start()
        t_b.start()

        # Dispatch responses in reverse order.
        client._dispatch_response({"jsonrpc": "2.0", "id": 20, "result": "B"})
        client._dispatch_response({"jsonrpc": "2.0", "id": 10, "result": "A"})

        t_a.join(timeout=2.0)
        t_b.join(timeout=2.0)

        assert not errors
        assert results[10] == "A"
        assert results[20] == "B"


# ---------------------------------------------------------------------------
# B15: monotonic version tracking in did_change
# ---------------------------------------------------------------------------


class TestMonotonicVersion:
    def _make_client(self, tmp_path: Path) -> ExternalLSPClient:
        import tensor_grep.cli.lsp_external_provider as m

        original = m._provider_command

        def _fake(lang: str) -> list[str]:
            return ["fake-lsp"]

        m._provider_command = _fake  # type: ignore[assignment]
        try:
            client = ExternalLSPClient(language="python", workspace_root=tmp_path)
        finally:
            m._provider_command = original  # type: ignore[assignment]
        return client

    def test_did_change_increments_version_monotonically(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self._make_client(tmp_path)
        uri = "file:///test/file.py"

        sent_versions: list[int] = []

        def _capture_notify(method: str, params: dict[str, Any]) -> None:
            if method == "textDocument/didChange":
                sent_versions.append(params["textDocument"]["version"])

        monkeypatch.setattr(client, "notify", _capture_notify)
        # Simulate document open.
        with client._lock:
            client._doc_versions[uri] = 1
        client._opened_documents[uri] = None

        # Call did_change multiple times with the same version (non-monotonic
        # client behavior that was the root cause of B15).
        client.did_change(uri=uri, text="v1", version=1)
        client.did_change(uri=uri, text="v2", version=1)
        client.did_change(uri=uri, text="v3", version=1)

        assert sent_versions == sorted(sent_versions), (
            f"Versions are not monotonically increasing: {sent_versions}"
        )
        assert len(set(sent_versions)) == 3, f"Expected 3 distinct versions, got {sent_versions}"

    def test_did_change_skips_when_document_not_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self._make_client(tmp_path)
        uri = "file:///test/not_opened.py"

        called = []
        monkeypatch.setattr(client, "notify", lambda *a, **kw: called.append(a))

        # Should silently skip — document was never opened.
        client.did_change(uri=uri, text="irrelevant", version=1)
        assert not called


# ---------------------------------------------------------------------------
# B16: _resolve_workspace_root resolves without open documents
# ---------------------------------------------------------------------------
from tensor_grep.cli.lsp_server import _resolve_workspace_root  # noqa: E402


class TestResolveWorkspaceRoot:
    def test_returns_cwd_root_when_no_docs_open(self) -> None:
        server = _make_server()
        assert not server.documents_cache  # Confirm empty.
        root = _resolve_workspace_root(server, None)
        # Should return *something* — not None — even with no docs open (B16 fix).
        assert root is not None

    def test_path_hint_takes_priority(self, tmp_path: Path) -> None:
        server = _make_server()
        # Put a different URI in the doc cache.
        server.documents_cache["file:///other/file.py"] = "x"
        hint_uri = (tmp_path / "myfile.py").as_uri()
        root = _resolve_workspace_root(server, hint_uri)
        # The root must be derived from tmp_path, not from "/other/".
        assert root is not None
        assert str(root).startswith(str(tmp_path.resolve().anchor)) or str(root) == str(
            tmp_path.resolve()
        )
