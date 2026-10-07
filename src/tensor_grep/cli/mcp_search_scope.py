"""Carry default-path evidence across synchronous MCP meta-tool dispatch.

Handlers still receive and revalidate the confined absolute path. Request-local
metadata preserves the original scope choice without exposing a guard override in
the public tool schema. ContextVar isolates concurrent requests; reset also covers
exceptions and nested calls.
"""

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

_scope: ContextVar[tuple[str, bool] | None] = ContextVar("mcp_search_scope", default=None)


def paths_defaulted(path: str) -> bool:
    scope = _scope.get()
    return path == "." or (scope is not None and scope[0] == path and scope[1])


def call(handler: Callable[..., str], *, path: str, paths_defaulted: bool, **kwargs: Any) -> str:
    """Call a synchronous handler with confined path and original scope evidence."""
    token = _scope.set((path, paths_defaulted))
    try:
        return handler(path=path, **kwargs)
    finally:
        _scope.reset(token)
