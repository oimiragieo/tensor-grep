"""Test helper: give an ``ExternalLSPClient`` an explicit, fully-built ``ProviderSession``.

The client no longer forwards attribute ASSIGNMENTS to "whichever session is current" (that
certified the wrong provider after a restart), so tests that need a client in a given state build
the session explicitly with the fields they care about, using the ``ProviderSession`` field names
(``process``, ``containment``, ``capabilities``, ``initialized``, ``lsp_provider_response``,
``stderr_tail``, ``opened_documents`` ...).
"""

from __future__ import annotations

from typing import Any

from tensor_grep.cli.lsp_session import ProviderSession


def install_session(client: Any, **fields: Any) -> ProviderSession:
    """Replace ``client``'s current session with a NEW one carrying ``fields``; returns it."""
    session = ProviderSession(client._session.generation + 1)
    for name, value in fields.items():
        if not hasattr(session, name):
            raise AttributeError(f"ProviderSession has no field {name!r}")
        setattr(session, name, value)
    client._session = session
    return session
