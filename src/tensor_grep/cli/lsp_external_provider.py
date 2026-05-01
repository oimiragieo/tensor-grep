from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, cast

from tensor_grep.cli.lsp_provider_setup import (
    canonical_language,
    managed_provider_env,
    resolved_provider_command,
)
from tensor_grep.cli.lsp_provider_setup import (
    managed_provider_root as _managed_provider_root,
)


class LSPTransportError(RuntimeError):
    pass


_DEFAULT_LSP_REQUEST_TIMEOUT_SECONDS = 3.0
_DEFAULT_LSP_INITIALIZE_TIMEOUT_SECONDS = 15.0
_LSP_REQUEST_TIMEOUT_ENV_VAR = "TENSOR_GREP_LSP_REQUEST_TIMEOUT_SECONDS"
_LSP_INITIALIZE_TIMEOUT_ENV_VAR = "TENSOR_GREP_LSP_INITIALIZE_TIMEOUT_SECONDS"


def _configured_timeout_seconds(env_var: str, default: float) -> float:
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        return default
    try:
        return max(float(raw_value), 0.0)
    except (TypeError, ValueError):
        return default


def _read_message(stream: Any) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if line == "":
            return None
        if line in ("\r\n", "\n"):
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        return None
    body = stream.read(content_length)
    if not body:
        return None
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        return None
    return cast(dict[str, Any], parsed)


def _write_message(stream: Any, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":"))
    encoded = body.encode("utf-8")
    stream.write(f"Content-Length: {len(encoded)}\r\n\r\n")
    stream.write(body)
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


class ExternalLSPClient:
    def __init__(
        self,
        *,
        language: str,
        workspace_root: Path,
        request_timeout_seconds: float | None = None,
        initialize_timeout_seconds: float | None = None,
        retry_cooldown_seconds: float = 30.0,
    ) -> None:
        self.language = language
        self.workspace_root = workspace_root.resolve()
        self.command = _provider_command(language)
        self.process: subprocess.Popen[str] | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._opened_documents: set[str] = set()
        self._message_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
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

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        if self.disabled_until_monotonic > time.monotonic():
            raise LSPTransportError(self.last_error or "LSP provider temporarily unavailable")
        if self.process is not None:
            self.stop()
        self.process = subprocess.Popen(
            self.command,
            cwd=str(self.workspace_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=managed_provider_env(self.command, managed_root=_managed_provider_root()),
        )
        self._message_queue = queue.Queue()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        try:
            result = self.request(
                "initialize",
                {
                    "processId": None,
                    "rootUri": self.workspace_root.as_uri(),
                    "capabilities": {},
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

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        reader_thread = self._reader_thread
        with self._lock:
            try:
                self._write_notification("shutdown", {})
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
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except Exception:
                pass
            try:
                process.wait(timeout=2.0)
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
            reader_thread.join(timeout=2.0)
        with self._lock:
            if self.process is process:
                self.process = None
            self._opened_documents.clear()
            self.capabilities = {}
            if self._reader_thread is reader_thread:
                self._reader_thread = None
            self._message_queue = queue.Queue()

    def request(self, method: str, params: dict[str, Any]) -> Any:
        self.start()
        if self.process is None or self.process.stdin is None or self.process.stdout is None:
            raise LSPTransportError("LSP process is not available")
        timeout_seconds = (
            self.initialize_timeout_seconds
            if method == "initialize"
            else self.request_timeout_seconds
        )
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            self._write_request(request_id, method, params)
            while True:
                try:
                    message = self._message_queue.get(timeout=timeout_seconds)
                except queue.Empty as exc:
                    self.last_error = f"timeout waiting for LSP response: {method}"
                    raise LSPTransportError(self.last_error) from exc
                if message is None:
                    self.last_error = f"LSP process closed during request: {method}"
                    raise LSPTransportError(f"LSP process closed during request: {method}")
                if "id" not in message:
                    continue
                if int(message["id"]) != request_id:
                    continue
                if "error" in message:
                    self.last_error = str(message["error"])
                    raise LSPTransportError(self.last_error)
                self.last_error = None
                return message.get("result")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.start()
        if self.process is None or self.process.stdin is None:
            raise LSPTransportError("LSP process is not available")
        with self._lock:
            self._write_notification(method, params)

    def ensure_document(self, *, uri: str, text: str, language_id: str) -> None:
        if uri in self._opened_documents:
            return
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
        self._opened_documents.add(uri)

    def did_change(self, *, uri: str, text: str, version: int = 1) -> None:
        if uri not in self._opened_documents:
            return
        self.notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
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
            "capabilities": dict(self.capabilities),
            "last_error": self.last_error,
            "opened_documents": len(self._opened_documents),
            "request_timeout_seconds": self.request_timeout_seconds,
            "initialize_timeout_seconds": self.initialize_timeout_seconds,
            "cooldown_remaining_s": max(0.0, self.disabled_until_monotonic - time.monotonic()),
        }

    def _write_request(self, request_id: int, method: str, params: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise LSPTransportError("LSP process is not available")
        _write_message(
            self.process.stdin,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        )

    def _write_notification(self, method: str, params: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise LSPTransportError("LSP process is not available")
        _write_message(
            self.process.stdin,
            {"jsonrpc": "2.0", "method": method, "params": params},
        )

    def _reader_loop(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            self._message_queue.put(None)
            return
        try:
            while True:
                message = _read_message(process.stdout)
                if message is None:
                    self._message_queue.put(None)
                    return
                self._message_queue.put(message)
        except Exception as exc:
            self.last_error = str(exc)
            self._message_queue.put(None)


class ExternalLSPProviderManager:
    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], ExternalLSPClient] = {}

    def get_client(self, *, language: str, workspace_root: Path) -> ExternalLSPClient:
        key = (language.lower(), str(workspace_root.resolve()))
        current = self._clients.get(key)
        if current is None:
            current = ExternalLSPClient(language=language, workspace_root=workspace_root)
            self._clients[key] = current
        return current

    def provider_status(self, *, language: str, workspace_root: Path) -> dict[str, Any]:
        key = (language.lower(), str(workspace_root.resolve()))
        current = self._clients.get(key)
        if current is not None:
            status = current.status()
            status["available"] = True
            return status
        try:
            command = _provider_command(language)
        except (FileNotFoundError, ValueError) as exc:
            return {
                "language": language.lower(),
                "workspace_root": str(workspace_root.resolve()),
                "available": False,
                "running": False,
                "command": [],
                "command_source": "missing",
                "managed_provider_root": str(_managed_provider_root()),
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
            }
        return {
            "language": language.lower(),
            "workspace_root": str(workspace_root.resolve()),
            "available": True,
            "running": False,
            "command": command,
            "command_source": _command_source(command),
            "managed_provider_root": str(_managed_provider_root()),
            "capabilities": {},
            "last_error": None,
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
        }

    def stop_all(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            client.stop()

    def close_all(self) -> None:
        for current in self._clients.values():
            current.stop()
        self._clients.clear()


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
