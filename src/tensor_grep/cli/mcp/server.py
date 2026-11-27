"""MCPServer instance, registration decorators, and stdio transport."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from io import TextIOWrapper
from typing import TYPE_CHECKING, Any, cast

import anyio
import pydantic
from mcp import types
from mcp.server.mcpserver import MCPServer
from mcp.shared.message import SessionMessage

from tensor_grep.cli import mcp_tool_contract as _tool_contract

AuditAction = _tool_contract.AuditAction
CheckpointAction = _tool_contract.CheckpointAction
ContextAction = _tool_contract.ContextAction
ExploreAction = _tool_contract.ExploreAction
ImpactAction = _tool_contract.ImpactAction
NavigateAction = _tool_contract.NavigateAction
QueryAction = _tool_contract.QueryAction
RewriteAction = _tool_contract.RewriteAction
ScanAction = _tool_contract.ScanAction
SessionAction = _tool_contract.SessionAction
_TG_AUDIT_ACTIONS = _tool_contract.TG_AUDIT_ACTIONS
_TG_CHECKPOINT_ACTIONS = _tool_contract.TG_CHECKPOINT_ACTIONS
_TG_CONTEXT_ACTIONS = _tool_contract.TG_CONTEXT_ACTIONS
_TG_EXPLORE_ACTIONS = _tool_contract.TG_EXPLORE_ACTIONS
_TG_IMPACT_ACTIONS = _tool_contract.TG_IMPACT_ACTIONS
_TG_NAVIGATE_ACTIONS = _tool_contract.TG_NAVIGATE_ACTIONS
_TG_QUERY_ACTIONS = _tool_contract.TG_QUERY_ACTIONS
_TG_REWRITE_ACTIONS = _tool_contract.TG_REWRITE_ACTIONS
_TG_SCAN_ACTIONS = _tool_contract.TG_SCAN_ACTIONS
_TG_SESSION_ACTIONS = _tool_contract.TG_SESSION_ACTIONS
register_mcp_tool = _tool_contract.register_mcp_tool

if TYPE_CHECKING:
    from tensor_grep.cli import mcp_server as _self
else:
    _self = sys.modules["tensor_grep.cli.mcp_server"]

# Raised 512 -> 2000  to match the post-cap-fix CLI default
# (repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT) so MCP routing-family tools (defs/context/etc.)
# get the same routing accuracy as the CLI. Safe: the caller-scan cost stays independently
# bounded at 512 by CALLER_SCAN_FILE_CEILING (repo_map.py) regardless of this value -- see
# the rationale comment at repo_map.py's CALLER_SCAN_FILE_CEILING definition.
_DEFAULT_MCP_REPO_SCAN_LIMIT = 2000
# Bound the context payload on the AGENT surface by default (round-6 rank-4). The #359 cap only
# reached the CLI (typer default 16000); the MCP tools an agent actually calls defaulted to None
# (unbounded), so a context pack/render could balloon to ~800KB straight into a model's prompt.
# Mirrors repo_map._DEFAULT_CONTEXT_MAX_TOKENS; 0/None = explicit unbounded opt-out (a guard test
# pins them equal). Literal keeps the heavy repo_map import lazy.
_DEFAULT_MCP_CONTEXT_MAX_TOKENS = 16000
# `tg_find`'s own budget default (Wave 2d, #189 fix-approach council max_tokens must-fix):
# mirrors the CLI's `tg find --max-tokens` default (main.py's `typer.Option(4000, ...)` on the
# `find` command) -- DELIBERATELY DIFFERENT from `_DEFAULT_MCP_CONTEXT_MAX_TOKENS` above, which
# bounds a different tool family (context pack/render/agent-capsule). A guard test
# (test_tg_find_max_tokens_default_matches_cli) pins this literal equal to the CLI's own default
# via `inspect.signature`, mirroring test_inventory.py's
# test_cli_default_max_files_matches_module_constant pattern.
_DEFAULT_MCP_FIND_MAX_TOKENS = 4000


def _mcp_server_version() -> str:
    try:
        return package_version("tensor-grep")
    except PackageNotFoundError:
        return "0+unknown"


# Bump the stable MCP contract for intentional tool/resource or tool-set shape changes.
# CLI version is exposed separately via `tg_mcp_capabilities` -> `cli_version`.
# Earlier contracts added primary path confinement, tools, and task-shaped meta-tools.
# Pass-through producers are public MCP wire surfaces; review their payload changes too.
# compact outputSchema, tg_session preconditions & safety notes
_TG_MCP_SERVER_CONTRACT_VERSION = "1.30.0"


def _apply_mcp_server_metadata(server: MCPServer) -> None:
    server._lowlevel_server.version = _TG_MCP_SERVER_CONTRACT_VERSION


# Initialize the MCPServer server
mcp = MCPServer("tensor-grep")
_apply_mcp_server_metadata(mcp)
_register_tool = register_mcp_tool(mcp)


def _legacy_tools_enabled() -> bool:
    """Whether legacy per-function MCP tool names are individually advertised (default False; set TG_MCP_LEGACY_TOOLS=1 for full 61-tool legacy surface)."""
    return os.environ.get("TG_MCP_LEGACY_TOOLS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _register_legacy_tool(fn: Callable[..., str]) -> Callable[..., str]:
    """Decorator for the 47 legacy (pre-consolidation) MCP tool functions (#98).

    Registers `fn` through the shared tool wrapper only when `_legacy_tools_enabled()` is
    True at IMPORT time. Both paths return `fn` unchanged: the wrapper only
    registers the function, and the 10 meta-tools dispatch directly to these Python
    callables even when their legacy names are de-advertised from `list_tools()`.

    Evaluated once per decoration (import time) so registration and `_MCP_TOOL_CAPABILITIES`
    share the SAME flag read. Each registered tool's description gains a covering-meta-tool
    note after the meta tools are defined (see `_LEGACY_NOTE`).
    """
    if _legacy_tools_enabled():
        return _register_tool(fn)  # type: ignore[no-any-return]
    return fn


_LEGACY_NOTE = (
    "\n\nLegacy per-function tool; meta-tool `{}` (action={}) covers this operation. "
    "Set TG_MCP_LEGACY_TOOLS=off to advertise only the consolidated surface."
)


def _annotate_legacy_tools() -> None:
    from tensor_grep.cli.mcp_tool_discovery import compact_mcp_tool_schemas, legacy_action_for

    # Private MCPServer API: if absent, skip the note rather than break import.
    get_tool = getattr(getattr(_self.mcp, "_tool_manager", None), "get_tool", None)
    for meta, spec in _self._META_MCP_TOOL_CAPABILITIES.items() if get_tool else ():
        for old in cast("list[str]", spec["composes"]):
            if (tool := get_tool(old)) is not None:  # type: ignore[misc]
                tool.description += _LEGACY_NOTE.format(meta, legacy_action_for(old))
    if not _legacy_tools_enabled():
        compact_mcp_tool_schemas(_self.mcp)


# Bound the Content-Length compatibility read. Official MCP stdio is newline-delimited; this framed
# path is a legacy shim, and a hostile/buggy client sending a huge Content-Length must not drive an
# unbounded stdin.read (memory DoS). Mirrors lsp_external_provider._MAX_LSP_MESSAGE_BYTES.
_MAX_MCP_STDIO_MESSAGE_BYTES = 64 * 1024 * 1024

# Bound the header preamble itself (audit #49 part 2): a hostile/buggy client that streams endless
# header lines, or one that never sends the blank-line terminator, must not hang the reader or
# exhaust memory BEFORE the Content-Length size check above even gets a chance to run. Both a hard
# iteration cap (number of header lines) and a hard byte cap (any single line, and the cumulative
# preamble) apply -- fail closed on either, mirroring the body-size cap's fail-closed shape.
_MAX_MCP_STDIO_HEADER_LINES = 128
_MAX_MCP_STDIO_HEADER_BYTES = 64 * 1024

# Bound the FIRST line too (audit #49 part 3, gate must-fix): the first read carries either the
# `Content-Length:` header (framed shim) OR -- in the newline-delimited transport, which is the
# OFFICIAL/primary MCP stdio path -- the ENTIRE JSON-RPC message. `_MAX_MCP_STDIO_MESSAGE_BYTES`
# above caps only the framed body, so without this the newline path had NO message-size cap at all,
# and a `Content-Length` line with no newline could grow memory unbounded BEFORE the size check.
# Cap line 1 at the full message budget (+1 so a maximal message keeps room for its trailing
# newline); a legit MCP message is far under 64MB and a framed header line is tiny, so nothing valid
# is truncated. A first line that reaches this cap with no newline is refused (fail closed).
_MAX_MCP_STDIO_FIRST_LINE_BYTES = _MAX_MCP_STDIO_MESSAGE_BYTES + 1
_JSONRPC_MESSAGE_ADAPTER: pydantic.TypeAdapter[types.JSONRPCMessage] = pydantic.TypeAdapter(
    types.JSONRPCMessage
)


async def _read_bounded_line(stdin: anyio.AsyncFile[bytes], limit: int) -> bytes:
    """``readline()``, but never buffer more than `limit` bytes for a single line.

    ``anyio.AsyncFile.readline()`` does not forward a size bound to the wrapped sync file, so this
    reaches into ``.wrapped`` -- the same underlying binary buffered stream `stdin` already reads
    through, with no text-decoder layer involved, so there is no read-ahead/desync risk in doing
    so -- to call the stdlib's own size-bounded ``readline(limit)``.
    """
    return await anyio.to_thread.run_sync(stdin.wrapped.readline, limit)


async def _read_stdio_message_payload(stdin: anyio.AsyncFile[bytes]) -> str | None:
    first_line_cap = _self._MAX_MCP_STDIO_FIRST_LINE_BYTES
    message_cap = _self._MAX_MCP_STDIO_MESSAGE_BYTES
    header_lines_cap = _self._MAX_MCP_STDIO_HEADER_LINES
    header_bytes_cap = _self._MAX_MCP_STDIO_HEADER_BYTES

    line = await _read_bounded_line(stdin, first_line_cap)
    if line == b"":
        return None
    if len(line) >= first_line_cap and not line.endswith(b"\n"):
        # Fail closed: line 1 reached the read cap with no newline terminator. This is the last
        # unbounded-read vector -- either a `Content-Length:` header line with no newline (memory
        # growth BEFORE the size check below), or a newline-delimited message larger than
        # _MAX_MCP_STDIO_MESSAGE_BYTES (that transport has no other size cap). A complete line that
        # happens to be exactly the cap length still ends in "\n", so this rejects only the
        # over-cap / newline-less case, never truncates a valid message.
        return None
    if not line.strip():
        return ""
    if not line.lower().startswith(b"content-length:"):
        try:
            return line.decode("utf-8")
        except UnicodeDecodeError:
            # Fail closed: a non-UTF-8 line is a framing error, not a silent-wrong parse.
            return None

    try:
        content_length = int(line.split(b":", 1)[1].strip())
    except (IndexError, ValueError):
        try:
            return line.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if content_length <= 0 or content_length > message_cap:
        # Fail closed: a non-positive or oversized frame is refused rather than read unbounded.
        return None

    header_bytes_total = 0
    for _ in range(header_lines_cap):
        header = await _read_bounded_line(stdin, header_bytes_cap)
        if header == b"":
            return None
        header_bytes_total += len(header)
        if header_bytes_total > header_bytes_cap:
            # Fail closed: the header preamble (one oversized line, or too many small ones)
            # exceeded its byte budget before a blank-line terminator ever showed up. Worst case
            # here is bounded to ~2x the byte cap (the over-budget line that tripped this check),
            # never unbounded.
            return None
        if not header.strip():
            break
    else:
        # Fail closed: too many header lines without a blank-line terminator.
        return None

    # Read the body as EXACTLY `content_length` BYTES off the binary stream -- not characters off
    # a text-decoding wrapper. A multi-byte UTF-8 payload has fewer characters than its
    # Content-Length-declared byte count, so a char-count read desyncs every subsequent framed
    # message on the same connection (audit #49).
    body = await stdin.read(content_length)
    if len(body) != content_length:
        # Fail closed: EOF before the declared byte count arrived is a framing error, not a hang
        # or a silently-truncated parse.
        return None
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None


@asynccontextmanager
async def _stdio_server_accepting_content_length(
    stdin: anyio.AsyncFile[bytes] | None = None,
    stdout: anyio.AsyncFile[str] | None = None,
) -> AsyncIterator[tuple[Any, Any]]:
    if not stdin:
        # Raw binary stream -- NOT a TextIOWrapper. `_read_stdio_message_payload` needs byte-exact
        # control over how many bytes it consumes for a Content-Length-framed body; decoding is
        # done once, per logical message, after the correct number of bytes has been read (see
        # audit #49). Mixing a text-decoding wrapper with an explicit byte count desyncs the
        # stream on any multi-byte UTF-8 payload.
        stdin = anyio.wrap_file(sys.stdin.buffer)
    if not stdout:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))

    # Subscript anyio factory so mypy infers item types explicitly.
    read_stream_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](
        0
    )
    write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)

    async def stdin_reader() -> None:
        try:
            async with read_stream_writer:
                while True:
                    payload = await _read_stdio_message_payload(stdin)
                    if payload is None:
                        break
                    if not payload.strip():
                        continue
                    try:
                        message = _JSONRPC_MESSAGE_ADAPTER.validate_json(payload)
                    except Exception as exc:  # pragma: no cover
                        await read_stream_writer.send(exc)
                        continue
                    await read_stream_writer.send(SessionMessage(message))
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async def stdout_writer() -> None:
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    payload = session_message.message.model_dump_json(
                        by_alias=True, exclude_none=True
                    )
                    await stdout.write(payload + "\n")
                    await stdout.flush()
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(stdin_reader)
        task_group.start_soon(stdout_writer)
        yield read_stream, write_stream


async def _run_mcp_stdio_async() -> None:
    _apply_mcp_server_metadata(_self.mcp)
    async with _stdio_server_accepting_content_length() as (read_stream, write_stream):
        await _self.mcp._lowlevel_server.run(
            read_stream, write_stream, _self.mcp._lowlevel_server.create_initialization_options()
        )


def run_mcp_server() -> None:
    """Entry point for the MCP server."""
    anyio.run(_self._run_mcp_stdio_async)
