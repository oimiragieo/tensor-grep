from importlib import import_module
from typing import Any

__all__ = ["main_entry"]


def __getattr__(name: str) -> Any:
    if name != "main_entry":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = import_module("tensor_grep.cli.bootstrap").main_entry
    globals()[name] = value
    return value
