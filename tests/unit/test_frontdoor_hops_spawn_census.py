"""MECHANICAL census: no spawn of the native ``tg`` skips the ``TG_FRONTDOOR_HOPS`` stamp.

PR #1208's first cut listed the spawn sites by hand and missed four (``search --type-list``,
``--pcre2-version``, and the two ``--version`` probes). A hand-maintained list is what missed
them, so this walks the AST of every module under ``src/tensor_grep`` and the text of every Rust
spawn of Python.

Python rule, per function (qualified ``Class.method`` / ``outer.inner`` names):

* A function is NATIVE-CAPABLE when it references any identifier / attribute / string constant in
  ``_NATIVE_TOKENS`` (it resolves, receives, or is handed the native binary).
* A function SPAWNS when it calls a primitive in ``_SPAWN_NAMES``.
* RULE 1: a function that is native-capable AND spawns must be STAMPED (it references one of
  ``_STAMP_TOKENS``) or sit on ``_EXEMPT`` with a written reason.
* RULE 2: in a STAMPED function every spawn primitive must pass ``env=`` (or the function stamps
  ``os.environ`` through ``stamp_bootstrap_hop_or_refuse``).
* RULE 3: ``_EXEMPT`` may not rot: every entry must still be native-capable + spawning +
  unstamped (otherwise it is stale and must be deleted).

Known limit, stated rather than hidden: a spawn funnel that only receives an argv list (no native
token in its own body) is covered by Rule 2 only if it is stamped; the funnels that exist today
(``_run_rewrite_subprocess``, ``_run_agent_gpu_json_command``, ``_delegate_to_native_tg_search``)
are stamped, and their stamp is asserted by ``test_frontdoor_hops_spawn_sites.py`` behaviourally.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "tensor_grep"

_NATIVE_TOKENS = {
    "resolve_native_tg_binary",
    "native_tg_binary",
    "native_binary",
    "native_tg",
    "_resolve_native_tg_binary_for_mcp",
    "TG_NATIVE_TG_BINARY",
    "TG_MCP_TG_BINARY",
    "native_stdio_path",
}
#: String constants that mark "this spawn is probing a tg-like binary" (argv carries a candidate path).
_PROBE_FLAGS = {"--version", "--pcre2-version", "--type-list"}
_STAMP_TOKENS = {
    "child_env_or_refusal",
    "next_hop_env",
    "probe_env",
}
_SPAWN_NAMES = {
    "run",
    "Popen",
    "check_output",
    "check_call",
    "call",
    "run_subprocess",
    "_popen_child",
}
_SUBPROCESS_ATTRS = {"run", "Popen", "check_output", "check_call", "call"}

#: (module path under src/tensor_grep, qualified function) -> why this native-capable spawn is
#: allowed to be unstamped. Keep each reason specific; a vague one is a hole.
_EXEMPT: dict[tuple[str, str], str] = {
    ("cli/dogfood_features.py", "main"): (
        "Spawns only isolated Python -m tensor_grep.cli.dogfood_inventory, which imports "
        "schemas and emits their inventory without invoking either CLI door. Feature checks "
        "delegate to the independently stamped _run funnel."
    ),
    ("cli/dogfood_unified.py", "run_unified_dogfood"): (
        "Spawns only isolated Python -m tensor_grep.cli.dogfood_features, a packaged test "
        "orchestrator rather than a front door. Its tg children are stamped at _run; counting "
        "this orchestration process as a door would consume the legitimate routing budget."
    ),
    ("backends/ast_wrapper_backend.py", "_is_ast_grep_sg_binary"): (
        "runs `<ast-grep candidate> --version` to recognise the ast-grep `sg` binary; the "
        "argv[0] is an ast-grep install, never tensor-grep, so it cannot re-enter either door."
    ),
    ("cli/lsp_provider_setup.py", "_ensure_csharp_ls"): (
        "runs `dotnet --version` / `csharp-ls --version` for LSP provider setup; neither is "
        "tensor-grep, so the call cannot re-enter either door."
    ),
}


def _fixed_isolated_module_spawns(source: str, function_name: str, module_name: str) -> bool:
    tree = ast.parse(source)
    function = next(node for name, node in _functions(tree) if name == function_name)
    assignments = {
        target.id: node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    calls = _own_spawn_calls(function)
    if len(calls) != 1 or not calls[0].args:
        return False
    argv = calls[0].args[0]
    if isinstance(argv, ast.Name):
        argv = assignments.get(argv.id)
    return (
        isinstance(argv, ast.List)
        and len(argv.elts) >= 4
        and all(
            isinstance(node, ast.Constant) and node.value == value
            for node, value in zip(argv.elts[1:4], ["-I", "-m", module_name], strict=True)
        )
    )


def test_dogfood_non_frontdoor_exemptions_pin_the_actual_isolated_module() -> None:
    for file, function, module in [
        ("cli/dogfood_features.py", "main", "tensor_grep.cli.dogfood_inventory"),
        ("cli/dogfood_unified.py", "run_unified_dogfood", "tensor_grep.cli.dogfood_features"),
    ]:
        assert _fixed_isolated_module_spawns(
            (SRC / file).read_text(encoding="utf-8"), function, module
        )
    safe = "def example():\n    subprocess.run([python, '-I', '-m', 'inventory'])\n"
    assert _fixed_isolated_module_spawns(safe, "example", "inventory")
    assert not _fixed_isolated_module_spawns(
        safe.replace("'inventory'", "'tensor_grep'"), "example", "inventory"
    )
    assert not _fixed_isolated_module_spawns(safe.replace("'-I'", "'-c'"), "example", "inventory")


def _tokens(node: ast.AST) -> set[str]:
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
        elif isinstance(child, ast.arg):
            found.add(child.arg)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            if child.value in _NATIVE_TOKENS or child.value in _PROBE_FLAGS:
                found.add(child.value)
    return found


def _is_spawn(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in {"run_subprocess", "_popen_child", "Popen"}
    if isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_ATTRS:
        chain: list[str] = []
        base: ast.AST = func.value
        while isinstance(base, ast.Attribute):
            chain.append(base.attr)
            base = base.value
        if isinstance(base, ast.Name):
            chain.append(base.id)
        return "subprocess" in chain
    return isinstance(func, ast.Attribute) and func.attr in {"run_subprocess", "_popen_child"}


def _functions(tree: ast.Module):
    """Yield (qualified_name, node) for every def, innermost-first ownership of its own body."""

    def walk(node: ast.AST, prefix: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    yield name, child
                yield from walk(child, name + ".")
            else:
                yield from walk(child, prefix)

    yield from walk(tree, "")


def _own_spawn_calls(func: ast.AST) -> list[ast.Call]:
    """Spawn calls lexically inside ``func`` but not inside a nested def (those are their own)."""
    calls: list[ast.Call] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Call) and _is_spawn(child):
                calls.append(child)
            walk(child)

    walk(func)
    return calls


def _census() -> list[dict]:
    rows: list[dict] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for qualname, func in _functions(tree):
            calls = _own_spawn_calls(func)
            if not calls:
                continue
            tokens = _tokens(func)
            rows.append({
                "module": rel,
                "func": qualname,
                "calls": calls,
                "native": bool(tokens & (_NATIVE_TOKENS | _PROBE_FLAGS)),
                "stamped": bool(tokens & _STAMP_TOKENS),
            })
    return rows


def test_census_is_not_vacuous() -> None:
    rows = _census()
    assert len(rows) > 25, "the walk saw almost no spawn sites: the instrument is blind"
    native_stamped = {(r["module"], r["func"]) for r in rows if r["native"] and r["stamped"]}
    # Positive controls: the sites this PR fixed MUST be seen as native-capable AND stamped.
    for expected in (
        ("cli/main.py", "_delegate_to_native_tg_search"),
        ("cli/main.py", "_run_rg_compatible_info_action"),
        ("cli/main.py", "calibrate"),
        ("cli/main.py", "worker"),
        ("cli/main.py", "main_entry"),
        ("cli/runtime_paths.py", "_native_tg_version"),
        ("cli/doctor_report.py", "_doctor_rust_binary_version"),
    ):
        assert expected in native_stamped or any(
            r["module"] == expected[0] and r["func"] == expected[1] and r["stamped"] for r in rows
        ), f"{expected} not recognised as a stamped spawn site"


def test_every_native_capable_spawn_is_stamped_or_exempt() -> None:
    offenders = [
        f"{r['module']}::{r['func']}"
        for r in _census()
        if r["native"] and not r["stamped"] and (r["module"], r["func"]) not in _EXEMPT
    ]
    assert not offenders, (
        "native-capable subprocess spawn without a TG_FRONTDOOR_HOPS stamp "
        "(use frontdoor_hops.child_env_or_refusal()/probe_env() and pass env=, or add a "
        f"reasoned _EXEMPT entry): {offenders}"
    )


def test_stamped_spawns_actually_pass_the_stamped_env() -> None:
    offenders = []
    for r in _census():
        if not r["stamped"]:
            continue
        for call in r["calls"]:
            if not any(kw.arg == "env" for kw in call.keywords):
                offenders.append(f"{r['module']}::{r['func']} line {call.lineno}")
    assert not offenders, f"stamp computed but spawn does not pass env=: {offenders}"


def test_exempt_entries_are_not_stale() -> None:
    live = {(r["module"], r["func"]) for r in _census() if r["native"] and not r["stamped"]}
    stale = sorted(set(_EXEMPT) - live)
    assert not stale, f"stale _EXEMPT entries (delete them): {stale}"
    assert all(len(reason) > 30 for reason in _EXEMPT.values())


# ---------------------------------------------------------------------------
# Rust: every Python spawn is stamped by the ONE configure function, which refuses before spawn
# ---------------------------------------------------------------------------

RUST_SRC = REPO_ROOT / "rust_core" / "src"


def _rust_fn_bodies(text: str) -> dict[str, str]:
    bodies: dict[str, str] = {}
    for match in re.finditer(r"^(?:pub )?fn (\w+)", text, re.MULTILINE):
        start = match.end()
        nxt = re.search(r"^(?:pub )?fn \w+", text[start:], re.MULTILINE)
        bodies[match.group(1)] = text[start : start + nxt.start()] if nxt else text[start:]
    return bodies


def test_every_rust_python_spawn_is_stamped_before_spawning() -> None:
    offenders: list[str] = []
    seen_spawns = 0
    for path in sorted(RUST_SRC.rglob("*.rs")):
        if path.name.endswith("_tests_h3.rs"):
            continue
        text = path.read_text(encoding="utf-8")
        # Cut off in-file unit tests: they call resolve_python_command_for_context directly.
        text = text.split("#[cfg(test)]", 1)[0]
        for name, body in _rust_fn_bodies(text).items():
            if "command_for_executable(&python)" not in body:
                continue
            seen_spawns += 1
            spawn_at = body.index("command_for_executable(&python)")
            configure = "configure_python_child_environment(&mut child)?;"
            if configure not in body or body.index(configure) < spawn_at:
                offenders.append(
                    f"{path.name}::{name}: no `{configure}` after building the Command"
                )
            else:
                spawn_idx = body.find(".spawn(")
                if spawn_idx != -1 and body.index(configure) > spawn_idx:
                    offenders.append(f"{path.name}::{name}: stamped after spawn")
    assert seen_spawns >= 3, "Rust census found fewer Python spawns than the 3 known: blind"
    assert not offenders, offenders

    sidecar = (RUST_SRC / "python_sidecar.rs").read_text(encoding="utf-8")
    body = _rust_fn_bodies(sidecar)["configure_python_child_environment"]
    assert "stamp_python_child(command)?" in body
    assert body.index("stamp_python_child") < body.index("configure_python_module_path")


# ---------------------------------------------------------------------------
# No process-global environment writes in the hop plumbing (race: two threads)
# ---------------------------------------------------------------------------

_ENVIRON_MUTATORS = {"update", "pop", "setdefault", "clear", "popitem"}


def _environ_writes(node: ast.AST) -> list[int]:
    lines: list[int] = []

    def is_environ(expr: ast.AST) -> bool:
        return isinstance(expr, ast.Attribute) and expr.attr == "environ"

    for child in ast.walk(node):
        if isinstance(child, (ast.Assign, ast.AugAssign, ast.Delete)):
            targets = child.targets if not isinstance(child, ast.AugAssign) else [child.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and is_environ(target.value):
                    lines.append(child.lineno)
        elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr in {"putenv", "unsetenv"}:
                lines.append(child.lineno)
            elif child.func.attr in _ENVIRON_MUTATORS and is_environ(child.func.value):
                lines.append(child.lineno)
    return lines


def test_hop_plumbing_never_writes_os_environ() -> None:
    offenders: list[str] = []
    hops = ast.parse((SRC / "cli" / "frontdoor_hops.py").read_text(encoding="utf-8"))
    offenders += [f"frontdoor_hops.py:{n}" for n in _environ_writes(hops)]
    targets = {
        "cli/bootstrap.py": {
            "_popen_child",
            "_streaming_passthrough_returncode",
            "_run_native_tg_command",
        },
        "cli/bootstrap_native_argv.py": {"run_native_tg_search"},
    }
    for rel, names in targets.items():
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        found = {qual for qual, _ in _functions(tree)}
        assert names <= found, f"census target vanished: {names - found}"
        for qual, func in _functions(tree):
            if qual in names:
                offenders += [f"{rel}::{qual}:{n}" for n in _environ_writes(func)]
    assert not offenders, f"os.environ/putenv writes in the hop plumbing (racy): {offenders}"


def test_environ_write_detector_can_fail() -> None:
    sample = ast.parse(
        "import os\nos.environ['X'] = '1'\nos.environ.pop('X')\nos.putenv('X', '1')\n"
    )
    assert len(_environ_writes(sample)) == 3


def test_bootstrap_passthrough_callers_pass_the_stamped_env() -> None:
    for rel, name in (
        ("cli/bootstrap.py", "_run_native_tg_command"),
        ("cli/bootstrap_native_argv.py", "run_native_tg_search"),
    ):
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        func = dict(_functions(tree))[name]
        calls = [
            c
            for c in ast.walk(func)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == "_streaming_passthrough_returncode"
        ]
        assert calls, f"{name} no longer calls _streaming_passthrough_returncode"
        assert all(any(k.arg == "env" for k in c.keywords) for c in calls), name
