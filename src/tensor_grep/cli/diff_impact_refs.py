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


def _bound_module_expr(expr: ast.expr, bindings: _ImportBindingState) -> bool:
    if bindings.expr_binding_path(expr) is not None:
        return True
    if isinstance(expr, ast.Name) and expr.id in bindings.container_holders:
        return True
    if isinstance(expr, ast.Attribute) and expr.attr == "__dict__":
        return _bound_module_expr(expr.value, bindings)
    if isinstance(expr, ast.Subscript):
        return _bound_module_expr(expr.value, bindings)
    if isinstance(expr, (ast.List, ast.Tuple)):
        return any(_bound_module_expr(elt, bindings) for elt in expr.elts)
    return False


def _uses_module_dict_surface(callee: ast.expr, bindings: _ImportBindingState) -> bool:
    if isinstance(callee, ast.Attribute):
        if callee.attr == "__dict__" and _bound_module_expr(callee.value, bindings):
            return True
        return _uses_module_dict_surface(callee.value, bindings)
    if isinstance(callee, ast.Call):
        return _uses_module_dict_surface(callee.func, bindings)
    return False


class _ImportBindingState:
    __slots__ = (
        "attrgetter_assigned_aliases",
        "attrgetter_import_aliases",
        "binding_paths",
        "bound_attrgetter_targets",
        "bound_getter_results",
        "builtin_module_aliases",
        "container_holders",
        "getattr_aliases",
        "getattr_assigned_aliases",
        "getattr_import_aliases",
        "methodcaller_import_aliases",
        "module_dict_holders",
        "name_bindings",
        "operator_attrgetter_aliases",
        "operator_methodcaller_aliases",
        "operator_module_aliases",
        "typed_getattr_aliases",
        "typed_module_aliases",
    )

    def __init__(self) -> None:
        self.name_bindings: dict[str, tuple[str, ...]] = {}
        self.binding_paths: set[tuple[str, ...]] = set()
        self.getattr_aliases: set[str] = {"getattr"}
        self.getattr_assigned_aliases: set[str] = set()
        self.getattr_import_aliases: set[str] = set()
        self.operator_attrgetter_aliases: set[str] = {"attrgetter"}
        self.attrgetter_import_aliases: set[str] = set()
        self.attrgetter_assigned_aliases: set[str] = set()
        self.operator_module_aliases: set[str] = {"operator"}
        self.operator_methodcaller_aliases: set[str] = {"methodcaller"}
        self.methodcaller_import_aliases: set[str] = set()
        self.bound_attrgetter_targets: dict[str, str | None] = {}
        self.bound_getter_results: dict[str, str | None] = {}
        self.builtin_module_aliases: set[str] = {"builtins"}
        self.typed_module_aliases: set[str] = set()
        self.typed_getattr_aliases: set[str] = set()
        self.container_holders: set[str] = set()
        self.module_dict_holders: set[str] = set()

    def register_name(self, name: str, binding: tuple[str, ...]) -> None:
        self.name_bindings[name] = binding
        self.binding_paths.add(binding)

    def expr_binding_path(self, expr: ast.expr) -> tuple[str, ...] | None:
        if isinstance(expr, ast.Tuple) and len(expr.elts) == 1:
            return self.expr_binding_path(expr.elts[0])
        if isinstance(expr, ast.Name):
            return self.name_bindings.get(expr.id)
        path = _attribute_path(expr)
        if path is None:
            return None
        if path in self.binding_paths:
            return path
        root = path[0]
        binding = self.name_bindings.get(root)
        if binding is None:
            return None
        if len(path) == 1:
            return binding
        if path == binding:
            return binding
        return None


def _module_binding_from_expr(
    expr: ast.expr, bindings: _ImportBindingState
) -> tuple[str, ...] | None:
    if isinstance(expr, ast.Tuple) and len(expr.elts) == 1:
        return _module_binding_from_expr(expr.elts[0], bindings)
    if isinstance(expr, ast.Name):
        return bindings.name_bindings.get(expr.id)
    return bindings.expr_binding_path(expr)


def _register_module_alias_target(
    target: ast.expr, source: tuple[str, ...], bindings: _ImportBindingState
) -> None:
    if isinstance(target, ast.Name):
        bindings.register_name(target.id, source)
        return
    if isinstance(target, ast.Tuple):
        for elt in target.elts:
            if isinstance(elt, ast.Name):
                bindings.register_name(elt.id, source)


def _call_is_getattr(func: ast.expr, bindings: _ImportBindingState) -> bool:
    if isinstance(func, ast.Name):
        return func.id in bindings.getattr_aliases
    if isinstance(func, ast.Attribute) and func.attr == "getattr":
        return isinstance(func.value, ast.Name) and func.value.id in bindings.builtin_module_aliases
    return False


def _getattr_call_reference(
    node: ast.Call, bindings: _ImportBindingState, symbol: str
) -> tuple[bool, bool] | None:
    if len(node.args) < 2 or not _call_is_getattr(node.func, bindings):
        return None
    if isinstance(node.func, ast.Name) and node.func.id in bindings.typed_getattr_aliases:
        return False, False
    getattr_name = node.func.id if isinstance(node.func, ast.Name) else "getattr"
    if isinstance(node.args[0], ast.Name) and node.args[0].id in bindings.typed_module_aliases:
        return False, False
    if not _bound_module_expr(node.args[0], bindings):
        if isinstance(node.args[0], ast.Name):
            return False, False
        return None
    attribute_node = node.args[1]
    allow_fold = (
        isinstance(node.func, ast.Name)
        and node.func.id in bindings.getattr_import_aliases
        and node.func.id not in bindings.getattr_assigned_aliases
    )
    if getattr_name == "getattr" or not allow_fold:
        if not isinstance(attribute_node, ast.Constant) or not isinstance(
            attribute_node.value, str
        ):
            return False, False
        attribute = attribute_node.value
    else:
        folded = _constant_string(attribute_node)
        if folded is None:
            return False, False
        attribute = folded
    if attribute == symbol:
        return True, True
    return None


def _call_is_methodcaller_factory(func: ast.expr, bindings: _ImportBindingState) -> bool:
    if isinstance(func, ast.Name):
        return func.id in bindings.operator_methodcaller_aliases
    if isinstance(func, ast.Attribute) and func.attr == "methodcaller":
        return (
            isinstance(func.value, ast.Name) and func.value.id in bindings.operator_module_aliases
        )
    return False


def _methodcaller_call_reference(
    node: ast.Call, bindings: _ImportBindingState, symbol: str
) -> tuple[bool, bool] | None:
    if not isinstance(node.func, ast.Call):
        return None
    bound_call = node.func
    factory = bound_call.func
    if not isinstance(factory, ast.Call) or not _call_is_methodcaller_factory(
        factory.func, bindings
    ):
        return None
    if not bound_call.args or not _bound_module_expr(bound_call.args[0], bindings):
        return None
    allow_fold = isinstance(factory.func, ast.Attribute) or (
        isinstance(factory.func, ast.Name)
        and factory.func.id in bindings.methodcaller_import_aliases
    )
    if not factory.args:
        return False, False
    if len(factory.args) > 1:
        return False, False
    if allow_fold:
        attribute = _constant_string(factory.args[0])
    else:
        attribute_node = factory.args[0]
        if not isinstance(attribute_node, ast.Constant) or not isinstance(
            attribute_node.value, str
        ):
            return False, False
        attribute = attribute_node.value
    if attribute is None:
        return False, False
    if attribute == symbol:
        return True, True
    return None


def _call_is_attrgetter_factory(func: ast.expr, bindings: _ImportBindingState) -> bool:
    if isinstance(func, ast.Name):
        return func.id in bindings.operator_attrgetter_aliases
    if isinstance(func, ast.Attribute) and func.attr == "attrgetter":
        return (
            isinstance(func.value, ast.Name) and func.value.id in bindings.operator_module_aliases
        )
    return False


def _attrgetter_factory_attribute(
    factory: ast.Call, bindings: _ImportBindingState
) -> str | None | bool:
    if not _call_is_attrgetter_factory(factory.func, bindings):
        return False
    allow_fold = isinstance(factory.func, ast.Attribute) or (
        isinstance(factory.func, ast.Name)
        and factory.func.id in bindings.attrgetter_import_aliases
        and factory.func.id not in bindings.attrgetter_assigned_aliases
    )
    if not factory.args:
        return None
    if len(factory.args) > 1:
        return None
    if allow_fold:
        return _constant_string(factory.args[0])
    attribute_node = factory.args[0]
    if not isinstance(attribute_node, ast.Constant) or not isinstance(attribute_node.value, str):
        return None
    return attribute_node.value


def _register_bound_attrgetter_target(
    target: ast.expr, factory: ast.Call, bindings: _ImportBindingState
) -> None:
    attribute = _attrgetter_factory_attribute(factory, bindings)
    if attribute is False:
        return
    if isinstance(target, ast.Name):
        bindings.bound_attrgetter_targets[target.id] = None if attribute is None else str(attribute)
    elif isinstance(target, ast.Tuple):
        for elt in target.elts:
            if isinstance(elt, ast.Name):
                bindings.bound_attrgetter_targets[elt.id] = (
                    None if attribute is None else str(attribute)
                )


def _attrgetter_bound_module_call(
    call: ast.Call, bindings: _ImportBindingState
) -> str | None | bool:
    if isinstance(call.func, ast.Name):
        attribute = bindings.bound_attrgetter_targets.get(call.func.id)
        if attribute is None and call.func.id not in bindings.bound_attrgetter_targets:
            return False
        if not call.args or not _bound_module_expr(call.args[0], bindings):
            return False
        return attribute
    if not isinstance(call.func, ast.Call):
        return False
    inner = call.func
    if _call_is_attrgetter_factory(inner.func, bindings):
        if not call.args or not _bound_module_expr(call.args[0], bindings):
            return False
        return _attrgetter_factory_attribute(inner, bindings)
    if isinstance(inner.func, ast.Call) and _call_is_attrgetter_factory(inner.func.func, bindings):
        if not inner.args or not _bound_module_expr(inner.args[0], bindings):
            return False
        return _attrgetter_factory_attribute(inner.func, bindings)
    if isinstance(inner.func, ast.Name):
        attribute = bindings.bound_attrgetter_targets.get(inner.func.id)
        if attribute is None and inner.func.id not in bindings.bound_attrgetter_targets:
            return False
        if not inner.args or not _bound_module_expr(inner.args[0], bindings):
            return False
        return attribute
    return False


def _register_bound_getter_result(
    target: ast.expr,
    call: ast.Call,
    bindings: _ImportBindingState,
    symbol: str,
) -> bool:
    attribute = _attrgetter_bound_module_call(call, bindings)
    if attribute is False:
        return False
    if isinstance(target, ast.Name):
        bindings.bound_getter_results[target.id] = None if attribute is None else str(attribute)
    elif isinstance(target, ast.Tuple):
        for elt in target.elts:
            if isinstance(elt, ast.Name):
                bindings.bound_getter_results[elt.id] = (
                    None if attribute is None else str(attribute)
                )
    return isinstance(attribute, str) and attribute == symbol


def _bound_getter_result_call_reference(
    node: ast.Call, bindings: _ImportBindingState, symbol: str
) -> tuple[bool, bool] | None:
    if not isinstance(node.func, ast.Name):
        return None
    attribute = bindings.bound_getter_results.get(node.func.id)
    if attribute is None and node.func.id not in bindings.bound_getter_results:
        return None
    if attribute is None:
        return False, False
    if attribute == symbol:
        return True, True
    return None


def _bound_attrgetter_call_reference(
    node: ast.Call, bindings: _ImportBindingState, symbol: str
) -> tuple[bool, bool] | None:
    if not isinstance(node.func, ast.Call):
        return None
    bound_call = node.func
    if not isinstance(bound_call.func, ast.Name):
        return None
    attribute = bindings.bound_attrgetter_targets.get(bound_call.func.id)
    if attribute is None and bound_call.func.id not in bindings.bound_attrgetter_targets:
        return None
    if not bound_call.args or not _bound_module_expr(bound_call.args[0], bindings):
        return None
    if attribute is None:
        return False, False
    if attribute == symbol:
        return True, True
    return None


def _attrgetter_call_reference(
    node: ast.Call, bindings: _ImportBindingState, symbol: str
) -> tuple[bool, bool] | None:
    if not isinstance(node.func, ast.Call):
        return None
    bound_call = node.func
    factory = bound_call.func
    if not isinstance(factory, ast.Call) or not _call_is_attrgetter_factory(factory.func, bindings):
        return None
    if not bound_call.args or not _bound_module_expr(bound_call.args[0], bindings):
        return None
    allow_fold = isinstance(factory.func, ast.Attribute) or (
        isinstance(factory.func, ast.Name)
        and factory.func.id in bindings.attrgetter_import_aliases
        and factory.func.id not in bindings.attrgetter_assigned_aliases
    )
    if not factory.args:
        return False, False
    if allow_fold:
        attribute = _constant_string(factory.args[0])
    else:
        attribute_node = factory.args[0]
        if not isinstance(attribute_node, ast.Constant) or not isinstance(
            attribute_node.value, str
        ):
            return False, False
        attribute = attribute_node.value
    if attribute is None:
        return False, False
    if attribute == symbol:
        return True, True
    return None


def _import_alias_call_reference(
    node: ast.Call,
    bindings: _ImportBindingState,
    symbol: str,
    origin_modules: set[str],
) -> tuple[bool, bool] | None:
    if not isinstance(node.func, ast.Name):
        return None
    binding = bindings.name_bindings.get(node.func.id)
    if binding is None or not binding or binding[-1] != symbol:
        return None
    module = ".".join(binding[:-1]) if len(binding) > 1 else ""
    if module in origin_modules:
        return True, True
    if len(binding) == 1 and binding[0] in origin_modules:
        return True, True
    return None


def _python_import_reference(
    repo_map_payload: dict[str, Any],
    symbol: str,
    deadline_monotonic: float | None,
    origin_files: tuple[str, ...],
    *,
    unreadable_hit: repo_map._UnreadablePathFlag | None = None,
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
    scan_paths: list[Path] = []
    seen_scan: set[str] = set()
    for path in [*files, *tests]:
        key = os.path.abspath(str(path))
        if key in seen_scan:
            continue
        seen_scan.add(key)
        scan_paths.append(path)
    for raw_path in repo_map_payload.get("tests", []) or []:
        candidate = Path(str(raw_path)).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        if not candidate.is_symlink() or candidate.suffix.lower() != ".py":
            continue
        key = os.path.abspath(str(candidate))
        if key in seen_scan:
            continue
        seen_scan.add(key)
        scan_paths.append(candidate)
    for path in scan_paths:
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
                    if unreadable_hit is not None:
                        unreadable_hit.record(OSError("path identity changed during read"))
                        continue
                    return False, False
                raw = source_file.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return False, False
            tree = ast.parse(raw.decode("utf-8-sig", errors="strict"))
        except OSError as exc:
            if unreadable_hit is not None:
                unreadable_hit.record(exc)
                continue
            return False, False
        except (UnicodeError, SyntaxError, ValueError, RecursionError, MemoryError):
            return False, False
        bindings = _ImportBindingState()
        ordered_nodes = sorted(
            (node for node in ast.walk(tree) if hasattr(node, "lineno")),
            key=lambda node: node.lineno,
        )

        for node in ordered_nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "builtins":
                        bindings.builtin_module_aliases.add(alias.asname or "builtins")
                    if alias.name == "operator":
                        bindings.operator_module_aliases.add(alias.asname or "operator")
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
                if (
                    _is_test_file(path)
                    and module in origin_modules
                    and any(alias.name in {symbol, "*"} for alias in node.names)
                ):
                    return True, True
                for alias in node.names:
                    if alias.name == "getattr":
                        imported = alias.asname or "getattr"
                        bindings.getattr_aliases.add(imported)
                        bindings.getattr_import_aliases.add(imported)
                    if alias.name == "attrgetter" and module == "operator":
                        imported = alias.asname or "attrgetter"
                        bindings.operator_attrgetter_aliases.add(imported)
                        bindings.attrgetter_import_aliases.add(imported)
                    if alias.name == "methodcaller" and module == "operator":
                        imported = alias.asname or "methodcaller"
                        bindings.operator_methodcaller_aliases.add(imported)
                        bindings.methodcaller_import_aliases.add(imported)
                    qualified = f"{module}.{alias.name}"
                    if module in origin_modules and alias.name == symbol:
                        binding = (*module.split("."), alias.name)
                        bindings.register_name(alias.asname or alias.name, binding)
                    elif qualified in origin_modules:
                        binding = tuple(qualified.split("."))
                        bindings.register_name(alias.asname or alias.name, binding)

        bound_getter_symbol_reference = False
        for node in ordered_nodes:
            if isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Name) and node.value.id == "getattr":
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bindings.getattr_aliases.add(target.id)
                            bindings.getattr_assigned_aliases.add(target.id)
                elif (
                    isinstance(node.value, ast.Attribute)
                    and node.value.attr == "getattr"
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id in bindings.builtin_module_aliases
                ):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bindings.getattr_aliases.add(target.id)
                            bindings.getattr_assigned_aliases.add(target.id)
                elif isinstance(node.value, ast.Name) and node.value.id in bindings.getattr_aliases:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bindings.getattr_aliases.add(target.id)
                            bindings.getattr_assigned_aliases.add(target.id)
                elif (
                    isinstance(node.value, ast.Name)
                    and node.value.id in bindings.operator_attrgetter_aliases
                ):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bindings.operator_attrgetter_aliases.add(target.id)
                            bindings.attrgetter_assigned_aliases.add(target.id)
                elif isinstance(node.value, ast.Call) and _call_is_attrgetter_factory(
                    node.value.func, bindings
                ):
                    for target in node.targets:
                        _register_bound_attrgetter_target(target, node.value, bindings)
                elif isinstance(node.value, ast.Call):
                    for target in node.targets:
                        if _register_bound_getter_result(target, node.value, bindings, symbol):
                            bound_getter_symbol_reference = True
                if isinstance(node.value, (ast.List, ast.Tuple)) and any(
                    _bound_module_expr(elt, bindings) for elt in node.value.elts
                ):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bindings.container_holders.add(target.id)
                if isinstance(node.value, ast.Dict) and any(
                    value is not None and _bound_module_expr(value, bindings)
                    for value in node.value.values
                ):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bindings.container_holders.add(target.id)
                if (
                    isinstance(node.value, ast.Attribute)
                    and node.value.attr == "__dict__"
                    and _bound_module_expr(node.value.value, bindings)
                ):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bindings.module_dict_holders.add(target.id)
                source = _module_binding_from_expr(node.value, bindings)
                if source is None:
                    continue
                for target in node.targets:
                    if isinstance(target, (ast.Name, ast.Tuple)):
                        _register_module_alias_target(target, source, bindings)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                if isinstance(node.value, ast.Name) and node.value.id == "getattr":
                    if isinstance(node.target, ast.Name):
                        bindings.getattr_aliases.add(node.target.id)
                        bindings.getattr_assigned_aliases.add(node.target.id)
                        if node.annotation is not None:
                            bindings.typed_getattr_aliases.add(node.target.id)
                elif (
                    isinstance(node.value, ast.Attribute)
                    and node.value.attr == "getattr"
                    and isinstance(node.value.value, ast.Name)
                    and node.value.value.id in bindings.builtin_module_aliases
                    and isinstance(node.target, ast.Name)
                ):
                    bindings.getattr_aliases.add(node.target.id)
                    bindings.getattr_assigned_aliases.add(node.target.id)
                    if node.annotation is not None:
                        bindings.typed_getattr_aliases.add(node.target.id)
                elif isinstance(node.value, ast.Call) and _call_is_attrgetter_factory(
                    node.value.func, bindings
                ):
                    _register_bound_attrgetter_target(node.target, node.value, bindings)
                elif isinstance(node.value, ast.Call):
                    if _register_bound_getter_result(node.target, node.value, bindings, symbol):
                        bound_getter_symbol_reference = True
                source = _module_binding_from_expr(node.value, bindings)
                if source is not None and isinstance(node.target, (ast.Name, ast.Tuple)):
                    _register_module_alias_target(node.target, source, bindings)
                    if node.annotation is not None:
                        if isinstance(node.target, ast.Name):
                            bindings.typed_module_aliases.add(node.target.id)
                        elif isinstance(node.target, ast.Tuple):
                            for elt in node.target.elts:
                                if isinstance(elt, ast.Name):
                                    bindings.typed_module_aliases.add(elt.id)

        if bound_getter_symbol_reference:
            return True, True

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "vars" and len(node.args) == 1:
                    if bindings.expr_binding_path(node.args[0]) is not None:
                        return False, False
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "vars"
                and len(node.value.args) == 1
                and bindings.expr_binding_path(node.value.args[0]) is not None
            ):
                return False, False
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "__getattribute__"
                and _bound_module_expr(node.value, bindings)
            ):
                return False, False
            if (
                isinstance(node, ast.Attribute)
                and node.attr == symbol
                and _bound_module_expr(node.value, bindings)
            ):
                return True, True
            if isinstance(node, ast.Subscript):
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id in bindings.module_dict_holders
                ):
                    return False, False
                if (
                    isinstance(node.value, ast.Attribute)
                    and node.value.attr == "__dict__"
                    and _bound_module_expr(node.value.value, bindings)
                ):
                    return False, False
                if _bound_module_expr(node.value, bindings) and not isinstance(
                    node.slice, ast.Constant
                ):
                    return False, False
            if isinstance(node, ast.Call):
                if _uses_module_dict_surface(node.func, bindings):
                    return False, False
                ref = _import_alias_call_reference(node, bindings, symbol, origin_modules)
                if ref is not None:
                    return ref
                ref = _getattr_call_reference(node, bindings, symbol)
                if ref is not None:
                    return ref
                ref = _attrgetter_call_reference(node, bindings, symbol)
                if ref is not None:
                    return ref
                ref = _methodcaller_call_reference(node, bindings, symbol)
                if ref is not None:
                    return ref
                ref = _bound_attrgetter_call_reference(node, bindings, symbol)
                if ref is not None:
                    return ref
                ref = _bound_getter_result_call_reference(node, bindings, symbol)
                if ref is not None:
                    return ref
                if isinstance(node.func, ast.Call):
                    ref = _getattr_call_reference(node.func, bindings, symbol)
                    if ref is not None:
                        return ref
                    ref = _attrgetter_call_reference(node.func, bindings, symbol)
                    if ref is not None:
                        return ref
                    ref = _methodcaller_call_reference(node.func, bindings, symbol)
                    if ref is not None:
                        return ref
    if unreadable_hit is not None and unreadable_hit.hit:
        return False, False
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
    import_scan_unreadable = repo_map._UnreadablePathFlag()
    imports_complete, has_import_reference = _python_import_reference(
        repo_map_payload,
        symbol,
        deadline_monotonic,
        origin_files,
        unreadable_hit=import_scan_unreadable,
    )
    if import_scan_unreadable.hit and lookup_incomplete is not None:
        lookup_incomplete.append("unreadable_path")
    if not imports_complete and lookup_incomplete is not None:
        lookup_incomplete.append("test_reference_lookup_incomplete")
    return imports_complete, has_import_reference
