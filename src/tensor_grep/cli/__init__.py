# perf (+10% campaign #6 / F2.6): `tensor_grep.cli` is the PARENT PACKAGE of every `tensor_grep.
# cli.*` submodule (`bootstrap`, `main`, `runtime_paths`, ...), so THIS file's own top-level import
# cost is paid on LITERALLY EVERY `tg` invocation -- including the trivial `--version` fast path --
# before any submodule-specific code ever runs. `from typing import Any` used to be the ONLY
# reason `typing` (a measurably non-free stdlib import) loaded this early; the sole use below,
# `__getattr__`'s return annotation, never needs to be `Any` specifically -- returning the builtin
# `object` instead needs no import at all (verified: nothing in this repo does `from tensor_grep.
# cli import main_entry` and calls it -- the real console-script entry point in pyproject.toml
# targets `tensor_grep.cli.bootstrap:main_entry` directly, bypassing this `__getattr__` hook
# entirely -- so narrowing this hook's static return type has no in-repo call-site impact).
# NOTE: `from __future__ import annotations` alone would NOT have been sufficient here -- PEP 563
# only defers *evaluation* of an annotation to a string; mypy still resolves that string against
# the module's real (possibly TYPE_CHECKING-gated) imports, and a bare `from typing import
# TYPE_CHECKING` pays the exact same `typing`-module-load cost this fix exists to avoid. Verified:
# `mypy --strict` on this file with `-> Any` and no `typing` import at all fails with `Name "Any"
# is not defined`; `-> object` needs no import and passes clean.
from importlib import import_module

__all__ = ["main_entry"]


def __getattr__(name: str) -> object:
    if name != "main_entry":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = import_module("tensor_grep.cli.bootstrap").main_entry
    globals()[name] = value
    return value
