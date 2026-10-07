from __future__ import annotations

import argparse
import ast
import hashlib
import json
import textwrap
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / "src" / "tensor_grep"
SINK_ATTRS = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
UNKNOWN_PYTHON_ARGV = "<possible-python-c>"
# Exact forwarding boundaries only. Records include the call AST, the `**name` expression,
# and the owning function AST (which is the bounded producer/provenance fingerprint).
FORWARDER_ALLOWLIST: dict[tuple[str, str, str, str, str], str] = {
    (
        "cli/bootstrap.py:_streaming_passthrough_returncode:run_subprocess:b437e4570d25707596480c6cbc9c39d66191d5d1a3596ce6f0aa090291c210f1:0",
        "run_subprocess",
        "shim_kwargs",
        "b437e4570d25707596480c6cbc9c39d66191d5d1a3596ce6f0aa090291c210f1",
        "3890e2a9e09942d4c1f65eb02dae37e4485ca6c0b2f62ebe5a54702fa6b04286",
    ): "legacy subprocess compatibility call forwards only its constructed shim options",
    (
        "cli/bootstrap.py:_streaming_passthrough_returncode:run_subprocess:5cde2066bd09cb67c745c60f118a265f64eb474bf7fd5a4dc1ef1a0cf83fd330:0",
        "run_subprocess",
        "shim_kwargs",
        "5cde2066bd09cb67c745c60f118a265f64eb474bf7fd5a4dc1ef1a0cf83fd330",
        "3890e2a9e09942d4c1f65eb02dae37e4485ca6c0b2f62ebe5a54702fa6b04286",
    ): "legacy subprocess compatibility call forwards only its constructed shim options",
    (
        "cli/dogfood.py:run_dogfood_readiness:subprocess.Popen:e954fccb42ac247b758f13dff473f28117ca45dbbadbd5c3bcddf729a07ed643:0",
        "subprocess.Popen",
        "popen_kwargs",
        "e954fccb42ac247b758f13dff473f28117ca45dbbadbd5c3bcddf729a07ed643",
        "babba0ff9efe32801d2fcdbe16fe0bdfcfcdb73a42fbb856a14fc584886c7b90",
    ): "readiness child receives the locally built bounded Popen options",
    (
        "cli/process_containment.py:_spawn_posix:subprocess.Popen:e7084bed0981858e86d020949edb2393d9499e99e8a8678ee5c0dc35d3315292:0",
        "subprocess.Popen",
        "popen_kwargs",
        "e7084bed0981858e86d020949edb2393d9499e99e8a8678ee5c0dc35d3315292",
        "63ba53cd4fa9bedaed4a6859952e2c52fdc4b83bf9310b26fe8fe2cab15d302d",
    ): "POSIX containment primitive forwards options from its checked caller",
    (
        "cli/process_containment.py:_spawn_windows:subprocess.Popen:fd298f8fc8369cd525e0bac6fc0173a95c5b71ebd82d42f7620f38aba468cca9:0",
        "subprocess.Popen",
        "popen_kwargs",
        "fd298f8fc8369cd525e0bac6fc0173a95c5b71ebd82d42f7620f38aba468cca9",
        "8738fd491a3755df63fa84ec355ed46191cbfcf8259c211ca83de4a5cc476300",
    ): "Windows containment primitive forwards options from its checked caller",
    (
        "cli/session_daemon.py:_spawn_daemon_subprocess:subprocess.Popen:18836de9a392fbedcd6d1c9c81b077de877fed544733faa6e29002ba1c58f99f:0",
        "subprocess.Popen",
        "popen_kwargs",
        "18836de9a392fbedcd6d1c9c81b077de877fed544733faa6e29002ba1c58f99f",
        "677876295c273e09bf25288b00544a46a78023c1b64de7e987fff0cdfe4ce565",
    ): "daemon spawn uses its locally assembled launch options",
    (
        "cli/subprocess_policy.py:run_subprocess:subprocess.run:6eaec4d190ffafae3d3f611906e7558f07e7f694cb80feb84d63e8de95060f85:0",
        "subprocess.run",
        "kwargs",
        "6eaec4d190ffafae3d3f611906e7558f07e7f694cb80feb84d63e8de95060f85",
        "d856b8ac8957bd76b077abd059a0b2cbe4c53358497030cc2fe18d85ddc9558b",
    ): "central timeout wrapper forwards caller options without choosing decoding policy",
}


@dataclass(frozen=True)
class Sink:
    path: str
    line: int
    column: int
    function: str
    target: str
    text: str | None
    encoding: str | None
    errors: str | None
    universal_newlines: str | None
    dynamic_kwargs: tuple[str, ...]
    output_captured: bool
    generated_from: str | None = None
    fingerprint: str = ""
    producer_fingerprint: str = ""
    ordinal: int = 0
    positional_options: bool = False

    @property
    def identity(self) -> str:
        return f"{self.path}:{self.function}:{self.target}:{self.fingerprint}:{self.ordinal}"


def _dump(node: object) -> str:
    """Fingerprint parsed semantics without interpreter-specific AST formatting."""
    if isinstance(node, ast.AST):
        fields = (
            f"{name}={_dump(value)}"
            for name, value in sorted(ast.iter_fields(node))
            if not (name == "type_params" and isinstance(value, list) and not value)
        )
        return f"{type(node).__name__}({','.join(fields)})"
    if isinstance(node, list):
        return "[" + ",".join(_dump(value) for value in node) + "]"
    return repr(node)


def _fingerprint(node: ast.AST) -> str:
    return hashlib.sha256(_dump(node).encode("utf-8")).hexdigest()


def _literal_payload(node: ast.AST, values: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_payload(node.left, values), _literal_payload(node.right, values)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "strip" and not node.args:
            value = _literal_payload(node.func.value, values)
            return value.strip() if value is not None else None
        if node.func.attr == "dedent" and len(node.args) == 1:
            value = _literal_payload(node.args[0], values)
            return textwrap.dedent(value) if value is not None else None
    return None


def _target(node: ast.AST, env: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        target = env.get(node.id)
        if target == "ambiguous-subprocess":
            return "unresolved-subprocess-alias"
        return target if target and target != "unknown" else None
    if isinstance(node, ast.Attribute):
        if node.attr == "subprocess":
            return "module:subprocess"
        if (
            isinstance(node.value, ast.Name)
            and env.get(node.value.id) == "module:subprocess-policy"
        ):
            return "run_subprocess" if node.attr == "run_subprocess" else None
        if isinstance(node.value, ast.Name) and env.get(node.value.id) == "module:subprocess":
            return f"subprocess.{node.attr}" if node.attr in SINK_ATTRS else None
        if isinstance(node.value, ast.Name) and env.get(node.value.id) == "ambiguous-subprocess":
            return (
                "unresolved-subprocess-alias"
                if node.attr in SINK_ATTRS | {"run_subprocess"}
                else None
            )
        if isinstance(node.value, ast.Attribute) and node.value.attr == "subprocess":
            return f"injected.subprocess.{node.attr}" if node.attr in SINK_ATTRS else None
    return None


def _captures_output(keywords: dict[str | None, ast.AST]) -> bool:
    capture = keywords.get("capture_output")
    if isinstance(capture, ast.Constant) and capture.value is True:
        return True
    for name in ("stdout", "stderr"):
        value = keywords.get(name)
        if isinstance(value, ast.Attribute) and value.attr in {"PIPE", "STDOUT"}:
            return True
        if isinstance(value, ast.Name) and value.id in {"PIPE", "STDOUT"}:
            return True
        if isinstance(value, ast.Constant) and value.value == -1:
            return True
        if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
            if isinstance(value.operand, ast.Constant) and value.operand.value == 1:
                return True
    return False


class _Scanner:
    def __init__(self, path: str, *, generated_from: str | None = None) -> None:
        self.path = path
        self.generated_from = generated_from
        self.rows: list[Sink] = []
        self._generated_active: set[tuple[str, str]] = set()
        self.scope_fingerprints: dict[str, str] = {"<module>": ""}
        self._site_counts: dict[tuple[str, str, str], int] = {}

    def _append_sink(self, row: Sink) -> Sink:
        key = (row.function, row.target, row.fingerprint)
        row = Sink(**{**asdict(row), "ordinal": self._site_counts.get(key, 0)})
        self._site_counts[key] = row.ordinal + 1
        self.rows.append(row)
        return row

    def scan(self, tree: ast.Module, initial: dict[str, str] | None = None) -> list[Sink]:
        self._block(tree.body, dict(initial or {}), {}, "<module>")
        return self.rows

    @staticmethod
    def _assigned_names(nodes: list[ast.stmt]) -> set[str]:
        assigned: set[str] = set()

        def visit(node: ast.AST, *, root: bool = False) -> None:
            if not root and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    assigned.add(node.name)
                return
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                assigned.add(node.id)
            for child in ast.iter_child_nodes(node):
                visit(child)

        for node in nodes:
            visit(node, root=True)
        return assigned

    def _block(
        self,
        statements: list[ast.stmt],
        env: dict[str, str],
        values: dict[str, str],
        scope: str,
        prefixes: list[tuple[dict[str, str], dict[str, str]]] | None = None,
    ) -> None:
        for statement in statements:
            if prefixes is not None:
                prefixes.append((dict(env), dict(values)))
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._exprs(statement.decorator_list, env, values, scope)
                self._exprs(
                    statement.args.defaults + statement.args.kw_defaults, env, values, scope
                )
                local_env = dict(env)
                local_values = dict(values)
                local_names = self._assigned_names(statement.body)
                local_names.update(
                    arg.arg
                    for arg in (
                        *statement.args.posonlyargs,
                        *statement.args.args,
                        *statement.args.kwonlyargs,
                    )
                )
                if statement.args.vararg:
                    local_names.add(statement.args.vararg.arg)
                if statement.args.kwarg:
                    local_names.add(statement.args.kwarg.arg)
                for name in local_names:
                    local_env.pop(name, None)
                    local_values.pop(name, None)
                    local_values.pop(f"__argv__:{name}", None)
                child_scope = f"{scope}.{statement.name}" if scope != "<module>" else statement.name
                self.scope_fingerprints[child_scope] = _fingerprint(statement)
                self._block(statement.body, local_env, local_values, child_scope)
                env.pop(statement.name, None)
                values.pop(statement.name, None)
                continue
            if isinstance(statement, ast.ClassDef):
                self._exprs(statement.decorator_list + statement.bases, env, values, scope)
                child_scope = f"{scope}.{statement.name}" if scope != "<module>" else statement.name
                self._block(statement.body, dict(env), dict(values), child_scope)
                env.pop(statement.name, None)
                values.pop(statement.name, None)
                continue
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                if isinstance(statement, ast.Import):
                    for item in statement.names:
                        bound = item.asname or item.name.split(".")[0]
                        if item.name == "subprocess":
                            env[bound] = "module:subprocess"
                        elif item.name == "tensor_grep.cli.subprocess_policy" and item.asname:
                            env[bound] = "module:subprocess-policy"
                        else:
                            env.pop(bound, None)
                        values.pop(bound, None)
                elif statement.module == "subprocess":
                    for item in statement.names:
                        bound = item.asname or item.name
                        env[bound] = (
                            f"subprocess.{item.name}" if item.name in SINK_ATTRS else "unknown"
                        )
                        values.pop(bound, None)
                elif statement.module == "tensor_grep.cli.subprocess_policy":
                    for item in statement.names:
                        bound = item.asname or item.name
                        env[bound] = (
                            "run_subprocess" if item.name == "run_subprocess" else "unknown"
                        )
                        values.pop(bound, None)
                elif statement.module == "tensor_grep.cli.freshness_process":
                    for item in statement.names:
                        bound = item.asname or item.name
                        env[bound] = "capture_probe" if item.name == "capture_probe" else "unknown"
                        values.pop(bound, None)
                elif statement.module == "tensor_grep.cli":
                    for item in statement.names:
                        bound = item.asname or item.name
                        env[bound] = (
                            "module:subprocess-policy"
                            if item.name == "subprocess_policy"
                            else "unknown"
                        )
                        values.pop(bound, None)
                else:
                    for item in statement.names:
                        env.pop(item.asname or item.name, None)
                        values.pop(item.asname or item.name, None)
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                value = statement.value
                self._expr(value, env, values, scope)
                targets = (
                    statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                )
                alias = _target(value, env) if value is not None else None
                if isinstance(value, ast.Name):
                    alias = env.get(value.id)
                if alias == "unresolved-subprocess-alias":
                    alias = "ambiguous-subprocess"
                literal = _literal_payload(value, values) if value is not None else None
                argv_items = _argv_items(value, values)
                uncertain_argv = _possible_python_argv(value, values)
                argv_ref = (
                    values.get(f"__argv_ref__:{value.id}")
                    if isinstance(value, ast.Name)
                    else f"{statement.lineno}:{statement.col_offset}"
                )
                for target in targets:
                    if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                        _invalidate_argv(f"__argv__:{target.value.id}", values)
                    for name in self._target_names(target):
                        if alias is None:
                            env.pop(name, None)
                        else:
                            env[name] = alias
                        if literal is None:
                            values.pop(name, None)
                        else:
                            values[name] = literal
                        argv_key = f"__argv__:{name}"
                        previous_python = _raw_possible_python_argv(values.get(argv_key))
                        if argv_items is None and (
                            uncertain_argv or (previous_python and literal is None)
                        ):
                            values[argv_key] = UNKNOWN_PYTHON_ARGV
                        elif argv_items is None:
                            values.pop(argv_key, None)
                        else:
                            values[argv_key] = json.dumps(argv_items)
                        ref_key = f"__argv_ref__:{name}"
                        if argv_key in values and argv_ref is not None:
                            values[ref_key] = argv_ref
                        else:
                            values.pop(ref_key, None)
                continue
            if isinstance(statement, ast.AugAssign):
                self._expr(statement.value, env, values, scope)
                if isinstance(statement.target, ast.Name):
                    name = statement.target.id
                    previous = values.get(name)
                    addition_text = _literal_payload(statement.value, values)
                    if (
                        isinstance(statement.op, ast.Add)
                        and previous is not None
                        and addition_text is not None
                    ):
                        values[name] = previous + addition_text
                    else:
                        values.pop(name, None)
                if isinstance(statement.target, ast.Name) and isinstance(statement.op, ast.Add):
                    key = f"__argv__:{statement.target.id}"
                    current = _argv_items(statement.target, values)
                    addition = _argv_items(statement.value, values)
                    if current is not None and addition is not None:
                        _set_argv(key, values, json.dumps([*current, *addition]))
                    else:
                        _invalidate_argv(
                            key, values, _possible_python_argv(statement.value, values)
                        )
                continue
            if isinstance(statement, ast.If):
                self._expr(statement.test, env, values, scope)
                left_env, right_env = dict(env), dict(env)
                left_values, right_values = dict(values), dict(values)
                self._block(statement.body, left_env, left_values, scope, prefixes)
                self._block(statement.orelse, right_env, right_values, scope, prefixes)
                self._join(env, left_env, right_env)
                self._join(values, left_values, right_values)
                continue
            if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                self._expr(
                    statement.iter
                    if isinstance(statement, (ast.For, ast.AsyncFor))
                    else statement.test,
                    env,
                    values,
                    scope,
                )
                branch_env, branch_values = dict(env), dict(values)
                if isinstance(statement, (ast.For, ast.AsyncFor)):
                    for name in self._target_names(statement.target):
                        branch_env.pop(name, None)
                        branch_values.pop(name, None)
                self._block(statement.body, branch_env, branch_values, scope, prefixes)
                self._block(statement.orelse, branch_env, branch_values, scope, prefixes)
                self._join(env, env, branch_env)
                self._join(values, values, branch_values)
                continue
            if isinstance(statement, (ast.With, ast.AsyncWith)):
                for item in statement.items:
                    self._expr(item.context_expr, env, values, scope)
                self._block(statement.body, env, values, scope, prefixes)
                continue
            if isinstance(statement, ast.Try):
                base_env, base_values = dict(env), dict(values)
                normal_env, normal_values = dict(base_env), dict(base_values)
                try_prefixes = [(base_env, base_values)]
                self._block(statement.body, normal_env, normal_values, scope, try_prefixes)
                try_prefixes.append((dict(normal_env), dict(normal_values)))
                else_prefixes: list[tuple[dict[str, str], dict[str, str]]] = []
                self._block(statement.orelse, normal_env, normal_values, scope, else_prefixes)
                outcomes = [(normal_env, normal_values)]
                # Exceptions in else bypass this try's handlers, but still run finally
                # and can reach a surrounding try's handlers.
                exceptional_env, exceptional_values = self._joined_states(try_prefixes)
                for handler in statement.handlers:
                    handler_env, handler_values = dict(exceptional_env), dict(exceptional_values)
                    if handler.name:
                        handler_env.pop(handler.name, None)
                        handler_values.pop(handler.name, None)
                        handler_values.pop(f"__argv__:{handler.name}", None)
                    self._expr(handler.type, handler_env, handler_values, scope)
                    self._block(handler.body, handler_env, handler_values, scope, try_prefixes)
                    outcomes.append((handler_env, handler_values))
                # finally also runs after an unhandled exception at any earlier prefix.
                if statement.finalbody:
                    outcomes.extend(try_prefixes)
                    outcomes.extend(else_prefixes)
                joined_env, joined_values = self._joined_states(outcomes)
                env.clear()
                env.update(joined_env)
                values.clear()
                values.update(joined_values)
                # A finally block runs after either normal completion or an exception arm.
                self._block(statement.finalbody, env, values, scope, prefixes)
                if prefixes is not None:
                    prefixes.extend(try_prefixes)
                    prefixes.extend(else_prefixes)
                continue
            self._stmt_exprs(statement, env, values, scope)
        if prefixes is not None:
            prefixes.append((dict(env), dict(values)))

    @classmethod
    def _joined_states(
        cls, states: list[tuple[dict[str, str], dict[str, str]]]
    ) -> tuple[dict[str, str], dict[str, str]]:
        env, values = dict(states[0][0]), dict(states[0][1])
        for branch_env, branch_values in states[1:]:
            cls._join(env, env, branch_env)
            cls._join(values, values, branch_values)
        return env, values

    @staticmethod
    def _join(target: dict[str, str], left: dict[str, str], right: dict[str, str]) -> None:
        for key in set(target) | set(left) | set(right):
            if key.startswith("__argv__:"):
                left_items = _json_argv(left.get(key))
                right_items = _json_argv(right.get(key))
                if left_items is not None and left_items == right_items:
                    target[key] = json.dumps(left_items)
                elif any(_raw_possible_python_argv(branch.get(key)) for branch in (left, right)):
                    target[key] = UNKNOWN_PYTHON_ARGV
                else:
                    target.pop(key, None)
                continue
            if key in left and key in right and left[key] == right[key]:
                target[key] = left[key]
            elif any(
                value == "module:subprocess"
                or value == "module:subprocess-policy"
                or value == "run_subprocess"
                or value == "capture_probe"
                or value.startswith("subprocess.")
                or value.startswith("injected.subprocess.")
                or value == "ambiguous-subprocess"
                or value == "unresolved-subprocess-alias"
                for value in (left.get(key, ""), right.get(key, ""))
            ):
                target[key] = "ambiguous-subprocess"
            else:
                target.pop(key, None)

    @staticmethod
    def _target_names(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, (ast.Tuple, ast.List)):
            return [name for item in node.elts for name in _Scanner._target_names(item)]
        return []

    def _stmt_exprs(
        self, node: ast.stmt, env: dict[str, str], values: dict[str, str], scope: str
    ) -> None:
        for field, value in ast.iter_fields(node):
            if field in {"body", "orelse", "finalbody", "handlers", "type_ignores"}:
                continue
            if isinstance(value, ast.expr):
                self._expr(value, env, values, scope)
            elif isinstance(value, list):
                self._exprs([x for x in value if isinstance(x, ast.expr)], env, values, scope)

    def _exprs(
        self, nodes: list[ast.expr | None], env: dict[str, str], values: dict[str, str], scope: str
    ) -> None:
        for node in nodes:
            if node is not None:
                self._expr(node, env, values, scope)

    def _expr(self, node: ast.AST, env: dict[str, str], values: dict[str, str], scope: str) -> None:
        if node is None:
            return
        if isinstance(node, ast.Call):
            self._expr(node.func, env, values, scope)
            for arg in node.args:
                self._expr(arg, env, values, scope)
            for keyword in node.keywords:
                self._expr(keyword.value, env, values, scope)
            target = _target(node.func, env)
            if target is not None:
                self._record(node, target, env, values, scope)
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"append", "extend"}
                and isinstance(node.func.value, ast.Name)
            ):
                _update_argv_from_method(node, values)
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._expr(child, env, values, scope)

    def _record(
        self, node: ast.Call, target: str, env: dict[str, str], values: dict[str, str], scope: str
    ) -> None:
        if target == "unresolved-subprocess-alias":
            self._append_sink(
                Sink(
                    self.path,
                    node.lineno,
                    node.col_offset,
                    scope,
                    target,
                    None,
                    None,
                    None,
                    None,
                    (),
                    False,
                    self.generated_from,
                    _fingerprint(node),
                    self.scope_fingerprints.get(scope, ""),
                )
            )
            return
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
        text = ast.unparse(keywords["text"]) if "text" in keywords else None
        encoding = (
            ast.unparse(keywords["encoding"]).strip("'\"") if "encoding" in keywords else None
        )
        errors = ast.unparse(keywords["errors"]).strip("'\"") if "errors" in keywords else None
        universal = (
            ast.unparse(keywords["universal_newlines"])
            if "universal_newlines" in keywords
            else None
        )
        dynamic = tuple(sorted(ast.unparse(kw.value) for kw in node.keywords if kw.arg is None))
        api = target.rsplit(".", 1)[-1]
        implicit_text = api in {"getoutput", "getstatusoutput"}
        if implicit_text:
            text = "True"
        output_captured = (
            implicit_text or api in {"check_output", "capture_probe"} or _captures_output(keywords)
        )
        call_fingerprint = _fingerprint(node)
        recorded = self._append_sink(
            Sink(
                self.path,
                node.lineno,
                node.col_offset,
                scope,
                target,
                text,
                encoding,
                errors,
                universal,
                dynamic,
                output_captured,
                self.generated_from,
                call_fingerprint,
                self.scope_fingerprints.get(scope, ""),
                positional_options=len(node.args) > 1
                or any(isinstance(arg, ast.Starred) for arg in node.args),
            )
        )
        if target == "generated-python-unresolved":
            return
        argv = node.args[0] if node.args else keywords.get("args")
        argv_values = _argv_items(argv, values)
        if argv_values is None:
            if _possible_python_argv(argv, values):
                self._append_sink(
                    Sink(
                        self.path,
                        node.lineno,
                        node.col_offset,
                        scope,
                        "generated-python-unresolved",
                        None,
                        None,
                        None,
                        None,
                        (ast.unparse(argv),),
                        False,
                        recorded.identity,
                        call_fingerprint,
                        self.scope_fingerprints.get(scope, ""),
                    )
                )
            return
        if not _possible_python_executable(argv_values):
            return
        code_index = next((i for i, item in enumerate(argv_values) if item == "-c"), None)
        if code_index is None:
            return
        if any(item is None for item in argv_values[1:code_index]):
            self._append_sink(
                Sink(
                    self.path,
                    node.lineno,
                    node.col_offset,
                    scope,
                    "generated-python-unresolved",
                    None,
                    None,
                    None,
                    None,
                    ("unresolved Python option before -c",),
                    False,
                    recorded.identity,
                    call_fingerprint,
                    self.scope_fingerprints.get(scope, ""),
                )
            )
            return
        # Resolve a code string from the original args list when supported.
        code_ast: ast.AST | None = None
        code: str | None = (
            argv_values[code_index + 1] if code_index + 1 < len(argv_values) else None
        )
        if isinstance(argv, (ast.List, ast.Tuple)) and code_index + 1 < len(argv.elts):
            code_ast = argv.elts[code_index + 1]
            code = _literal_payload(code_ast, values)
        elif isinstance(argv, ast.Name):
            encoded = values.get(f"__argv__:{argv.id}")
            if encoded is not None:
                try:
                    code = json.loads(encoded)[code_index + 1]
                except (ValueError, IndexError, TypeError):
                    pass
        call_identity = f"{self.path}:{scope}:{target}:{call_fingerprint}:{recorded.ordinal}"
        caller = call_identity
        if code is None:
            self.rows.append(
                Sink(
                    self.path,
                    node.lineno,
                    node.col_offset,
                    scope,
                    "generated-python-unresolved",
                    None,
                    None,
                    None,
                    None,
                    (ast.unparse(code_ast) if code_ast else ast.unparse(argv),),
                    False,
                    caller,
                    _fingerprint(node),
                    self.scope_fingerprints.get(scope, ""),
                )
            )
            return
        generated_path = f"{self.path}::<-c:{scope}:{target}:{call_fingerprint}:{recorded.ordinal}>"
        marker = (generated_path, caller)
        if marker in self._generated_active:
            self.rows.append(
                Sink(
                    generated_path,
                    1,
                    0,
                    scope,
                    "generated-python-unresolved",
                    None,
                    None,
                    None,
                    None,
                    ("recursive generated source",),
                    False,
                    caller,
                    _fingerprint(node),
                    self.scope_fingerprints.get(scope, ""),
                )
            )
            return
        self._generated_active.add(marker)
        try:
            try:
                tree = ast.parse(code, filename=generated_path)
            except SyntaxError:
                self.rows.append(
                    Sink(
                        generated_path,
                        1,
                        0,
                        scope,
                        "generated-python-unresolved",
                        None,
                        None,
                        None,
                        None,
                        ("syntax-invalid generated source",),
                        False,
                        caller,
                        _fingerprint(node),
                        self.scope_fingerprints.get(scope, ""),
                    )
                )
            else:
                nested = _Scanner(generated_path, generated_from=caller)
                self.rows.extend(nested.scan(tree))
        finally:
            self._generated_active.remove(marker)


def _literal_argv(node: ast.AST, values: dict[str, str]) -> list[str | None] | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        result: list[str | None] = []
        for item in node.elts:
            literal = _literal_payload(item, values)
            result.append(literal)
        return result
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _argv_items(node.left, values)
        right = _argv_items(node.right, values)
        if left is not None and right is not None:
            return [*left, *right]
    return None


def _update_argv_from_method(node: ast.Call, values: dict[str, str]) -> None:
    assert isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
    name = node.func.value.id
    key = f"__argv__:{name}"
    raw = values.get(key)
    if raw is None:
        return
    items = _json_argv(raw)
    if items is None:
        _invalidate_argv(key, values)
        return
    if len(node.args) != 1:
        _invalidate_argv(key, values)
        return
    addition = _argv_items(node.args[0], values) if node.func.attr == "extend" else None
    if node.func.attr == "append":
        items.append(_literal_payload(node.args[0], values))
    elif addition is not None:
        items.extend(addition)
    else:
        _invalidate_argv(key, values, _possible_python_argv(node.args[0], values))
        return
    _set_argv(key, values, json.dumps(items))


def _set_argv(key: str, values: dict[str, str], raw: str | None) -> None:
    ref = values.get(key.replace("__argv__:", "__argv_ref__:", 1))
    keys = [key]
    if ref is not None:
        keys.extend(
            candidate.replace("__argv_ref__:", "__argv__:", 1)
            for candidate, candidate_ref in values.items()
            if candidate.startswith("__argv_ref__:") and candidate_ref == ref
        )
    for candidate in keys:
        if raw is None:
            values.pop(candidate, None)
        else:
            values[candidate] = raw


def _invalidate_argv(key: str, values: dict[str, str], possible_python: bool = False) -> None:
    possible_python = possible_python or _raw_possible_python_argv(values.get(key))
    _set_argv(key, values, UNKNOWN_PYTHON_ARGV if possible_python else None)


def _raw_possible_python_argv(raw: str | None) -> bool:
    items = _json_argv(raw)
    return raw == UNKNOWN_PYTHON_ARGV or (
        items is not None and _possible_python_executable(items) and "-c" in items
    )


def _possible_python_executable(items: list[str | None]) -> bool:
    if not items:
        return False
    if items[0] is None:
        # sys.executable and computed executable expressions remain conservatively possible.
        return True
    name = items[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
    for prefix in ("python", "pypy"):
        if name.startswith(prefix) and all(char in "0123456789.tw" for char in name[len(prefix) :]):
            return True
    return False


def _possible_python_argv(node: ast.AST | None, values: dict[str, str]) -> bool:
    if isinstance(node, ast.Name):
        return _raw_possible_python_argv(values.get(f"__argv__:{node.id}"))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _possible_python_argv(node.left, values) or _possible_python_argv(node.right, values)
    items = _argv_items(node, values)
    return items is not None and _possible_python_executable(items) and "-c" in items


def _argv_items(node: ast.AST | None, values: dict[str, str]) -> list[str | None] | None:
    if isinstance(node, (ast.List, ast.Tuple, ast.BinOp)):
        return _literal_argv(node, values)
    if isinstance(node, ast.Name):
        return _json_argv(values.get(f"__argv__:{node.id}"))
    return None


def _json_argv(raw: str | None) -> list[str | None] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) else None


def scan_source(source: str, path: str = "<fixture>") -> list[Sink]:
    tree = ast.parse(source, filename=path)
    return _Scanner(path).scan(tree)


def scan_tree(root: Path = PRODUCTION) -> list[Sink]:
    rows: list[Sink] = []
    for source_path in sorted(root.rglob("*.py")):
        relative = source_path.relative_to(root).as_posix()
        try:
            rows.extend(scan_source(source_path.read_text(encoding="utf-8"), relative))
        except (OSError, SyntaxError, UnicodeError) as exc:
            raise RuntimeError(f"cannot census {relative}: {exc}") from exc
    unique: list[Sink] = []
    seen: set[tuple[str, int, int, str, str]] = set()
    for row in rows:
        key = row.path, row.line, row.column, row.target, row.generated_from or ""
        if key not in seen:
            unique.append(row)
            seen.add(key)
    return unique


def forwarder_key(row: Sink) -> tuple[str, str, str, str, str] | None:
    if not row.dynamic_kwargs:
        return None
    return (
        row.identity,
        row.target,
        row.dynamic_kwargs[0],
        row.fingerprint,
        row.producer_fingerprint,
    )


def _uses_text_decoding(row: Sink) -> bool:
    return (
        row.text not in (None, "False")
        or row.universal_newlines not in (None, "False")
        or row.encoding not in (None, "None")
        or row.errors not in (None, "None")
    )


def violations(
    rows: Iterable[Sink],
    *,
    production: bool = False,
    forwarder_allowlist: dict[tuple[str, str, str, str, str], str] | None = None,
) -> list[str]:
    rows = list(rows)
    allowlist = FORWARDER_ALLOWLIST if forwarder_allowlist is None else forwarder_allowlist
    problems: list[str] = []
    consumed: dict[tuple[str, str, str, str, str], int] = {}
    for row in rows:
        policy, _rationale = policy_for(row, allowlist)
        if row.positional_options:
            problems.append(
                f"{row.identity}: subprocess positional options or *args are unsupported; use explicit keyword options"
            )
        if production and policy in {"unresolved-dynamic", "unresolved-alias", "unclassified"}:
            problems.append(f"{row.identity}: missing output-policy classification ({policy})")
        if row.target == "generated-python-unresolved":
            problems.append(f"{row.identity}: generated -c source is unresolved or invalid")
            continue
        if row.target == "unresolved-subprocess-alias":
            problems.append(f"{row.identity}: subprocess callable alias has ambiguous rebinding")
            continue
        if row.dynamic_kwargs:
            for expression in row.dynamic_kwargs:
                key = (
                    row.identity,
                    row.target,
                    expression,
                    row.fingerprint,
                    row.producer_fingerprint,
                )
                if key in allowlist:
                    consumed[key] = consumed.get(key, 0) + 1
                else:
                    problems.append(
                        f"{row.identity}: unresolved subprocess **kwargs {expression!r} ({row.fingerprint}; producer {row.producer_fingerprint})"
                    )
        if _uses_text_decoding(row) and row.encoding != "utf-8":
            problems.append(
                f"{row.identity}: text/universal_newlines/encoding requires explicit UTF-8 encoding"
            )
    if production:
        for key, count in consumed.items():
            if count != 1:
                problems.append(
                    f"forwarder exception consumed {count} times, expected once: {key[0]}"
                )
        for key in allowlist.keys() - consumed.keys():
            problems.append(f"unused exact forwarder exception: {key[0]}")
    return problems


FILESYSTEM_PATH_SITES = {
    ("backends/ripgrep_backend.py", "RipgrepBackend._search_files_with_matches"),
    ("backends/ripgrep_backend.py", "RipgrepBackend._search_counts"),
    ("cli/checkpoint_store.py", "_detect_checkpoint_scope"),
    ("cli/checkpoint_store.py", "_git_snapshot_entries"),
    ("cli/codemap.py", "_tracked_file_set"),
    ("cli/evidence_receipt.py", "_repo_revision_identity_excluding"),
    ("cli/diff_impact.py", "_git_toplevel"),
}
MACHINE_PROTOCOL_SITES = {
    ("backends/ast_wrapper_backend.py", "_is_ast_grep_sg_binary"),
    ("backends/ast_wrapper_backend.py", "AstGrepWrapperBackend._run_ast_grep_command"),
    ("cli/agent_capsule.py", "_run_agent_gpu_json_command"),
    ("cli/audit_manifest.py", "_resolve_git_ref_commit_sha"),
    ("cli/dogfood.py", "_derive_readiness_timeout_s"),
    ("cli/doctor_report.py", "_doctor_rust_binary_version"),
    ("cli/doctor_report.py", "_doctor_tg_candidate_version"),
    ("cli/doctor_report.py", "_doctor_gpu_search_runtime_probe"),
    ("cli/evidence_receipt.py", "_repo_revision_identity"),
    ("cli/main.py", "_version"),
    ("cli/main.py", "_verify_installed_version"),
    ("cli/main.py", "main_entry"),
    ("cli/mcp_rewrite_tools.py", "_run_rewrite_subprocess"),
    ("cli/native_frontdoor.py", "_candidate_versions_from_pip_index"),
    ("cli/native_frontdoor.py", "_candidate_versions_from_pypi_indices"),
    ("cli/native_frontdoor.py", "_verify_target_python_tensor_grep_version"),
    ("cli/runtime_paths.py", "_native_tg_version"),
    ("cli/session_daemon.py", "_restrict_windows_file_to_current_user"),
    ("cli/windows_launcher.py", "_windows_python_scripts_tensor_grep_package_version"),
    ("cli/windows_launcher.py", "_version"),
}
TOLERANT_GIT_STATUS_SITES = {
    ("cli/dogfood.py", "_build_release_docs_worktree_status"),
}
STREAM_FORWARDERS = {
    ("cli/bootstrap.py", "_popen_child"),
    ("cli/diff_impact_git.py", "spawn_git"),
    ("cli/main.py", "_delegate_to_native_tg_search"),
    ("cli/main.py", "calibrate"),
    ("cli/main.py", "worker"),
}


def policy_for(
    row: Sink,
    forwarder_allowlist: dict[tuple[str, str, str, str, str], str] | None = None,
) -> tuple[str, str]:
    allowlist = FORWARDER_ALLOWLIST if forwarder_allowlist is None else forwarder_allowlist
    if row.positional_options:
        return (
            "unclassified",
            "Subprocess options must be explicit keywords; positional options and *args can hide capture or decoding.",
        )
    if row.target == "unresolved-subprocess-alias":
        return (
            "unresolved-alias",
            "A branch or loop rebind makes a known subprocess callable ambiguous.",
        )
    if row.dynamic_kwargs:
        expression = row.dynamic_kwargs[0]
        key = (row.identity, row.target, expression, row.fingerprint, row.producer_fingerprint)
        rationale = allowlist.get(key)
        if rationale is None:
            return (
                "unresolved-dynamic",
                "Dynamic subprocess keyword expansion must be resolved or fingerprint-allowlisted.",
            )
        return "forwarder", rationale
    policy_path = row.path.split("::<-c:", 1)[0]
    site = (policy_path, row.function)
    decoding_enabled = _uses_text_decoding(row)
    if site in STREAM_FORWARDERS:
        return (
            "stream-forwarder",
            "The wrapper streams or delegates child IO; the caller owns any decoding of captured streams.",
        )
    if not row.output_captured:
        return (
            "no-capture",
            "The call does not capture stdout/stderr; no child output is decoded at this sink.",
        )
    if site in FILESYSTEM_PATH_SITES:
        if row.errors == "surrogateescape":
            if not decoding_enabled or row.encoding != "utf-8":
                return (
                    "unclassified",
                    "The Git root path site must retain explicit UTF-8 with surrogateescape.",
                )
            return (
                "filesystem-path-surrogateescape",
                "Git's root path is decoded as UTF-8 with surrogateescape so POSIX filename bytes retain their identity.",
            )
        if decoding_enabled:
            return "unclassified", "Filesystem path records must remain bytes until os.fsdecode."
        if site == ("cli/diff_impact.py", "_git_toplevel"):
            return (
                "filesystem-path-bytes",
                "Git root stdout remains bytes; remove exactly its terminal LF, then apply os.fsdecode before constructing the root Path.",
            )
        return (
            "filesystem-path-bytes",
            "NUL/path records stay bytes through framing and each filename is decoded with os.fsdecode (surrogateescape on POSIX).",
        )
    if site in MACHINE_PROTOCOL_SITES:
        if site == ("cli/native_frontdoor.py", "_candidate_versions_from_pip_index"):
            if decoding_enabled:
                return (
                    "unclassified",
                    "Pip-index version streams must remain bytes until strict decoding.",
                )
            return (
                "strict-version-streams",
                "Both stdout and stderr feed version parsing; both use strict UTF-8 and malformed output makes the probe unknown.",
            )
        if not decoding_enabled:
            if site == ("cli/mcp_rewrite_tools.py", "_run_rewrite_subprocess"):
                return (
                    "protocol-or-preview-bytes",
                    "JSON/index callers decode stdout strictly, diff previews use replacement, and stderr remains diagnostic; valid partial-result stdout survives nonzero exits.",
                )
            return (
                "strict-protocol",
                "Stdout remains bytes until strict UTF-8 protocol decoding; stderr is separately replacement-decoded for diagnostics.",
            )
        return (
            "unclassified",
            "Machine protocol streams must remain bytes until the consumer decodes them explicitly.",
        )
    if site in TOLERANT_GIT_STATUS_SITES:
        if decoding_enabled and row.encoding == "utf-8" and row.errors == "replace":
            return (
                "git-status-tolerant-text",
                "Git porcelain quotes unusual path bytes; replacement is retained for this existing status summary's text presentation.",
            )
        return (
            "unclassified",
            "Release-doc Git status is pinned to explicit UTF-8 replacement text.",
        )
    if decoding_enabled and row.encoding == "utf-8" and row.errors == "replace":
        return (
            "diagnostic-utf8-replace",
            "Captured human-facing diagnostics use explicit UTF-8 with replacement so malformed bytes remain reportable.",
        )
    if decoding_enabled and row.encoding == "utf-8":
        return (
            "unclassified",
            "Strict subprocess text decoding can lose captured streams on Windows; capture bytes and decode in the consumer.",
        )
    if not decoding_enabled:
        return (
            "bytes-protocol",
            "Captured output remains bytes until the consumer applies its protocol or diagnostic decoder.",
        )
    return (
        "unclassified",
        "No output policy was inferred; add an explicit reviewed callsite classification.",
    )


def _manifest_records(entries: object, fields: set[str]) -> dict[str, str]:
    if not isinstance(entries, list):
        raise ValueError("inventory must be a list of callsite records")
    records: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError("inventory callsite record has missing or unexpected fields")
        identity = entry["identity"]
        if not isinstance(identity, str) or not identity:
            raise ValueError("inventory identity must be a nonempty string")
        if identity in records:
            raise ValueError(f"duplicate inventory identity: {identity}")
        if type(entry["line"]) is not int or entry["line"] < 1:
            raise ValueError(f"invalid inventory line: {identity}")
        if type(entry["column"]) is not int or entry["column"] < 0:
            raise ValueError(f"invalid inventory column: {identity}")
        # JSON comparison preserves types: Python dictionary equality equates True and 1.
        records[identity] = json.dumps(
            {key: value for key, value in entry.items() if key not in {"line", "column"}},
            sort_keys=True,
        )
    return records


def check_repository(rows: list[Sink]) -> list[str]:
    problems = violations(rows, production=True)
    manifest_path = ROOT / "docs" / "subprocess-output-policy-inventory.json"
    actual_entries = inventory(rows)
    fields = set(actual_entries[0]) if actual_entries else set()
    try:
        actual_records = _manifest_records(actual_entries, fields)
    except ValueError as exc:
        problems.append(f"invalid scanned subprocess population manifest: {exc}")
        actual_records = {}
    try:
        expected_entries = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_records = _manifest_records(expected_entries, fields)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        problems.append(f"cannot read committed subprocess population manifest: {exc}")
        expected_records = {}
    if actual_records != expected_records:
        missing = sorted(expected_records.keys() - actual_records.keys())
        added = sorted(actual_records.keys() - expected_records.keys())
        changed = sorted(
            identity
            for identity in actual_records.keys() & expected_records.keys()
            if actual_records[identity] != expected_records[identity]
        )
        problems.append(
            f"production callsite identity/fingerprint manifest changed; missing={missing}; added={added}; changed={changed}"
        )
    if len(rows) != 73:
        problems.append(
            f"production output-capable sink population changed: expected 73, found {len(rows)}"
        )
    generated = [row for row in rows if row.generated_from is not None]
    if len(generated) != 12:
        problems.append(
            f"embedded subprocess population changed: expected 12, found {len(generated)}"
        )
    expected_launches = {
        "cli/main.py::<-c:": 10,
        "cli/windows_launcher.py::<-c:": 2,
    }
    for launch, expected in expected_launches.items():
        actual = sum(row.path.startswith(launch) for row in generated)
        if actual != expected:
            problems.append(
                f"embedded subprocess population for {launch} changed: expected {expected}, found {actual}"
            )
    return problems


def inventory(rows: Iterable[Sink]) -> list[dict[str, object]]:
    result = []
    for row in rows:
        policy, rationale = policy_for(row)
        result.append({
            "identity": row.identity,
            "path": row.path,
            "function": row.function,
            "line": row.line,
            "column": row.column,
            "target": row.target,
            "policy": policy,
            "rationale": rationale,
            "text": row.text,
            "encoding": row.encoding,
            "errors": row.errors,
            "universal_newlines": row.universal_newlines,
            "dynamic_kwargs": list(row.dynamic_kwargs),
            "output_captured": row.output_captured,
            "positional_options": row.positional_options,
            "generated_from": row.generated_from,
            "call_fingerprint": row.fingerprint,
            "producer_fingerprint": row.producer_fingerprint,
        })
    return result


def write_inventory(rows: list[Sink], root: Path) -> None:
    entries = inventory(rows)
    json_path = root / "docs" / "subprocess-output-policy-inventory.json"
    markdown_path = root / "docs" / "subprocess-output-policy-inventory.md"
    json_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# Subprocess output policy inventory",
        "",
        f"Generated by `scripts/check_subprocess_decoding.py`; {len(entries)} unique production callsites.",
        "Each row has a stable source identity and the source AST call fingerprint; embedded `python -c` calls carry their launch site in `generated_from`.",
        "",
        "| Callsite | Policy | Rationale |",
        "| --- | --- | --- |",
    ]
    for entry in entries:
        callsite = str(entry["identity"]).replace("|", "\\|")
        rationale = str(entry["rationale"]).replace("|", "\\|")
        lines.append(f"| `{callsite}` | `{entry['policy']}` | {rationale} |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Census subprocess output decoding contracts.")
    parser.add_argument("--root", type=Path, default=PRODUCTION)
    parser.add_argument("--write-inventory", action="store_true")
    args = parser.parse_args()
    rows = scan_tree(args.root)
    if args.write_inventory:
        write_inventory(rows, ROOT)
    problems = check_repository(rows)
    print(
        f"subprocess sinks: {len(rows)}; text decoding: {sum(_uses_text_decoding(row) for row in rows)}; generated: {sum(row.generated_from is not None for row in rows)}"
    )
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
