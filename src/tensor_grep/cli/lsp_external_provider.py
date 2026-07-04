from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, cast

from tensor_grep.cli.lsp_provider_setup import (
    canonical_language,
    direct_managed_node_command,
    managed_provider_env,
    resolved_provider_command,
    wrap_windows_batch_command,
)
from tensor_grep.cli.lsp_provider_setup import (
    managed_provider_root as _managed_provider_root,
)


class LSPTransportError(RuntimeError):
    pass


_DEFAULT_LSP_REQUEST_TIMEOUT_SECONDS = 15.0
_DEFAULT_LSP_INITIALIZE_TIMEOUT_SECONDS = 15.0
_DEFAULT_LSP_STOP_TIMEOUT_SECONDS = 1.0
_DEFAULT_LSP_PROVIDER_CLIENT_CACHE_MAX_ENTRIES = 8
_DEFAULT_LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES = 64
_LSP_REQUEST_TIMEOUT_ENV_VAR = "TENSOR_GREP_LSP_REQUEST_TIMEOUT_SECONDS"
_LSP_INITIALIZE_TIMEOUT_ENV_VAR = "TENSOR_GREP_LSP_INITIALIZE_TIMEOUT_SECONDS"
_LSP_PROVIDER_CLIENT_CACHE_MAX_ENTRIES_ENV_VAR = "TENSOR_GREP_LSP_PROVIDER_CLIENT_CACHE_MAX_ENTRIES"
_LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES_ENV_VAR = (
    "TENSOR_GREP_LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES"
)
_GENERATED_CACHE_EXCLUDES = [
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
]

# audit B12: sentinel used as "process closed" marker in per-id response slots.
# Identity-checked (``is``), never equal to a real JSON-RPC response dict.
_CLOSED_SENTINEL: dict[str, Any] = {}
# Bound on buffered responses whose request slot is not yet registered (audit B12).
_MAX_ORPHAN_RESPONSES = 64


def _configured_timeout_seconds(env_var: str, default: float) -> float:
    # audit B17: treat 0 and negative values as invalid -> use default rather
    # than instantly-timing-out every request.  None (unbounded) is expressed
    # by callers passing float("inf") explicitly; env vars cannot select that.
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        return default
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0.0 else default


def _configured_positive_int(env_var: str, default: int) -> int:
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Audit MED (DoS): cap the framed-message body size so a malicious or buggy external LSP
# provider cannot declare a huge Content-Length and force an unbounded read/allocation.
_MAX_LSP_MESSAGE_BYTES = 64 * 1024 * 1024


def _read_message(stream: Any) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if line in ("", b""):
            return None
        if line in ("\r\n", "\n", b"\r\n", b"\n"):
            break
        if isinstance(line, bytes):
            line = line.decode("ascii", errors="replace")
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    try:
        content_length = int(headers.get("content-length", "0"))
    except (TypeError, ValueError):
        return None
    if content_length <= 0:
        return None
    if content_length > _MAX_LSP_MESSAGE_BYTES:
        # Refuse an oversized frame rather than allocating/reading an unbounded body.
        return None
    body = stream.read(content_length)
    if not body:
        return None
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        return None
    return cast(dict[str, Any], parsed)


def _write_message(stream: Any, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":"))
    encoded = body.encode("utf-8")
    framed = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii") + encoded
    try:
        stream.write(framed)
    except TypeError:
        stream.write(framed.decode("utf-8"))
    stream.flush()


def _provider_command(language: str) -> list[str]:
    normalized = canonical_language(language)
    command = resolved_provider_command(normalized, managed_root=_managed_provider_root())
    if command is not None:
        return command
    missing_binary_by_language = {
        "python": "pyright-langserver",
        "javascript": "typescript-language-server",
        "typescript": "typescript-language-server",
        "go": "gopls",
        "rust": "rust-analyzer",
        "java": "jdtls",
        "c": "clangd",
        "cpp": "clangd",
        "csharp": "csharp-ls",
        "php": "intelephense",
        "kotlin": "kotlin-lsp",
        "swift": "sourcekit-lsp",
        "lua": "lua-language-server",
    }
    if normalized in missing_binary_by_language:
        raise FileNotFoundError(f"{missing_binary_by_language[normalized]} binary not found")
    raise ValueError(f"Unsupported LSP language: {language}")


def _configuration_settings(language: str) -> dict[str, Any]:
    if canonical_language(language) != "python":
        return {"settings": {}}
    return {
        "settings": {
            "python": {
                "analysis": {
                    "diagnosticMode": "openFilesOnly",
                    "exclude": list(_GENERATED_CACHE_EXCLUDES),
                }
            }
        }
    }


def _health_probe_document(language: str, workspace_root: Path) -> dict[str, str]:
    normalized = canonical_language(language)
    probes = {
        "python": (
            "__tg_lsp_health_probe.py",
            "python",
            "def tg_lsp_health_probe():\n    return 1\n",
            "tg_lsp_health_probe",
        ),
        "javascript": (
            "__tg_lsp_health_probe.js",
            "javascript",
            "function tgLspHealthProbe() { return 1; }\n",
            "tgLspHealthProbe",
        ),
        "typescript": (
            "__tg_lsp_health_probe.ts",
            "typescript",
            "function tgLspHealthProbe(): number { return 1; }\n",
            "tgLspHealthProbe",
        ),
        "go": (
            "__tg_lsp_health_probe.go",
            "go",
            "package main\n\nfunc tgLspHealthProbe() int { return 1 }\n",
            "tgLspHealthProbe",
        ),
        "rust": (
            "__tg_lsp_health_probe.rs",
            "rust",
            "fn tg_lsp_health_probe() -> i32 { 1 }\n",
            "tg_lsp_health_probe",
        ),
        "java": (
            "__TgLspHealthProbe.java",
            "java",
            "class TgLspHealthProbe { int tgLspHealthProbe() { return 1; } }\n",
            "TgLspHealthProbe",
        ),
        "c": (
            "__tg_lsp_health_probe.c",
            "c",
            "int tg_lsp_health_probe(void) { return 1; }\n",
            "tg_lsp_health_probe",
        ),
        "cpp": (
            "__tg_lsp_health_probe.cpp",
            "cpp",
            "int tg_lsp_health_probe() { return 1; }\n",
            "tg_lsp_health_probe",
        ),
        "csharp": (
            "__TgLspHealthProbe.cs",
            "csharp",
            "class TgLspHealthProbe { int Probe() { return 1; } }\n",
            "TgLspHealthProbe",
        ),
        "php": (
            "__tg_lsp_health_probe.php",
            "php",
            "<?php function tg_lsp_health_probe() { return 1; }\n",
            "tg_lsp_health_probe",
        ),
        "kotlin": (
            "__TgLspHealthProbe.kt",
            "kotlin",
            "fun tgLspHealthProbe(): Int = 1\n",
            "tgLspHealthProbe",
        ),
        "swift": (
            "__TgLspHealthProbe.swift",
            "swift",
            "func tgLspHealthProbe() -> Int { return 1 }\n",
            "tgLspHealthProbe",
        ),
        "lua": (
            "__tg_lsp_health_probe.lua",
            "lua",
            "function tg_lsp_health_probe()\n  return 1\nend\n",
            "tg_lsp_health_probe",
        ),
    }
    filename, language_id, text, symbol = probes.get(
        normalized,
        (
            "__tg_lsp_health_probe.txt",
            normalized,
            "tg_lsp_health_probe\n",
            "tg_lsp_health_probe",
        ),
    )
    return {
        "uri": (workspace_root.resolve() / filename).as_uri(),
        "language_id": language_id,
        "text": text,
        "symbol": symbol,
    }


def _document_symbol_names(result: object) -> list[str]:
    names: list[str] = []
    if not isinstance(result, list):
        return names
    for item in result:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str):
            names.append(name)
        names.extend(_document_symbol_names(item.get("children")))
    return names


def _document_symbol_result_contains(result: object, expected_symbol: str) -> bool:
    return expected_symbol in _document_symbol_names(result)


def _lookup_configuration_section(settings: dict[str, Any], section: object) -> Any:
    if not isinstance(section, str) or not section:
        return settings
    current: Any = settings
    for part in section.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class ExternalLSPClient:
    def __init__(
        self,
        *,
        language: str,
        workspace_root: Path,
        request_timeout_seconds: float | None = None,
        initialize_timeout_seconds: float | None = None,
        retry_cooldown_seconds: float = 30.0,
        max_open_documents: int | None = None,
    ) -> None:
        self.language = language
        self.workspace_root = workspace_root.resolve()
        self.command = _provider_command(language)
        self.process: subprocess.Popen[Any] | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        # Serializes start()'s check-then-spawn so concurrent daemon worker threads sharing this
        # cached client cannot both Popen (round-6 r9). SEPARATE from _lock: start()'s initialize
        # handshake calls request() which takes _lock, so reusing it would re-entrant-deadlock.
        self._start_lock = threading.Lock()
        self._max_open_documents = (
            _configured_positive_int(
                _LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES_ENV_VAR,
                _DEFAULT_LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES,
            )
            if max_open_documents is None
            else max(1, int(max_open_documents))
        )
        self._opened_documents: OrderedDict[str, None] = OrderedDict()
        self._message_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        # audit B12: per-request-id response slots for correct demultiplexing.
        # Maps request_id -> Queue that receives exactly one response dict (or
        # _CLOSED_SENTINEL on EOF).  Guarded by _lock.
        self._pending_requests: dict[int, queue.Queue[dict[str, Any]]] = {}
        # audit B12: responses whose slot is not yet registered (a pre-queued/early
        # response that the reader dispatched before request() registered its slot).
        # A bounded buffer so request() can still claim it; without this the demux
        # would silently drop such responses where the old shared queue buffered them.
        self._orphan_responses: dict[int, dict[str, Any]] = {}
        # audit B15: monotonically-incrementing per-URI document version counter.
        # Keyed by URI; never decremented.  Guarded by _lock.
        self._doc_versions: dict[str, int] = {}
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_tail: list[str] = []
        self._debug_trace_enabled = False
        self._debug_trace_started_monotonic = time.monotonic()
        self._debug_trace: list[dict[str, Any]] = []
        self.request_timeout_seconds = (
            _configured_timeout_seconds(
                _LSP_REQUEST_TIMEOUT_ENV_VAR, _DEFAULT_LSP_REQUEST_TIMEOUT_SECONDS
            )
            if request_timeout_seconds is None
            else max(float(request_timeout_seconds), 0.0)
        )
        self.initialize_timeout_seconds = (
            _configured_timeout_seconds(
                _LSP_INITIALIZE_TIMEOUT_ENV_VAR, _DEFAULT_LSP_INITIALIZE_TIMEOUT_SECONDS
            )
            if initialize_timeout_seconds is None
            else max(float(initialize_timeout_seconds), 0.0)
        )
        self.retry_cooldown_seconds = retry_cooldown_seconds
        self.capabilities: dict[str, Any] = {}
        self.last_error: str | None = None
        self.disabled_until_monotonic = 0.0
        self.initialized = False
        self.lsp_provider_response = False
        # P0-2 readiness gate (warm-LSP moat): track server indexing via workDoneProgress tokens
        # so the first references/definitions per (root,language) can wait for the index to
        # settle instead of answering from a half-built index (the 2-of-14 under-return).
        # Guarded by _lock. _index_ready is the cached "settled" verdict; any new
        # create/begin re-invalidates it (server re-indexing after file churn).
        self._active_progress_tokens: set[str] = set()
        self._progress_end_count = 0
        self._progress_activity_seen = False
        self._index_ready = False

    def enable_debug_trace(self) -> None:
        self._debug_trace_enabled = True
        self._debug_trace_started_monotonic = time.monotonic()
        self._debug_trace = []

    def debug_trace(self) -> list[dict[str, Any]]:
        return list(self._debug_trace)

    def stderr_tail(self) -> list[str]:
        return list(self._stderr_tail)

    def _record_debug_trace(
        self,
        *,
        event: str,
        method: str | None = None,
        request_id: object | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if not self._debug_trace_enabled:
            return
        entry: dict[str, Any] = {
            "event": event,
            "elapsed_ms": round(
                (time.monotonic() - self._debug_trace_started_monotonic) * 1000.0,
                3,
            ),
        }
        if method is not None:
            entry["method"] = method
        if request_id is not None:
            entry["id"] = request_id
        if detail:
            entry.update(detail)
        self._debug_trace.append(entry)

    def start(self) -> None:
        # Fast path (no lock): already running.
        if self.process is not None and self.process.poll() is None:
            return
        # Serialize the check-then-spawn (round-6 r9): two daemon worker threads calling into the
        # SAME cached client (get_client is shared per (root,language)) must not both pass the None
        # check and both Popen, orphaning one child. Double-checked under _start_lock.
        with self._start_lock:
            if self.process is not None and self.process.poll() is None:
                return
            self._start_locked()

    def _start_locked(self) -> None:
        if self.disabled_until_monotonic > time.monotonic():
            raise LSPTransportError(self.last_error or "LSP provider temporarily unavailable")
        if self.process is not None:
            self.stop()
        managed_root = _managed_provider_root()
        try:
            spawn_argv = direct_managed_node_command(list(self.command), root=managed_root)
        except (ValueError, FileNotFoundError, OSError) as exc:
            # Fail CLOSED (CWE-427): a managed Node cmd-shim whose trusted node.exe / JS
            # entrypoint cannot be resolved must NOT fall back to the cmd.exe/.cmd path —
            # that path's bare `node` resolves CWD-first against the attacker-controlled
            # workspace_root. Silent fallback would re-open the exact hole we are closing.
            raise LSPTransportError(
                f"managed LSP provider could not be resolved to a trusted node runtime: {exc}"
            ) from exc
        if spawn_argv is None:
            # External/PATH providers, managed native .exe binaries, and all POSIX are
            # unchanged (wrap_windows_batch_command is a no-op except for a real .cmd/.bat).
            spawn_argv = wrap_windows_batch_command(list(self.command))
        # cwd stays workspace_root: the resolved argv contains zero CWD-searchable names, so
        # this launch is safe. Residual (not exploitable here — these servers are
        # worker-thread based): a server that itself spawns a bare-name grandchild at runtime
        # could recur one level down.
        self.process = subprocess.Popen(
            spawn_argv,
            cwd=str(self.workspace_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=managed_provider_env(self.command, managed_root=managed_root),
        )
        self._record_debug_trace(
            event="process_start",
            detail={"command": spawn_argv, "cwd": str(self.workspace_root)},
        )
        self._message_queue = queue.Queue()
        self._pending_requests = {}
        self._orphan_responses = {}
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_tail = []
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_thread.start()
        try:
            result = self.request(
                "initialize",
                {
                    "processId": None,
                    "rootUri": self.workspace_root.as_uri(),
                    "capabilities": {
                        "workspace": {
                            "configuration": True,
                            "workspaceFolders": True,
                        }
                    },
                    "initializationOptions": _configuration_settings(self.language).get(
                        "settings", {}
                    ),
                    "workspaceFolders": [
                        {
                            "uri": self.workspace_root.as_uri(),
                            "name": self.workspace_root.name,
                        }
                    ],
                },
            )
        except LSPTransportError as exc:
            self.last_error = str(exc)
            self.disabled_until_monotonic = time.monotonic() + self.retry_cooldown_seconds
            self.stop()
            raise
        if isinstance(result, dict):
            self.capabilities = dict(result.get("capabilities", {}))
        self.notify("initialized", {})
        self.initialized = True
        try:
            self.notify("workspace/didChangeConfiguration", _configuration_settings(self.language))
        except Exception:
            pass

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        reader_thread = self._reader_thread
        stderr_thread = self._stderr_thread
        stop_timeout_seconds = min(
            max(float(self.request_timeout_seconds), 0.0),
            _DEFAULT_LSP_STOP_TIMEOUT_SECONDS,
        )
        self._request_shutdown_for_stop()
        with self._lock:
            try:
                self._write_notification("exit", None)
            except Exception:
                pass
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except Exception:
                pass
            try:
                process.terminate()
            except Exception:
                pass
        try:
            process.wait(timeout=stop_timeout_seconds)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except Exception:
                pass
            try:
                process.wait(timeout=stop_timeout_seconds)
            except Exception:
                pass
        finally:
            for stream in (process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
        if reader_thread is not None and reader_thread.is_alive():
            reader_thread.join(timeout=stop_timeout_seconds)
        if stderr_thread is not None and stderr_thread.is_alive():
            stderr_thread.join(timeout=stop_timeout_seconds)
        with self._lock:
            if self.process is process:
                self.process = None
            self._opened_documents.clear()
            self.capabilities = {}
            self.initialized = False
            self.lsp_provider_response = False
            if self._reader_thread is reader_thread:
                self._reader_thread = None
            if self._stderr_thread is stderr_thread:
                self._stderr_thread = None
            self._message_queue = queue.Queue()
            # audit B12: unblock any callers still waiting in request().
            for slot in self._pending_requests.values():
                slot.put_nowait(_CLOSED_SENTINEL)
            self._pending_requests = {}
            self._orphan_responses = {}
            self._doc_versions = {}

    def _request_shutdown_for_stop(self) -> None:
        # audit B12: use a per-id slot so the shutdown request cannot race with
        # any concurrent request() calls that are still in flight.
        process = self.process
        if process is None or process.stdin is None:
            return
        slot: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        try:
            with self._lock:
                self._request_id += 1
                request_id = self._request_id
                self._pending_requests[request_id] = slot
                self._write_request(request_id, "shutdown", None)
                buffered = self._orphan_responses.pop(request_id, None)
            if buffered is not None:
                try:
                    slot.put_nowait(buffered)
                except queue.Full:
                    pass
        except Exception:
            return
        timeout_seconds = min(
            max(float(self.request_timeout_seconds), 0.0),
            _DEFAULT_LSP_STOP_TIMEOUT_SECONDS,
        )
        try:
            slot.get(timeout=timeout_seconds)
        except queue.Empty:
            pass
        finally:
            with self._lock:
                self._pending_requests.pop(request_id, None)

    def request(self, method: str, params: dict[str, Any]) -> Any:
        # audit B12: each in-flight request gets its own one-shot Queue so that
        # concurrent calls cannot steal each other's responses.
        self.start()
        if self.process is None or self.process.stdin is None or self.process.stdout is None:
            raise LSPTransportError("LSP process is not available")
        timeout_seconds = (
            self.initialize_timeout_seconds
            if method == "initialize"
            else self.request_timeout_seconds
        )
        slot: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            self._pending_requests[request_id] = slot
            self._write_request(request_id, method, params)
            # Claim a response that the reader dispatched before this slot existed.
            buffered = self._orphan_responses.pop(request_id, None)
        if buffered is not None:
            try:
                slot.put_nowait(buffered)
            except queue.Full:
                pass
        try:
            try:
                message = slot.get(timeout=timeout_seconds)
            except queue.Empty as exc:
                self.last_error = f"timeout waiting for LSP response: {method}"
                self._record_debug_trace(
                    event="request_timeout",
                    method=method,
                    request_id=request_id,
                    detail={"timeout_seconds": timeout_seconds},
                )
                raise LSPTransportError(self.last_error) from exc
            if message is _CLOSED_SENTINEL:
                self.last_error = f"LSP process closed during request: {method}"
                self._record_debug_trace(
                    event="request_closed",
                    method=method,
                    request_id=request_id,
                )
                raise LSPTransportError(self.last_error)
            if "error" in message:
                self.last_error = str(message["error"])
                self._record_debug_trace(
                    event="receive_error",
                    method=method,
                    request_id=request_id,
                    detail={"error": message["error"]},
                )
                raise LSPTransportError(self.last_error)
            self.last_error = None
            self._record_debug_trace(
                event="receive_response",
                method=method,
                request_id=request_id,
                detail={"result_type": type(message.get("result")).__name__},
            )
            return message.get("result")
        finally:
            with self._lock:
                self._pending_requests.pop(request_id, None)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.start()
        if self.process is None or self.process.stdin is None:
            raise LSPTransportError("LSP process is not available")
        with self._lock:
            self._write_notification(method, params)

    def ensure_document(self, *, uri: str, text: str, language_id: str) -> None:
        if uri in self._opened_documents:
            self._opened_documents.move_to_end(uri)
            return
        evicted_uri = (
            next(iter(self._opened_documents))
            if len(self._opened_documents) >= self._max_open_documents
            else None
        )
        # audit B15: record the initial version so did_change can monotonically
        # increment from it.
        with self._lock:
            self._doc_versions[uri] = 1
        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )
        self._opened_documents[uri] = None
        if evicted_uri is not None:
            self._opened_documents.pop(evicted_uri, None)
            self._notify_document_closed(evicted_uri)

    def _notify_document_closed(self, uri: str) -> None:
        # Audit LOW (leak): evict the per-URI version counter on close, mirroring the
        # _opened_documents cleanup. Both removal paths (open-eviction and close_document)
        # funnel through here, so _doc_versions no longer grows unbounded across a
        # long-lived client's lifetime. _doc_versions is _lock-guarded (see did_change).
        with self._lock:
            self._doc_versions.pop(uri, None)
        try:
            self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        except Exception:
            self._record_debug_trace(
                event="document_close_failed",
                method="textDocument/didClose",
                detail={"uri": uri},
            )

    def close_document(self, *, uri: str) -> None:
        if uri not in self._opened_documents:
            return
        self._opened_documents.pop(uri, None)
        self._notify_document_closed(uri)

    def did_change(self, *, uri: str, text: str, version: int = 1) -> None:
        # audit B15: ignore the caller-supplied version (which can be non-monotonic
        # when multiple editors send version=1) and use an internal counter instead.
        if uri not in self._opened_documents:
            return
        with self._lock:
            current_version = self._doc_versions.get(uri, 1)
            next_version = max(current_version + 1, version + 1)
            self._doc_versions[uri] = next_version
        self.notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": next_version},
                "contentChanges": [{"text": text}],
            },
        )

    def did_save(self, *, uri: str) -> None:
        if uri not in self._opened_documents:
            return
        self.notify("textDocument/didSave", {"textDocument": {"uri": uri}})

    def status(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "workspace_root": str(self.workspace_root),
            "command": list(self.command),
            "command_source": _command_source(self.command),
            "managed_provider_root": str(_managed_provider_root()),
            "running": self.process is not None and self.process.poll() is None,
            "initialized": self.initialized,
            "capabilities": dict(self.capabilities),
            "lsp_provider_response": self.lsp_provider_response,
            "last_error": self.last_error,
            "opened_documents": len(self._opened_documents),
            "max_open_documents": self._max_open_documents,
            "stderr_tail": self.stderr_tail(),
            "request_timeout_seconds": self.request_timeout_seconds,
            "initialize_timeout_seconds": self.initialize_timeout_seconds,
            "cooldown_remaining_s": max(0.0, self.disabled_until_monotonic - time.monotonic()),
        }

    def _write_request(self, request_id: int, method: str, params: dict[str, Any] | None) -> None:
        if self.process is None or self.process.stdin is None:
            raise LSPTransportError("LSP process is not available")
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._record_debug_trace(
            event="send_request",
            method=method,
            request_id=request_id,
            detail={"params_keys": sorted(params.keys()) if isinstance(params, dict) else []},
        )
        _write_message(self.process.stdin, payload)

    def _write_notification(self, method: str, params: dict[str, Any] | None) -> None:
        if self.process is None or self.process.stdin is None:
            raise LSPTransportError("LSP process is not available")
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._record_debug_trace(
            event="send_notification",
            method=method,
            detail={"params_keys": sorted(params.keys()) if isinstance(params, dict) else []},
        )
        _write_message(
            self.process.stdin,
            payload,
        )

    def _write_response(self, request_id: object, result: Any) -> None:
        if self.process is None or self.process.stdin is None:
            raise LSPTransportError("LSP process is not available")
        payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
        self._record_debug_trace(
            event="send_response",
            request_id=request_id,
            detail={"result_type": type(result).__name__},
        )
        _write_message(self.process.stdin, payload)

    def _write_error_response(self, request_id: object, *, code: int, message: str) -> None:
        if self.process is None or self.process.stdin is None:
            raise LSPTransportError("LSP process is not available")
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
        self._record_debug_trace(
            event="send_error_response",
            request_id=request_id,
            detail={"code": code, "message": message},
        )
        _write_message(self.process.stdin, payload)

    def _configuration_response(self, params: object) -> list[Any]:
        settings = _configuration_settings(self.language).get("settings", {})
        if not isinstance(params, dict):
            return [settings]
        items = params.get("items")
        if not isinstance(items, list):
            return [settings]
        return [
            _lookup_configuration_section(
                settings,
                item.get("section") if isinstance(item, dict) else None,
            )
            for item in items
        ]

    def _handle_server_request(self, message: dict[str, Any]) -> bool:
        if "id" not in message or "method" not in message:
            return False
        method = str(message.get("method"))
        request_id = message.get("id")
        try:
            if method == "workspace/configuration":
                result = self._configuration_response(message.get("params"))
            elif method == "workspace/workspaceFolders":
                result = [{"uri": self.workspace_root.as_uri(), "name": self.workspace_root.name}]
            elif method in {
                "client/registerCapability",
                "client/unregisterCapability",
            }:
                result = None
            elif method == "window/workDoneProgress/create":
                # P0-2: register the token as an in-flight indexing round (not just ACK it) so
                # wait_until_ready blocks until its $/progress end arrives. A created-but-not-yet-
                # begun token counts as active: the create->begin window must not read as "ready".
                token = (message.get("params") or {}).get("token")
                if token is not None:
                    self._note_progress_started(str(token))
                result = None
            else:
                with self._lock:
                    self._write_error_response(
                        request_id,
                        code=-32601,
                        message=f"Unsupported LSP server request: {method}",
                    )
                return True
            with self._lock:
                self._write_response(request_id, result)
            return True
        except Exception as exc:
            try:
                with self._lock:
                    self._write_error_response(request_id, code=-32603, message=str(exc))
            except Exception:
                pass
            return True

    def _reader_loop(self) -> None:
        # audit B12: route each response to the per-id slot registered by request().
        process = self.process
        if process is None or process.stdout is None:
            self._broadcast_closed()
            return
        try:
            while True:
                message = _read_message(process.stdout)
                if message is None:
                    self._record_debug_trace(event="process_stdout_closed")
                    self._broadcast_closed()
                    return
                if self._handle_server_request(message):
                    continue
                self._record_debug_trace(
                    event="receive_message",
                    method=str(message.get("method")) if "method" in message else None,
                    request_id=message.get("id"),
                    detail={
                        "has_result": "result" in message,
                        "has_error": "error" in message,
                    },
                )
                self._dispatch_response(message)
        except Exception as exc:
            self.last_error = str(exc)
            self._record_debug_trace(event="reader_error", detail={"message": str(exc)})
            self._broadcast_closed()

    def _note_progress_started(self, token: str) -> None:
        with self._lock:
            self._active_progress_tokens.add(token)
            self._progress_activity_seen = True
            self._index_ready = False  # a new indexing round re-invalidates readiness

    def _note_progress_ended(self, token: str) -> None:
        with self._lock:
            self._active_progress_tokens.discard(token)
            self._progress_activity_seen = True
            self._progress_end_count += 1

    def _handle_progress_notification(self, message: dict[str, Any]) -> bool:
        """P0-2: consume $/progress begin/report/end (previously dropped as id-less noise)."""
        if message.get("method") != "$/progress":
            return False
        params = message.get("params") or {}
        token = params.get("token")
        kind = (params.get("value") or {}).get("kind")
        if token is None:
            return True
        if kind == "begin":
            self._note_progress_started(str(token))
        elif kind == "end":
            self._note_progress_ended(str(token))
        # "report" -> in-flight; activity noted at begin. Nothing to do.
        return True

    def wait_until_ready(
        self,
        deadline_monotonic: float,
        *,
        probe: Any = None,
        no_progress_grace_seconds: float = 1.0,
        poll_interval_seconds: float = 0.05,
    ) -> bool:
        """Block until the server's workspace index has settled, or the deadline passes.

        Ready means: at least one workDoneProgress round has ENDED and none is active. For
        servers that never advertise progress, ``probe`` (a callable returning the current
        workspace/symbol hit count) is polled until stable across two consecutive polls; with
        no probe, we proceed best-effort after ``no_progress_grace_seconds`` of silence rather
        than burning the whole deadline. Returns False ONLY on a genuine timeout while indexing
        is demonstrably still in flight — and a timeout must NEVER arm
        ``disabled_until_monotonic`` (that cooldown is reserved for real initialize failures;
        arming it here would blackball the language for 30s of daemon uptime after one slow
        first index).
        """
        started_monotonic = time.monotonic()
        previous_probe_value: Any = None
        while True:
            with self._lock:
                if self._index_ready:
                    return True
                active = bool(self._active_progress_tokens)
                ended = self._progress_end_count > 0
                activity = self._progress_activity_seen
            if ended and not active:
                with self._lock:
                    self._index_ready = True
                return True
            now = time.monotonic()
            if now >= deadline_monotonic:
                return False
            if not activity:
                # No progress signal from this server (some don't emit workDoneProgress).
                if probe is not None:
                    try:
                        current_probe_value = probe()
                    except Exception:
                        current_probe_value = None
                    if (
                        current_probe_value is not None
                        and current_probe_value == previous_probe_value
                    ):
                        # Two consecutive stable polls -> index settled.
                        with self._lock:
                            self._index_ready = True
                        return True
                    previous_probe_value = current_probe_value
                elif now - started_monotonic >= max(no_progress_grace_seconds, 0.0):
                    # Silent server, no probe: best-effort after the grace window.
                    with self._lock:
                        self._index_ready = True
                    return True
            time.sleep(max(0.0, min(poll_interval_seconds, deadline_monotonic - now)))

    def _dispatch_response(self, message: dict[str, Any]) -> None:
        """Route a response message to the correct per-id slot (audit B12)."""
        if self._handle_progress_notification(message):
            return
        raw_id = message.get("id")
        if raw_id is None:
            # Notification from server with no id — drop (already handled upstream).
            return
        try:
            request_id = int(raw_id)
        except (TypeError, ValueError):
            return
        with self._lock:
            slot = self._pending_requests.get(request_id)
            if slot is None:
                # The response arrived before request() registered its slot (e.g. a
                # pre-queued response). Buffer it so request() can claim it on
                # registration; bound the buffer so late/duplicate responses for
                # ids that will never be requested cannot leak.
                self._orphan_responses[request_id] = message
                while len(self._orphan_responses) > _MAX_ORPHAN_RESPONSES:
                    self._orphan_responses.pop(next(iter(self._orphan_responses)))
                return
        try:
            slot.put_nowait(message)
        except queue.Full:
            pass  # duplicate response; ignore

    def _broadcast_closed(self) -> None:
        """Signal all pending request slots that the process has closed (audit B12)."""
        with self._lock:
            pending = list(self._pending_requests.values())
        for slot in pending:
            try:
                slot.put_nowait(_CLOSED_SENTINEL)
            except queue.Full:
                pass

    def _stderr_loop(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                text = line.rstrip("\r\n")
                if not text:
                    continue
                self._stderr_tail.append(text)
                if len(self._stderr_tail) > 50:
                    del self._stderr_tail[:-50]
                self._record_debug_trace(event="stderr", detail={"message": text})
        except Exception as exc:
            self._record_debug_trace(event="stderr_error", detail={"message": str(exc)})


class ExternalLSPProviderManager:
    def __init__(self, max_clients: int | None = None) -> None:
        self._max_clients = (
            _configured_positive_int(
                _LSP_PROVIDER_CLIENT_CACHE_MAX_ENTRIES_ENV_VAR,
                _DEFAULT_LSP_PROVIDER_CLIENT_CACHE_MAX_ENTRIES,
            )
            if max_clients is None
            else max(1, int(max_clients))
        )
        self._clients: OrderedDict[tuple[str, str], ExternalLSPClient] = OrderedDict()
        self._clients_lock = threading.Lock()

    def get_client(self, *, language: str, workspace_root: Path) -> ExternalLSPClient:
        key = (language.lower(), str(workspace_root.resolve()))
        with self._clients_lock:
            current = self._clients.pop(key, None)
            if current is not None:
                self._clients[key] = current
                return current

        current = ExternalLSPClient(language=language, workspace_root=workspace_root)
        evicted_clients: list[ExternalLSPClient] = []
        with self._clients_lock:
            cached = self._clients.pop(key, None)
            if cached is not None:
                current = cached
            self._clients[key] = current
            while len(self._clients) > self._max_clients:
                _, evicted = self._clients.popitem(last=False)
                evicted_clients.append(evicted)
        for evicted in evicted_clients:
            evicted.stop()
        return current

    def _cached_client(self, key: tuple[str, str]) -> ExternalLSPClient | None:
        with self._clients_lock:
            current = self._clients.pop(key, None)
            if current is not None:
                self._clients[key] = current
            return current

    def _pop_all_clients(self) -> list[ExternalLSPClient]:
        with self._clients_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        return clients

    def provider_status(
        self,
        *,
        language: str,
        workspace_root: Path,
        verify_health: bool = False,
        probe_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        key = (language.lower(), str(workspace_root.resolve()))
        current = self._cached_client(key)
        if current is not None:
            if verify_health:
                return self._verified_provider_status(
                    client=current,
                    language=language,
                    workspace_root=workspace_root,
                    probe_timeout_seconds=probe_timeout_seconds,
                )
            status = current.status()
            status["available"] = True
            status["health_status"] = _provider_health_status(status)
            status["health_check"] = "cached-client"
            return _attach_lsp_proof_fields(status)
        try:
            command = _provider_command(language)
        except (FileNotFoundError, ValueError) as exc:
            return _attach_lsp_proof_fields({
                "language": language.lower(),
                "workspace_root": str(workspace_root.resolve()),
                "available": False,
                "health_status": "missing",
                "health_check": "not_run",
                "running": False,
                "command": [],
                "command_source": "missing",
                "managed_provider_root": str(_managed_provider_root()),
                "initialized": False,
                "capabilities": {},
                "last_error": str(exc),
                "opened_documents": 0,
                "request_timeout_seconds": _configured_timeout_seconds(
                    _LSP_REQUEST_TIMEOUT_ENV_VAR,
                    _DEFAULT_LSP_REQUEST_TIMEOUT_SECONDS,
                ),
                "initialize_timeout_seconds": _configured_timeout_seconds(
                    _LSP_INITIALIZE_TIMEOUT_ENV_VAR,
                    _DEFAULT_LSP_INITIALIZE_TIMEOUT_SECONDS,
                ),
                "cooldown_remaining_s": 0.0,
            })
        request_timeout_seconds = _configured_timeout_seconds(
            _LSP_REQUEST_TIMEOUT_ENV_VAR,
            _DEFAULT_LSP_REQUEST_TIMEOUT_SECONDS,
        )
        initialize_timeout_seconds = _configured_timeout_seconds(
            _LSP_INITIALIZE_TIMEOUT_ENV_VAR,
            _DEFAULT_LSP_INITIALIZE_TIMEOUT_SECONDS,
        )
        if verify_health:
            probe_request_timeout = (
                max(float(probe_timeout_seconds), 0.0)
                if probe_timeout_seconds is not None
                else request_timeout_seconds
            )
            client = ExternalLSPClient(
                language=language,
                workspace_root=workspace_root,
                request_timeout_seconds=request_timeout_seconds,
                initialize_timeout_seconds=initialize_timeout_seconds,
            )
            return self._verified_provider_status(
                client=client,
                language=language,
                workspace_root=workspace_root,
                probe_timeout_seconds=probe_request_timeout,
                stop_after_probe=True,
            )
        return _attach_lsp_proof_fields({
            "language": language.lower(),
            "workspace_root": str(workspace_root.resolve()),
            "available": True,
            "health_status": "available_unverified",
            "health_check": "not_run",
            "running": False,
            "command": command,
            "command_source": _command_source(command),
            "managed_provider_root": str(_managed_provider_root()),
            "initialized": False,
            "capabilities": {},
            "last_error": None,
            "opened_documents": 0,
            "request_timeout_seconds": request_timeout_seconds,
            "initialize_timeout_seconds": initialize_timeout_seconds,
            "cooldown_remaining_s": 0.0,
        })

    def provider_debug_trace(
        self,
        *,
        language: str,
        workspace_root: Path,
        probe_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        request_timeout_seconds = _configured_timeout_seconds(
            _LSP_REQUEST_TIMEOUT_ENV_VAR,
            _DEFAULT_LSP_REQUEST_TIMEOUT_SECONDS,
        )
        initialize_timeout_seconds = _configured_timeout_seconds(
            _LSP_INITIALIZE_TIMEOUT_ENV_VAR,
            _DEFAULT_LSP_INITIALIZE_TIMEOUT_SECONDS,
        )
        timeout = (
            max(float(probe_timeout_seconds), 0.0)
            if probe_timeout_seconds is not None
            else request_timeout_seconds
        )
        client = ExternalLSPClient(
            language=language,
            workspace_root=workspace_root,
            request_timeout_seconds=request_timeout_seconds,
            initialize_timeout_seconds=initialize_timeout_seconds,
        )
        client.enable_debug_trace()
        status = self._verified_provider_status(
            client=client,
            language=language,
            workspace_root=workspace_root,
            probe_timeout_seconds=timeout,
            stop_after_probe=True,
        )
        return {
            "schema_version": 1,
            "language": canonical_language(language),
            "workspace_root": str(workspace_root.resolve()),
            "probe_timeout_seconds": timeout,
            "initialize_timeout_seconds": initialize_timeout_seconds,
            "request_timeout_seconds": request_timeout_seconds,
            "status": status,
            "trace": client.debug_trace(),
            "stderr_tail": client.stderr_tail(),
        }

    def _verified_provider_status(
        self,
        *,
        client: ExternalLSPClient,
        language: str,
        workspace_root: Path,
        probe_timeout_seconds: float | None,
        stop_after_probe: bool = False,
    ) -> dict[str, Any]:
        timeout = (
            max(float(probe_timeout_seconds), 0.0)
            if probe_timeout_seconds is not None
            else min(client.request_timeout_seconds, client.initialize_timeout_seconds)
        )
        probe = _health_probe_document(language, workspace_root)
        phase = "initialize"
        original_request_timeout = client.request_timeout_seconds
        original_initialize_timeout = client.initialize_timeout_seconds
        probe_succeeded = False
        probe_error: Exception | None = None
        client.request_timeout_seconds = timeout
        client.initialize_timeout_seconds = timeout
        try:
            try:
                client.start()
                phase = "did_open"
                client.ensure_document(
                    uri=probe["uri"],
                    text=probe["text"],
                    language_id=probe["language_id"],
                )
                phase = "document_symbol"
                result = client.request(
                    "textDocument/documentSymbol",
                    {"textDocument": {"uri": probe["uri"]}},
                )
                if not _document_symbol_result_contains(result, probe["symbol"]):
                    raise LSPTransportError(
                        "semantic documentSymbol probe returned no matching symbol"
                    )
                probe_succeeded = True
                client.lsp_provider_response = True
            except (FileNotFoundError, LSPTransportError, OSError, ValueError) as exc:
                probe_error = exc
                client.lsp_provider_response = False
            finally:
                client.request_timeout_seconds = original_request_timeout
                client.initialize_timeout_seconds = original_initialize_timeout
            status = client.status()
            status["available"] = True
            status["health_check"] = "semantic-document-symbol"
            status["health_phase"] = phase
            status["probe_timeout_seconds"] = timeout
            status["probe_document_uri"] = probe["uri"]
            status["probe_symbol"] = probe["symbol"]
            if probe_succeeded:
                status["health_status"] = "ready"
            else:
                status["health_status"] = "unhealthy"
                status["lsp_provider_response"] = False
                if probe_error is not None:
                    status["last_error"] = status.get("last_error") or str(probe_error)
            return _attach_lsp_proof_fields(status)
        finally:
            if stop_after_probe:
                client.stop()

    def stop_all(self) -> None:
        clients = self._pop_all_clients()
        for client in clients:
            client.stop()

    def close_all(self) -> None:
        clients = self._pop_all_clients()
        for current in clients:
            current.stop()


def _command_source(command: list[str]) -> str:
    if not command:
        return "missing"
    try:
        command_path = Path(command[0]).resolve()
        command_path.relative_to(_managed_provider_root())
    except ValueError:
        return "path"
    except OSError:
        return "path"
    return "managed"


def _provider_health_status(status: dict[str, Any]) -> str:
    if not status.get("available"):
        return "missing"
    if status.get("last_error"):
        return "unhealthy"
    if status.get("running") and (status.get("initialized") or status.get("capabilities")):
        return "ready"
    if status.get("running"):
        return "running_unverified"
    return "available_unverified"


def _attach_lsp_proof_fields(status: dict[str, Any]) -> dict[str, Any]:
    health_status = str(status.get("health_status", _provider_health_status(status)))
    health_check = str(status.get("health_check", "not_run"))
    status.setdefault("lsp_provider_response", False)
    lsp_proof = (
        bool(status.get("available"))
        and health_status == "ready"
        and status.get("lsp_provider_response") is True
    )
    status["health_status"] = health_status
    status["health_check"] = health_check
    status["lsp_proof"] = lsp_proof
    if lsp_proof:
        status.pop("not_lsp_proof_reason", None)
        stderr_tail = [str(item) for item in status.get("stderr_tail", []) if str(item)]
        provider_warnings = [
            item
            for item in stderr_tail
            if "sre module mismatch" in item.lower()
            or "_sre" in item.lower()
            or "abi mismatch" in item.lower()
        ]
        other_stderr = [item for item in stderr_tail if item not in provider_warnings]
        if provider_warnings:
            status["provider_warnings"] = provider_warnings[-3:]
            status["provider_warning_status"] = "non_current_diagnostic"
            status["provider_warning_remediation"] = (
                "Managed provider proof succeeded, but provider stderr previously reported a "
                "Python runtime or stdlib mismatch. Re-run `tg lsp-setup` after clearing "
                "inherited PYTHONHOME/PYTHONPATH or inspect `tg doctor --with-lsp --json`."
            )
            status["stderr_tail"] = []
            status["stderr_tail_suppressed"] = True
            if other_stderr:
                status["provider_recent_stderr"] = other_stderr[-3:]
        elif stderr_tail:
            status["provider_recent_stderr"] = stderr_tail[-3:]
            status["stderr_tail"] = []
            status["stderr_tail_suppressed"] = True
        return status
    if not status.get("available"):
        reason = "Provider binary is unavailable."
    elif health_status == "available_unverified" and health_check == "not_run":
        reason = "Provider binary is available but health was not verified."
    elif health_status == "unhealthy":
        reason = "Provider semantic health probe failed or timed out."
    elif health_status == "ready" and status.get("lsp_provider_response") is not True:
        reason = "Provider initialized, but semantic health has not been verified in this session."
    else:
        reason = "Provider has not completed a successful initialization probe."
    status["not_lsp_proof_reason"] = reason
    return status
