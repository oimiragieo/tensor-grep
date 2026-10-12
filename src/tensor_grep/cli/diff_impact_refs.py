"""Parser-backed reference evidence helpers for diff-impact."""

from __future__ import annotations

import ast
import os
import sys
import time
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.repo_map_coverage_gaps import blocking_gaps
from tensor_grep.cli.repo_map_test_paths import _is_test_file


def _opened_file_path(fd: int, path: Path) -> Path:
    if sys.platform != "win32":
        try:
            return Path(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            if os.name != "nt":
                if sys.platform != "darwin":
                    raise
                import fcntl

                return Path(fcntl.fcntl(fd, fcntl.F_GETPATH).decode("utf-8", errors="strict"))
    if os.name == "nt":
        import ctypes
        import msvcrt

        get_name = ctypes.windll.kernel32.GetFinalPathNameByHandleW
        get_name.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        get_name.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_name(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise OSError("opened file path unavailable")
        name = buffer.value
        if name.startswith("\\\\?\\UNC\\"):
            name = "\\\\" + name[8:]
        elif name.startswith("\\\\?\\"):
            name = name[4:]
        return Path(name)
    raise OSError("opened file path unavailable")


def _origin_modules(root: Path, origin_files: tuple[str, ...]) -> set[str]:
    modules: set[str] = set()
    for raw_origin in origin_files:
        origin = Path(raw_origin)
        if origin.is_absolute():
            origin = origin.relative_to(root)
        if origin.suffix.lower() != ".py" or ".." in origin.parts:
            continue
        parts = list(origin.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            modules.add(".".join(parts))
        if len(parts) > 1 and parts[0] == "src":
            modules.add(".".join(parts[1:]))
    return modules


def _import_module(node: ast.ImportFrom, importer: Path, root: Path) -> str | None:
    if not node.level:
        return node.module
    package = list(importer.relative_to(root).parts[:-1])
    if node.level > len(package):
        return None
    prefix = package[: len(package) - node.level + 1]
    return ".".join([*prefix, *node.module.split(".")]) if node.module else ".".join(prefix)


def _attribute_path(node: ast.expr) -> tuple[str, ...] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return (node.id, *reversed(parts))


def _constant_string(node: ast.expr) -> str | None:
    parts: list[str] = []
    pending = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, ast.Constant) and isinstance(current.value, str):
            parts.append(current.value)
        elif isinstance(current, ast.BinOp) and isinstance(current.op, ast.Add):
            pending.extend((current.right, current.left))
        else:
            return None
    return "".join(parts)


class _ImportBindingState:
    __slots__ = ("binding_paths", "name_bindings")

    def __init__(self) -> None:
        self.name_bindings: dict[str, tuple[str, ...]] = {}
        self.binding_paths: set[tuple[str, ...]] = set()

    def register_name(self, name: str, binding: tuple[str, ...]) -> None:
        self.name_bindings[name] = binding
        self.binding_paths.add(binding)
        self.binding_paths.add((name,))

    def expr_binding_path(self, expr: ast.expr) -> tuple[str, ...] | None:
        path = _attribute_path(expr)
        if path is not None and path in self.binding_paths:
            return path
        if isinstance(expr, ast.Name):
            return self.name_bindings.get(expr.id)
        return None


def _python_import_reference(
    repo_map_payload: dict[str, Any],
    symbol: str,
    deadline_monotonic: float | None,
    origin_files: tuple[str, ...],
) -> tuple[bool, bool]:
    files, tests = repo_map._repo_map_file_and_test_universe(repo_map_payload)
    root = repo_map._repo_map_root_dir(repo_map_payload)
    try:
        origin_modules = _origin_modules(root, origin_files)
    except (OSError, ValueError):
        return False, False
    if not origin_modules:
        return True, False
    max_bytes = repo_map._max_parse_bytes()
    for path in [*files, *tests]:
        if path.suffix.lower() != ".py":
            continue
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return False, False
        if not repo_map._path_is_relative_to(path, root):
            return False, False
        try:
            with path.open("rb") as source_file:
                opened_path = _opened_file_path(source_file.fileno(), path)
                opened_path.relative_to(root)
                opened_stat = os.fstat(source_file.fileno())
                current_stat = path.stat()
                if (opened_stat.st_dev, opened_stat.st_ino) != (
                    current_stat.st_dev,
                    current_stat.st_ino,
                ):
                    return False, False
                raw = source_file.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return False, False
            tree = ast.parse(raw.decode("utf-8-sig", errors="strict"))
        except (OSError, UnicodeError, SyntaxError, ValueError, RecursionError, MemoryError):
            return False, False
        bindings = _ImportBindingState()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in origin_modules:
                        continue
                    module_path = tuple(alias.name.split("."))
                    if alias.asname:
                        bindings.register_name(alias.asname, module_path)
                    else:
                        bindings.binding_paths.add(module_path)
                        if len(module_path) == 1:
                            bindings.register_name(module_path[0], module_path)
            elif isinstance(node, ast.ImportFrom):
                module = _import_module(node, path, root)
                if module is None:
                    continue
                if module in origin_modules and any(
                    alias.name in {symbol, "*"} for alias in node.names
                ):
                    return True, True
                for alias in node.names:
                    qualified = f"{module}.{alias.name}"
                    if qualified not in origin_modules:
                        continue
                    binding = tuple(qualified.split("."))
                    bindings.register_name(alias.asname or alias.name, binding)
            elif (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Name)
            ):
                source = bindings.name_bindings.get(node.value.id)
                if source is not None:
                    bindings.register_name(node.targets[0].id, source)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == symbol
                and bindings.expr_binding_path(node.value) is not None
            ):
                return True, True
            if (
                isinstance(node, ast.Subscript)
                and bindings.expr_binding_path(node.value) is not None
            ):
                return False, False
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id != "getattr" or len(node.args) < 2:
                    continue
                if bindings.expr_binding_path(node.args[0]) is None:
                    continue
                attribute_node = node.args[1]
                if not isinstance(attribute_node, ast.Constant) or not isinstance(
                    attribute_node.value, str
                ):
                    return False, False
                if attribute_node.value == symbol:
                    return True, True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Call):
                inner = node.func
                if (
                    isinstance(inner.func, ast.Name)
                    and inner.func.id == "getattr"
                    and len(inner.args) >= 2
                    and bindings.expr_binding_path(inner.args[0]) is not None
                ):
                    attribute_node = inner.args[1]
                    if not isinstance(attribute_node, ast.Constant) or not isinstance(
                        attribute_node.value, str
                    ):
                        return False, False
                    if attribute_node.value == symbol:
                        return True, True
    return True, False


def _test_reference_evidence(
    repo_map_payload: dict[str, Any],
    symbol: str,
    deadline_monotonic: float | None,
    max_repo_files: int | None,
    origin_files: tuple[str, ...] = (),
    lookup_incomplete: list[str] | None = None,
) -> tuple[bool, bool]:
    """Return complete-reference-data and whether a test reference or Python import exists."""
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        if lookup_incomplete is not None:
            lookup_incomplete.append("deadline_exceeded")
        return False, False
    if max_repo_files is not None:
        files, tests = repo_map._repo_map_file_and_test_universe(repo_map_payload)
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            if lookup_incomplete is not None:
                lookup_incomplete.append("deadline_exceeded")
            return False, False
        if len(files) + len(tests) > max(1, int(max_repo_files)):
            if lookup_incomplete is not None:
                lookup_incomplete.append("test_reference_lookup_incomplete")
            return False, False
    try:
        payload = repo_map.build_symbol_refs_from_map(
            repo_map_payload,
            symbol,
            deadline_monotonic=deadline_monotonic,
        )
    except Exception:
        if lookup_incomplete is not None:
            lookup_incomplete.append("test_reference_lookup_incomplete")
        return False, False

    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        if lookup_incomplete is not None:
            lookup_incomplete.append("deadline_exceeded")
        return False, False

    truncated = (
        payload.get("partial")
        or payload.get("result_incomplete")
        or bool(payload.get("deadline_limit"))
        or bool(payload.get("scan_limit", {}).get("possibly_truncated"))
        or bool(payload.get("caller_scan_limit", {}).get("possibly_truncated"))
    )
    reference_groups = [
        value for key, value in payload.items() if key == "references" or key.endswith("_refs")
    ]
    if (
        truncated
        or not isinstance(payload.get("references"), list)
        or not isinstance(payload.get("string_refs"), list)
        or not all(isinstance(group, list) for group in reference_groups)
    ):
        if lookup_incomplete is not None:
            lookup_incomplete.append(
                "deadline_exceeded"
                if payload.get("deadline_limit")
                else "test_reference_lookup_incomplete"
            )
        return False, False
    if payload.get("no_match"):
        return False, False
    if blocking_gaps(payload.get("resolution_gaps") or [], answer_empty=not any(reference_groups)):
        return False, False
    has_test_reference = any(
        isinstance(reference, dict) and _is_test_file(Path(str(reference.get("file", ""))))
        for group in reference_groups
        for reference in group
    )
    if has_test_reference:
        return True, True
    imports_complete, has_import_reference = _python_import_reference(
        repo_map_payload, symbol, deadline_monotonic, origin_files
    )
    if not imports_complete and lookup_incomplete is not None:
        lookup_incomplete.append("test_reference_lookup_incomplete")
    return imports_complete, has_import_reference
