from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import OrderedDict
from functools import partial
from pathlib import Path
from typing import Any, cast

from tensor_grep.cli.lsp_probe_budget import ProbeBudget, client_lock, remaining_seconds
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
from tensor_grep.cli.lsp_readiness import ReadinessMixin
from tensor_grep.cli.lsp_session import ProviderSession, SessionBackedState, reset_after_stop
from tensor_grep.cli.lsp_transport_writer import bounded_stdin
from tensor_grep.cli.process_containment import (
    ContainmentUnavailableError,
    cleanup_budget_seconds,
    spawn_contained,
    teardown_provider,
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

# Audit #116 (DoS): bound the header preamble itself -- mirrors mcp_server.py's audit #49 fix
# for the identical unbounded-header-skip shape in the MCP stdio reader. A malformed or
# malicious language-server subprocess that streams endless header lines, or a single giant
# line with no newline terminator, or one that never sends the blank-line terminator, must not
# hang the reader or exhaust memory BEFORE the Content-Length size check below ever runs.
# Three caps apply, fail-closed on any breach: a hard iteration cap on the NUMBER of header
# lines (_MAX_LSP_HEADER_LINES), a hard byte cap on any SINGLE line (enforced by the bounded
# readline() below), and a cumulative byte cap across the whole preamble -- the two byte caps
# reuse one constant (_MAX_LSP_HEADER_BYTES), mirroring #49's reuse of a single constant for
# both roles.
_MAX_LSP_HEADER_LINES = 128
_MAX_LSP_HEADER_BYTES = 64 * 1024


def _read_bounded_line(stream: Any, limit: int) -> Any:
    """``stream.readline()``, but never buffer more than ``limit`` bytes/chars for one line.

    Named to mirror mcp_server.py's ``_read_bounded_line`` (audit #49). That MCP reader wraps
    an ``anyio.AsyncFile``, whose ``readline()`` does not forward a size bound -- it needs a
    reach-into-``.wrapped`` thread trick to get one. ``_read_message`` here is a plain
    synchronous function reading a subprocess pipe (or a test double), and both
    ``io.BufferedReader.readline(size)`` and text-stream ``readline(size)`` already accept a
    size bound directly, so this is a direct pass-through -- kept as a named helper purely to
    mirror the #49 shape for anyone diffing the two readers.
    """
    return stream.readline(limit)


def _read_message(stream: Any) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    header_bytes_total = 0
    for _ in range(_MAX_LSP_HEADER_LINES):
        line = _read_bounded_line(stream, _MAX_LSP_HEADER_BYTES)
        if line in ("", b""):
            return None
        ends_with_newline = line.endswith(b"\n") if isinstance(line, bytes) else line.endswith("\n")
        if len(line) >= _MAX_LSP_HEADER_BYTES and not ends_with_newline:
            # Fail closed: this single line reached the per-line read cap with no newline
            # terminator -- a giant or never-terminating header line. A complete line exactly
            # at the cap still ends in "\n", so a legitimate message is never truncated.
            return None
        header_bytes_total += len(line)
        if header_bytes_total > _MAX_LSP_HEADER_BYTES:
            # Fail closed: too many header lines summed past the cumulative byte budget
            # before a blank-line terminator ever showed up.
            return None
        if line in ("\r\n", "\n", b"\r\n", b"\n"):
            break
        if isinstance(line, bytes):
            line = line.decode("ascii", errors="replace")
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    else:
        # Fail closed: too many header lines without a blank-line terminator.
        return None
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
        try:
            stream.write(framed)
        except TypeError:
            stream.write(framed.decode("utf-8"))
        stream.flush()
    except OSError as exc:  # broken pipe / EINVAL: the provider is gone or was killed
        raise LSPTransportError(f"LSP write failed: {exc}") from exc


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


class ExternalLSPClient(ReadinessMixin, SessionBackedState):
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
        self._session = ProviderSession()
        self._request_id = 0
        self._lock = threading.Lock()
        # Serializes start()'s check-then-spawn so concurrent daemon worker threads sharing this
        # cached client cannot both Popen (round-6 r9). SEPARATE from _lock: start()'s initialize
        # handshake calls request() which takes _lock, so reusing it would re-entrant-deadlock.
        self._start_lock = threading.RLock()
        self._max_open_documents = (
            _configured_positive_int(
                _LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES_ENV_VAR,
                _DEFAULT_LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES,
            )
            if max_open_documents is None
            else max(1, int(max_open_documents))
        )
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
        # When set (doctor probe under a deadline), stop() uses this slice instead of the
        # default bound -- including the stop() that start() runs after a failed initialize.
        self.stop_grace_seconds: float | None = None
        self._starting = False
        self.deadline_monotonic: float | None = None  # absolute probe deadline (see ProbeBudget)
        self.unusable = False  # set when teardown could not take the client lock in time
        self.teardown_error: str | None = None
        self.containment_error: str | None = None  # set when the tree could not be contained
        self.last_error: str | None = None
        self.disabled_until_monotonic = 0.0
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
        return list(self._session.stderr_tail)

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
        if self.unusable:
            raise LSPTransportError(
                self.last_error or "LSP client is unusable after failed teardown"
            )
        process = self._session.process  # fast path (no lock): already running
        if process is not None and process.poll() is None:
            return
        # Serialize the check-then-spawn (round-6 r9): two daemon worker threads calling into the
        # SAME cached client (get_client is shared per (root,language)) must not both pass the None
        # check and both Popen, orphaning one child. Double-checked under _start_lock.
        with client_lock(self, lock=self._start_lock):
            if self._starting:  # re-entered by our own initialize handshake: never respawn
                return
            process = self._session.process
            if process is not None and process.poll() is None:
                return
            self._starting = True
            try:
                self._start_locked()
            finally:
                self._starting = False

    def _start_locked(self) -> None:
        if self.disabled_until_monotonic > time.monotonic():
            raise LSPTransportError(self.last_error or "LSP provider temporarily unavailable")
        if self._session.process is not None:
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
        # cwd is workspace_root (safe: argv has no CWD-searchable names); contained spawn.
        session = ProviderSession(self._session.generation + 1)
        try:
            session.process, session.containment = spawn_contained(
                spawn_argv,
                cwd=str(self.workspace_root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=managed_provider_env(self.command, managed_root=managed_root),
            )
        except ContainmentUnavailableError as exc:  # fail closed: the provider was NOT launched
            self.containment_error = str(exc)
            self.last_error = f"containment_unavailable: {exc}"
            raise LSPTransportError(self.last_error) from exc
        try:
            with client_lock(self):
                self._session = session  # NEW state: stale teardowns/readers cannot reach it
        except BaseException:
            # `session` owns the spawned provider from the moment of spawn. If it cannot be
            # published (the lock wait hit the probe deadline) it must not leak: kill the tree,
            # release containment and pipes inside the cleanup budget, then re-raise.
            teardown_provider(
                session.process,
                session.containment,
                deadline=time.monotonic()
                + cleanup_budget_seconds(
                    self.request_timeout_seconds,
                    _DEFAULT_LSP_STOP_TIMEOUT_SECONDS,
                    self.stop_grace_seconds,
                ),
            )
            raise
        self._record_debug_trace(
            event="process_start",
            detail={"command": spawn_argv, "cwd": str(self.workspace_root)},
        )
        session.reader_thread = threading.Thread(
            target=self._reader_loop, args=(session,), daemon=True
        )
        session.reader_thread.start()
        session.stderr_thread = threading.Thread(
            target=self._stderr_loop, args=(session,), daemon=True
        )
        session.stderr_thread.start()
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
            session.capabilities = dict(result.get("capabilities", {}))
        self.notify("initialized", {})
        session.initialized = True
        try:
            self.notify(
                "workspace/didChangeConfiguration",
                _configuration_settings(self.language),
            )
        except Exception:
            pass

    def stop(self, grace_seconds: float | None = None) -> None:
        session = self._session  # capture: everything below touches ONLY this session
        process = session.process
        if process is None:
            return
        reader_thread = session.reader_thread
        stderr_thread = session.stderr_thread
        grace = grace_seconds if grace_seconds is not None else self.stop_grace_seconds
        budget = cleanup_budget_seconds(
            self.request_timeout_seconds, _DEFAULT_LSP_STOP_TIMEOUT_SECONDS, grace
        )
        deadline = time.monotonic() + budget
        errors = teardown_provider(
            process,
            session.containment,
            deadline=deadline,
            graceful=partial(self._graceful_shutdown_for_stop, session),
        )
        self.teardown_error = None
        if errors:  # the handle is dropped below; keep the pid and the root cause visible
            self.teardown_error = (
                f"LSP child pid {getattr(process, 'pid', '?')} not stopped ({'; '.join(errors)})"
            )
            self.last_error = self.teardown_error
        stop_timeout_seconds = max(deadline - time.monotonic(), 0.05)
        if reader_thread is not None and reader_thread.is_alive():
            reader_thread.join(timeout=stop_timeout_seconds)
        if stderr_thread is not None and stderr_thread.is_alive():
            stderr_thread.join(timeout=stop_timeout_seconds)
        if not self._lock.acquire(timeout=max(deadline - time.monotonic(), 0.0)):
            self.unusable = True
            self.teardown_error = self.last_error = "teardown lock unavailable: client unusable"
            return
        try:
            if self._session is session:  # the ONLY shared write; a replacement is never touched
                self._session = ProviderSession(session.generation)
        finally:
            self._lock.release()
        reset_after_stop(session, _CLOSED_SENTINEL)

    def _graceful_shutdown_for_stop(self, session: ProviderSession, timeout: float) -> None:
        self._request_shutdown_for_stop(session, timeout)
        try:
            with client_lock(self, timeout):
                if self._session is not session:
                    return  # abandoned: a replacement provider owns this client now
                try:
                    self._write_notification("exit", None)
                except Exception:
                    pass
        except TimeoutError:
            return

    def _request_shutdown_for_stop(self, session: ProviderSession, timeout: float) -> None:
        # audit B12: per-id slot so the shutdown cannot race concurrent request() calls.
        if session.process is None or session.process.stdin is None:
            return
        slot: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        try:
            with client_lock(self, timeout):
                if self._session is not session:
                    return
                self._request_id += 1
                request_id = self._request_id
                session.pending_requests[request_id] = slot
                self._write_request(request_id, "shutdown", None)
                buffered = session.orphan_responses.pop(request_id, None)
            if buffered is not None:
                try:
                    slot.put_nowait(buffered)
                except queue.Full:
                    pass
        except Exception:
            return
        try:
            slot.get(timeout=timeout)
        except queue.Empty:
            pass
        finally:
            session.pending_requests.pop(request_id, None)

    def request(self, method: str, params: dict[str, Any]) -> Any:
        # audit B12: each in-flight request gets its own one-shot Queue so that
        # concurrent calls cannot steal each other's responses.
        self.start()
        session = self._session  # captured: all state below is THIS session's
        process = session.process
        if process is None or process.stdin is None or process.stdout is None:
            raise LSPTransportError("LSP process is not available")
        timeout_seconds = (
            self.initialize_timeout_seconds
            if method == "initialize"
            else self.request_timeout_seconds
        )
        slot: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        pending = session.pending_requests  # this request's session; finalised against it
        with client_lock(self):
            self._request_id += 1
            request_id = self._request_id
            pending[request_id] = slot
            self._write_request(request_id, method, params, session=session)
            # Claim a response that the reader dispatched before this slot existed.
            buffered = session.orphan_responses.pop(request_id, None)
        if buffered is not None:
            try:
                slot.put_nowait(buffered)
            except queue.Full:
                pass
        try:
            timeout_seconds = remaining_seconds(self, timeout_seconds)  # after lock + write
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
            with client_lock(self):
                pending.pop(request_id, None)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.start()
        session = self._session
        if session.process is None or session.process.stdin is None:
            raise LSPTransportError("LSP process is not available")
        with client_lock(self):
            self._write_notification(method, params, session=session)

    def ensure_document(self, *, uri: str, text: str, language_id: str) -> None:
        session = self._session
        if uri in session.opened_documents:
            session.opened_documents.move_to_end(uri)
            return
        evicted_uri = (
            next(iter(session.opened_documents))
            if len(session.opened_documents) >= self._max_open_documents
            else None
        )
        # audit B15: record the initial version so did_change can monotonically
        # increment from it.
        with client_lock(self):
            session.doc_versions[uri] = 1
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
        session.opened_documents[uri] = None
        if evicted_uri is not None:
            session.opened_documents.pop(evicted_uri, None)
            self._notify_document_closed(evicted_uri, session)

    def _notify_document_closed(self, uri: str, session: ProviderSession | None = None) -> None:
        session = session or self._session
        # Audit LOW (leak): evict the per-URI version counter on close, mirroring the
        # _opened_documents cleanup. Both removal paths (open-eviction and close_document)
        # funnel through here, so _doc_versions no longer grows unbounded across a
        # long-lived client's lifetime. _doc_versions is _lock-guarded (see did_change).
        with client_lock(self):
            session.doc_versions.pop(uri, None)
        try:
            self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        except Exception:
            self._record_debug_trace(
                event="document_close_failed",
                method="textDocument/didClose",
                detail={"uri": uri},
            )

    def close_document(self, *, uri: str) -> None:
        session = self._session
        if uri not in session.opened_documents:
            return
        session.opened_documents.pop(uri, None)
        self._notify_document_closed(uri, session)

    def did_change(self, *, uri: str, text: str, version: int = 1) -> None:
        session = self._session
        # audit B15: ignore the caller-supplied version (which can be non-monotonic
        # when multiple editors send version=1) and use an internal counter instead.
        if uri not in session.opened_documents:
            return
        with client_lock(self):
            current_version = session.doc_versions.get(uri, 1)
            next_version = max(current_version + 1, version + 1)
            session.doc_versions[uri] = next_version
        self.notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": next_version},
                "contentChanges": [{"text": text}],
            },
        )

    def did_save(self, *, uri: str) -> None:
        session = self._session
        if uri not in session.opened_documents:
            return
        self.notify("textDocument/didSave", {"textDocument": {"uri": uri}})

    def status(self) -> dict[str, Any]:
        session = self._session
        return {
            "language": self.language,
            "workspace_root": str(self.workspace_root),
            "command": list(self.command),
            "command_source": _command_source(self.command),
            "managed_provider_root": str(_managed_provider_root()),
            "running": session.process is not None and session.process.poll() is None,
            "process_containment": session.containment.level if session.containment else None,
            "initialized": session.initialized,
            "capabilities": dict(session.capabilities),
            "lsp_provider_response": session.lsp_provider_response,
            "last_error": self.last_error,
            "opened_documents": len(session.opened_documents),
            "max_open_documents": self._max_open_documents,
            "stderr_tail": self.stderr_tail(),
            "request_timeout_seconds": self.request_timeout_seconds,
            "initialize_timeout_seconds": self.initialize_timeout_seconds,
            "cooldown_remaining_s": max(0.0, self.disabled_until_monotonic - time.monotonic()),
        }

    def _writable(self, session: ProviderSession | None) -> ProviderSession:
        """The session a transport write targets; the caller holds the client lock. A write for a
        session that is no longer current is refused, so nothing reaches a replacement provider."""
        session = session or self._session
        if session.process is None or session.process.stdin is None:
            raise LSPTransportError("LSP process is not available")
        if self._session is not session:
            raise LSPTransportError("LSP session was replaced")
        return session

    def _write_request(
        self,
        request_id: int,
        method: str,
        params: dict[str, Any] | None,
        session: ProviderSession | None = None,
    ) -> None:
        session = self._writable(session)
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._record_debug_trace(
            event="send_request",
            method=method,
            request_id=request_id,
            detail={"params_keys": sorted(params.keys()) if isinstance(params, dict) else []},
        )
        _write_message(bounded_stdin(self, session), payload)

    def _write_notification(
        self, method: str, params: dict[str, Any] | None, session: ProviderSession | None = None
    ) -> None:
        session = self._writable(session)
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._record_debug_trace(
            event="send_notification",
            method=method,
            detail={"params_keys": sorted(params.keys()) if isinstance(params, dict) else []},
        )
        _write_message(bounded_stdin(self, session), payload)

    def _write_response(
        self, request_id: object, result: Any, session: ProviderSession | None = None
    ) -> None:
        session = self._writable(session)
        payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
        self._record_debug_trace(
            event="send_response",
            request_id=request_id,
            detail={"result_type": type(result).__name__},
        )
        _write_message(bounded_stdin(self, session), payload)

    def _write_error_response(
        self,
        request_id: object,
        *,
        code: int,
        message: str,
        session: ProviderSession | None = None,
    ) -> None:
        session = self._writable(session)
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
        _write_message(bounded_stdin(self, session), payload)

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

    def _handle_server_request(
        self, message: dict[str, Any], session: ProviderSession | None = None
    ) -> bool:
        if "id" not in message or "method" not in message:
            return False
        session = session or self._session
        if self._session is not session:
            return True  # stale reader: its provider was replaced; answer nothing
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
                with client_lock(self, follow_deadline=False):
                    self._write_error_response(
                        request_id,
                        code=-32601,
                        message=f"Unsupported LSP server request: {method}",
                        session=session,
                    )
                return True
            with client_lock(self, follow_deadline=False):
                self._write_response(request_id, result, session=session)
            return True
        except Exception as exc:
            try:
                with client_lock(self, follow_deadline=False):
                    self._write_error_response(
                        request_id, code=-32603, message=str(exc), session=session
                    )
            except Exception:
                pass
            return True

    def _reader_loop(self, session: ProviderSession | None = None) -> None:
        # audit B12: route each response to the per-id slot registered by request().
        session = session or self._session
        process = session.process
        if process is None or process.stdout is None:
            self._broadcast_closed(session)
            return
        try:
            while True:
                message = _read_message(process.stdout)
                if message is None:
                    self._record_debug_trace(event="process_stdout_closed")
                    self._broadcast_closed(session)
                    self._terminate_after_reader_exit(process)
                    return
                if self._session is not session:
                    return  # stale reader: its process was replaced
                if self._handle_server_request(message, session):
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
                self._dispatch_response(message, session)
        except Exception as exc:
            self.last_error = str(exc)
            self._record_debug_trace(event="reader_error", detail={"message": str(exc)})
            self._broadcast_closed(session)
            self._terminate_after_reader_exit(process)

    def _terminate_after_reader_exit(self, process: Any) -> None:
        # Nothing drains the child's stdout or routes its replies any more, so it is unusable. A
        # live child here made start() (poll() is None) reuse it: status() said running, and every
        # request waited out its full timeout. Terminating makes poll() truthful so the existing
        # restart path respawns; it also stops an undrained pipe from blocking the child.
        terminate = getattr(process, "terminate", None)
        if callable(terminate):
            try:
                terminate()
            except OSError:
                pass  # already gone (or not ours to signal); stop() owns the escalation

    def _dispatch_response(
        self, message: dict[str, Any], session: ProviderSession | None = None
    ) -> None:
        """Route a response message to the correct per-id slot (audit B12)."""
        session = session or self._session
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
        with client_lock(self, follow_deadline=False):
            if self._session is not session:
                return  # stale reader: never touch a replacement's slots or orphan buffer
            slot = session.pending_requests.get(request_id)
            if slot is None:
                # The response arrived before request() registered its slot (e.g. a
                # pre-queued response). Buffer it so request() can claim it on
                # registration; bound the buffer so late/duplicate responses for
                # ids that will never be requested cannot leak.
                session.orphan_responses[request_id] = message
                while len(session.orphan_responses) > _MAX_ORPHAN_RESPONSES:
                    session.orphan_responses.pop(next(iter(session.orphan_responses)))
                return
        try:
            slot.put_nowait(message)
        except queue.Full:
            pass  # duplicate response; ignore

    def _broadcast_closed(self, session: ProviderSession | None = None) -> None:
        """Signal all pending request slots that the process has closed (audit B12)."""
        session = session or self._session
        with client_lock(self, follow_deadline=False):
            pending = list(session.pending_requests.values())
        for slot in pending:
            try:
                slot.put_nowait(_CLOSED_SENTINEL)
            except queue.Full:
                pass

    def _stderr_loop(self, session: ProviderSession | None = None) -> None:
        session = session or self._session
        process = session.process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                text = line.rstrip("\r\n")
                if not text:
                    continue
                session.stderr_tail.append(text)
                if len(session.stderr_tail) > 50:
                    del session.stderr_tail[:-50]
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
        deadline_monotonic: float | None = None,
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
                    deadline_monotonic=deadline_monotonic,
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
                deadline_monotonic=deadline_monotonic,
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
        deadline_monotonic: float | None = None,
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
        stopped = False
        budget = ProbeBudget(deadline_monotonic, timeout, _DEFAULT_LSP_STOP_TIMEOUT_SECONDS)
        client.stop_grace_seconds = budget.cleanup_seconds
        budget.start_watchdog(client)
        try:
            try:
                budget.arm(client)
                client.start()
                phase = "did_open"
                client.ensure_document(
                    uri=probe["uri"],
                    text=probe["text"],
                    language_id=probe["language_id"],
                )
                phase = "document_symbol"
                budget.arm(client)
                result = client.request(
                    "textDocument/documentSymbol",
                    {"textDocument": {"uri": probe["uri"]}},
                )
                if not _document_symbol_result_contains(result, probe["symbol"]):
                    raise LSPTransportError(
                        "semantic documentSymbol probe returned no matching symbol"
                    )
                if not budget.settle():
                    raise TimeoutError("doctor LSP probe deadline exceeded")
                probe_succeeded = True
                client.lsp_provider_response = True
            except (FileNotFoundError, LSPTransportError, OSError, ValueError) as exc:
                probe_error = budget.explain(exc)
                client.lsp_provider_response = False
            finally:
                budget.cancel()
                client.request_timeout_seconds = original_request_timeout
                client.initialize_timeout_seconds = original_initialize_timeout
            status = client.status()  # snapshot BEFORE teardown clears the client's state
            status["available"] = True
            status["health_check"] = "semantic-document-symbol"
            status["health_phase"] = phase
            status["probe_timeout_seconds"] = timeout
            status["probe_document_uri"] = probe["uri"]
            status["probe_symbol"] = probe["symbol"]
            if stop_after_probe:  # finish teardown FIRST so its failures reach the report
                client.stop()
                stopped = True
            teardown_error = client.teardown_error if stop_after_probe else None
            if probe_succeeded and not teardown_error:
                status["health_status"] = "ready"
            else:
                unhealthy = "containment_unavailable" if client.containment_error else "unhealthy"
                status["health_status"] = unhealthy
                status["lsp_provider_response"] = False
                if teardown_error:
                    status["cleanup_error"] = teardown_error
                reasons = [budget.reason(status.get("last_error"), probe_error), teardown_error]
                status["last_error"] = "; ".join(str(r) for r in reasons if r) or None
            return _attach_lsp_proof_fields(status)
        finally:
            if stop_after_probe and not stopped:
                client.stop()  # honours client.stop_grace_seconds (set above under a deadline)
            client.stop_grace_seconds = None

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
