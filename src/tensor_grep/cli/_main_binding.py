"""Late-bound reference to ``tensor_grep.cli.main``'s module object.

WHY THIS EXISTS
---------------
``cli/main.py`` is being split into sibling helper modules
(``docs/design/2026-08-19-split-floor-escape.md``). The thing that made that split
impossible until PR #1042 is that Python resolves a bare name through the DEFINING
module's globals: a function that calls a monkeypatched name as a bare identifier is
welded to the file the tests patch. Move it and **the test still passes while production
runs the unpatched original** -- a silent false green.

Route A converted every such call site inside ``main.py`` to ``_self.NAME(...)``, where
``_self`` is ``main``'s own module object. This module carries that same ``_self`` into
the extracted sibling modules, still pointing at ``tensor_grep.cli.main``. A moved
function keeps reading through ``main``'s globals, so ``monkeypatch.setattr(main, ...)``
keeps winning exactly as it did before the move, and the extracted module needs no
import-time dependency on ``main`` at all.

WHY A PROXY RATHER THAN ``sys.modules[__name__]``
-------------------------------------------------
``main.py`` can write ``_self = sys.modules[__name__]`` because by then it is itself
mid-import and therefore registered. A *sibling* module cannot: ``main`` imports it, so
at that moment ``main`` is only partially initialised, and a module-level
``sys.modules["tensor_grep.cli.main"]`` would additionally raise ``KeyError`` for anyone
who imports the sibling directly. The proxy resolves on ATTRIBUTE ACCESS, which only ever
happens inside a function body -- long after ``main`` has finished importing.

The two branches are load-bearing for the same reason they are in ``main.py``: only the
runtime branch works at runtime, but it is untyped, so every converted call would return
``Any``. The ``TYPE_CHECKING`` branch never executes; it exists so the checker resolves
``_self`` to the ``main`` module and keeps every real signature.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

# Explicit re-export: `_self` is bound by an aliased import under TYPE_CHECKING, and mypy runs
# with `implicit_reexport = false`, under which `from x import y as z` binds privately.
__all__ = ["_self"]

_MAIN_MODULE = "tensor_grep.cli.main"

if TYPE_CHECKING:
    from tensor_grep.cli import main as _self
else:

    class _MainModuleProxy:
        """Forwards every attribute read to ``tensor_grep.cli.main``, at call time."""

        __slots__ = ()

        def __repr__(self) -> str:  # pragma: no cover - diagnostic only
            return f"<late-bound {_MAIN_MODULE}>"

        def __getattr__(self, name: str) -> Any:
            try:
                module = sys.modules[_MAIN_MODULE]
            except KeyError:  # pragma: no cover - only on a direct sibling import
                import importlib

                module = importlib.import_module(_MAIN_MODULE)
            return getattr(module, name)

    _self = _MainModuleProxy()
