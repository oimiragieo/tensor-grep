"""Historical entry point for MCP server unit tests.

The suite was split (W4-e) into themed siblings under ``tests/unit/``:

- ``test_mcp_server_search.py``
- ``test_mcp_server_ruleset_scan.py``
- ``test_mcp_server_rewrite_audit.py``
- ``test_mcp_server_path_confinement.py``
- ``test_mcp_server_context_session.py``
- ``test_mcp_server_symbol_navigation.py``
- ``test_mcp_server_meta_dispatch.py``

Shared helpers (including confinement ratchet tables) live in
``test_mcp_server_shared.py``.

This module re-exports those helpers for importers that still load
``test_mcp_server.py`` by path. It deliberately defines no ``test_*``
functions so pytest does not double-collect.

Note: ``from module import *`` skips ``_``-prefixed names, so this file
loads the shared module by path and copies its public namespace explicitly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SHARED_PATH = Path(__file__).with_name("test_mcp_server_shared.py")
_SPEC = importlib.util.spec_from_file_location("_tg_mcp_server_shared_shim", _SHARED_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_SHARED = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SHARED)

globals().update({key: value for key, value in vars(_SHARED).items() if not key.startswith("__")})
