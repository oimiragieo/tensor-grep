from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, cast


class LSPTransportError(RuntimeError):
    pass


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
    normalized = language.lower()
    if normalized == "python":
        binary = shutil.which("pyright-langserver")
        if not binary:
            raise FileNotFoundError("pyright-langserver binary not found on PATH")
        return [binary, "--stdio"]
    if normalized in {"javascript", "typescript"}:
        binary = shutil.which("typescript-language-server")
        if not binary:
            raise FileNotFoundError("typescript-language-server binary not found on PATH")
        return [binary, "--stdio"]
    if normalized == "rust":
        binary = shutil.which("rust-analyzer")
        if not binary:
            cargo_bin = Path.home() / ".cargo" / "bin" / "rust-analyzer.exe"
            if cargo_bin.exists():
                return [str(cargo_bin)]
            raise FileNotFoundError("rust-analyzer binary not found on PATH")
        return [binary]
    raise ValueError(f"Unsupported LSP language: {language}")


class ExternalLSPClient:
    def __init__(self, *, language: str, workspace_root: Path) -> None:
        self.language = language
        self.workspace_root = workspace_root.resolve()
        self.command = _provider_command(language)
        self.process: subprocess.Popen[str] | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._opened_documents: set[str] = set()

    def start(self) -> None:
        if self.process is not None:
            return
        self.process = subprocess.Popen(
            self.command,
            cwd=str(self.workspace_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.request(
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
        self.notify("initialized", {})

    def stop(self) -> None:
        if self.process is None:
            return
        with self._lock:
            try:
                self.notify("shutdown", {})
            except Exception:
                pass
            try:
                self.process.terminate()
            except Exception:
                pass
            self.process = None
            self._opened_documents.clear()

    def request(self, method: str, params: dict[str, Any]) -> Any:
        self.start()
        if self.process is None or self.process.stdin is None or self.process.stdout is None:
            raise LSPTransportError("LSP process is not available")
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            _write_message(
                self.process.stdin,
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            )
            while True:
                message = _read_message(self.process.stdout)
                if message is None:
                    raise LSPTransportError(f"LSP process closed during request: {method}")
                if "id" not in message:
                    continue
                if int(message["id"]) != request_id:
                    continue
                if "error" in message:
                    raise LSPTransportError(str(message["error"]))
                return message.get("result")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.start()
        if self.process is None or self.process.stdin is None:
            raise LSPTransportError("LSP process is not available")
        with self._lock:
            _write_message(
                self.process.stdin,
                {"jsonrpc": "2.0", "method": method, "params": params},
            )

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

    def close_all(self) -> None:
        for current in self._clients.values():
            current.stop()
        self._clients.clear()
