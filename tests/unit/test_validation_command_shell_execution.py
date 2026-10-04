"""Validation commands are pasted into an UNKNOWN shell, so they are tested by EXECUTION.

A path made only of the conservative inert character set is emitted bare and must reach bash,
PowerShell and cmd byte-for-byte; any other path must never be interpolated into a command
string at all (fail closed), with the raw path disclosed in a separate field.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map

_PROBE = "import sys, json\nprint(json.dumps(sys.argv[1:]))\n"

SAFE_NAMES = [
    "test_plain.py",
    "test_a-b_c.py",
    "test_a+b@c=d,e:f.py",
]
# `$(...)` / backtick / `%VAR%` / `!` / `;` / `&` / `|` / quotes / space / glob / non-ASCII.
UNSAFE_NAMES = [
    "test_$(Write-Output PWN).py",
    "test_`whoami`.py",
    "test_%OS%.py",
    "test_bang!.py",
    "test_a;b.py",
    "test_a&b.py",
    "test_a|b.py",
    'test_a"b.py',
    "test_a'b.py",
    "test_a b.py",
    "test_[ab].py",
    "test_café.py",
    "test_$HOME.py",
]


def _commands_and_plan(project: Path, name: str) -> tuple[list[str], dict]:
    test_path = project / "tests" / name
    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [str(test_path)],
        repo_root=project,
        primary_test=str(test_path),
        primary_symbol={"name": "x"},
    )
    assert all("command" in step for step in plan), plan  # consumers always see a command
    return [str(step["command"]) for step in plan], alignment


_PREFIXES = ("uv run pytest", "cargo", "npx", "node")


def _split_prefix(cmd: str) -> tuple[str, str]:
    for prefix in _PREFIXES:
        if cmd.startswith(prefix):
            return prefix, cmd[len(prefix) :]
    raise AssertionError(cmd)


def _shell_runners(tmp_path: Path):
    script = tmp_path / "probe.py"
    script.write_text(_PROBE, encoding="utf-8")
    env = {**os.environ, "PROBE_PY": sys.executable, "PROBE_SCRIPT": str(script)}

    def bash(cmd: str):
        return subprocess.run(
            ["bash", "-c", _split_prefix(cmd)[1].join(['"$PROBE_PY" "$PROBE_SCRIPT"', ""])],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    def pwsh(cmd: str):
        swapped = "& $env:PROBE_PY $env:PROBE_SCRIPT" + _split_prefix(cmd)[1]
        return subprocess.run(
            ["pwsh", "-NoProfile", "-Command", swapped],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    def cmd_exe(cmd: str):
        swapped = '"%PROBE_PY%" "%PROBE_SCRIPT%"' + _split_prefix(cmd)[1]
        return subprocess.run(
            f'cmd /c "{swapped}"',
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    runners = []
    if shutil.which("bash"):
        runners.append(("bash", bash))
    if shutil.which("pwsh"):
        runners.append(("pwsh", pwsh))
    if os.name == "nt" and shutil.which("cmd"):
        runners.append(("cmd", cmd_exe))
    return runners


def _received_argv(run, command: str) -> list[str] | None:
    result = run(command)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


@pytest.mark.parametrize("name", SAFE_NAMES)
def test_inert_paths_reach_every_shell_exactly_as_written(tmp_path: Path, name: str) -> None:
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / name).write_text("def test_x():\n    pass\n", encoding="utf-8")
    commands, _plan = _commands_and_plan(project, name)
    file_cmd = [c for c in commands if c.endswith(f"tests/{name} -q")]
    assert file_cmd, commands  # positive control: the bare command IS emitted
    runners = _shell_runners(tmp_path)
    if not runners:
        pytest.skip("no shell binary available")
    checked = 0
    for shell, run in runners:
        control = _received_argv(run, "uv run pytest control.py -q")
        if control != ["control.py", "-q"]:
            continue  # this shell binary cannot run the probe; do not pretend it was checked
        checked += 1
        assert _received_argv(run, file_cmd[0]) == [f"tests/{name}", "-q"], shell
    if checked == 0:
        pytest.skip("no usable shell binary for the execution probe")


@pytest.mark.parametrize("name", UNSAFE_NAMES)
def test_unsafe_paths_never_appear_in_any_command_string(tmp_path: Path, name: str) -> None:
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    commands, alignment = _commands_and_plan(project, name)
    for command in commands:
        assert name not in command, command
    # disclosure: the raw path is carried in a separate field, never in a command
    assert f"tests/{name}" in alignment.get("omitted_unsafe_paths", []), alignment
    assert alignment.get("omitted_note") == "path requires manual quoting", alignment


def test_unsafe_neighbour_suggestion_has_no_command_but_argv_and_raw_path(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_mod.py").write_text("def test_f():\n    pass\n", encoding="utf-8")
    # positive control: an inert neighbour yields a bare command
    ok = repo_map._suggested_validation_command_for_primary_file(
        str(tmp_path / "src" / "mod.py"), tmp_path
    )
    assert ok is not None and ok["command"] == "pytest tests/test_mod.py"

    (tmp_path / "src" / "my mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_my mod.py").write_text(
        "def test_f():\n    pass\n", encoding="utf-8"
    )
    entry = repo_map._suggested_validation_command_for_primary_file(
        str(tmp_path / "src" / "my mod.py"), tmp_path
    )
    assert entry is not None
    assert "command" not in entry
    assert entry["target_test"] == "tests/test_my mod.py"
    assert entry["argv"] == ["pytest", "tests/test_my mod.py"]
    assert entry["command_omitted"] == "path requires manual quoting"


def test_unsafe_javascript_test_title_falls_back_to_file_level_command(tmp_path: Path) -> None:
    project = tmp_path / "project"
    test_path = project / "tests" / "widget.test.js"
    test_path.parent.mkdir(parents=True)
    (project / "package.json").write_text(
        json.dumps({"devDependencies": {"jest": "^29.0.0"}}), encoding="utf-8"
    )
    test_path.write_text(
        "test('$(Write-Output PWN) widget', () => expect(1).toBe(1));\n", encoding="utf-8"
    )
    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [str(test_path.resolve())],
        repo_root=project,
        primary_test=str(test_path.resolve()),
        query="widget",
    )
    commands = [str(step["command"]) for step in plan]
    assert "npx jest tests/widget.test.js" in commands  # file-level step survives
    assert not any("PWN" in c for c in commands), commands
    # the dropped title must stay visible, raw, with the reason
    assert "$(Write-Output PWN) widget" in alignment.get("omitted_unsafe_paths", []), alignment
    assert alignment.get("omitted_note") == "path requires manual quoting", alignment
    assert alignment["issues"], alignment


def test_unsafe_cargo_manifest_omission_survives_the_primary_language_fallback(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    crate = project / "unsafe dir"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname = "x"\n', encoding="utf-8")
    (crate / "src" / "lib.rs").write_text("pub fn f() {}\n", encoding="utf-8")
    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [], repo_root=project, primary_file=crate / "src" / "lib.rs"
    )
    assert not any("unsafe dir" in str(step.get("command", "")) for step in plan), plan
    assert "unsafe dir/Cargo.toml" in alignment.get("omitted_unsafe_paths", []), alignment
    assert alignment.get("omitted_note") == "path requires manual quoting", alignment
    # positive control: an inert crate dir yields the manifest command and no disclosure
    ok = tmp_path / "ok"
    (ok / "crate" / "src").mkdir(parents=True)
    (ok / "crate" / "Cargo.toml").write_text('[package]\nname = "x"\n', encoding="utf-8")
    (ok / "crate" / "src" / "lib.rs").write_text("pub fn f() {}\n", encoding="utf-8")
    ok_plan, ok_alignment = repo_map._validation_plan_and_alignment_for_tests(
        [], repo_root=ok, primary_file=ok / "crate" / "src" / "lib.rs"
    )
    assert any("--manifest-path crate/Cargo.toml" in str(s.get("command")) for s in ok_plan), (
        ok_plan
    )
    assert "omitted_unsafe_paths" not in ok_alignment, ok_alignment


def test_only_render_command_constructs_an_omission() -> None:
    """Structural census: a rejection of a derived token has exactly one source, so the
    request-scoped collector inside it cannot be bypassed by a None/skip path."""
    import ast

    cli = Path(repo_map.__file__).parent
    constructors: list[str] = []
    for path in sorted(cli.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    func = node.func
                    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                    if name == "Omission":
                        constructors.append(f"{path.name}:{fn.name}")
    assert constructors == ["repo_map_shell_inert.py:render_command"], constructors


def test_render_command_records_every_rejection_in_the_active_collector() -> None:
    from tensor_grep.cli import repo_map_shell_inert as inert

    with inert.collecting() as outer:
        assert isinstance(
            inert.render_command("cargo", "test", inert.Derived("@x")), inert.Omission
        )
        with inert.collecting() as inner:
            inert.render_command("npx", "jest", inert.DerivedFilter("$(y)"))
        assert inner == ["$(y)"]  # inner scope sees its own
        inert.render_command("cargo", "test", inert.Derived("fine"))  # no rejection
    assert outer == ["@x", "$(y)"]  # and propagates to the enclosing scope
    # no active collector: rendering still works and records nothing
    assert isinstance(inert.render_command("a", inert.Derived("@z")), inert.Omission)


def _usable_runners(tmp_path: Path):
    usable = []
    for shell, run in _shell_runners(tmp_path):
        if _received_argv(run, "uv run pytest control.py -q") == ["control.py", "-q"]:
            usable.append((shell, run))
    return usable


def _rust_commands(project: Path, name: str) -> tuple[list[str], dict]:
    (project / "tests").mkdir(parents=True, exist_ok=True)
    (project / "Cargo.toml").write_text('[package]\nname = "x"\n', encoding="utf-8")
    test_path = project / "tests" / name
    test_path.write_text("#[test]\nfn it_works() {}\n", encoding="utf-8")
    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [str(test_path)],
        repo_root=project,
        primary_test=str(test_path),
        primary_symbol={"name": "it_works"},
    )
    return [str(step["command"]) for step in plan], alignment


def test_derived_rust_target_is_checked_even_when_the_path_looks_inert(tmp_path: Path) -> None:
    # `tests/@audit.rs` is an inert PATH, but the derived `--test @audit` token is PowerShell
    # splatting: the program would receive ['test', '--test'].
    project = tmp_path / "project"
    commands, alignment = _rust_commands(project, "@audit.rs")
    assert not any("@audit" in c for c in commands), commands
    assert "@audit" in alignment.get("omitted_unsafe_paths", []), alignment
    # every command that IS emitted reaches every usable shell exactly as written
    for shell, run in _usable_runners(tmp_path):
        for command in commands:
            _prefix, rest = _split_prefix(command)
            assert _received_argv(run, command) == rest.split(), (shell, command)


def test_derived_rust_target_with_leading_dash_is_not_emitted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    commands, alignment = _rust_commands(project, "-x.rs")
    for command in commands:
        tokens = command.split()
        assert not any(t.startswith("-x") for t in tokens), command
    assert "-x" in alignment.get("omitted_unsafe_paths", []), alignment


def test_positive_control_inert_rust_target_is_emitted_and_executes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    commands, _alignment = _rust_commands(project, "audit.rs")
    assert "cargo test --test audit" in commands, commands
    for shell, run in _usable_runners(tmp_path):
        assert _received_argv(run, "cargo test --test audit") == ["test", "--test", "audit"], shell


def test_unsafe_javascript_path_without_a_manifest_keeps_its_disclosure(tmp_path: Path) -> None:
    project = tmp_path / "project"
    test_path = project / "tests" / "unsafe name.test.js"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("test('x', () => {});\n", encoding="utf-8")
    raw = repo_map._raw_validation_plan_for_tests(
        [str(test_path)], repo_root=project, primary_test=str(test_path)
    )
    omitted = [step for step in raw if step.get("scope") == "omitted"]
    assert len(omitted) == 1, raw
    assert "command" not in omitted[0]
    assert omitted[0]["omitted_unsafe_paths"] == ["tests/unsafe name.test.js"]
    assert omitted[0]["omitted_note"] == "path requires manual quoting"
    # consumers never see the command-less entry, and the disclosure survives on the alignment
    plan, alignment = repo_map._validation_plan_and_alignment_for_tests(
        [str(test_path)], repo_root=project, primary_test=str(test_path)
    )
    assert all("command" in step for step in plan), plan
    assert alignment["omitted_unsafe_paths"] == ["tests/unsafe name.test.js"]
    assert alignment["omitted_note"] == "path requires manual quoting"
    assert repo_map._validation_commands_for_tests(
        [str(test_path)], repo_root=project, primary_test=str(test_path)
    ) == [str(step["command"]) for step in plan]


def test_render_command_checks_every_derived_token() -> None:
    from tensor_grep.cli import repo_map_shell_inert as inert

    assert inert.render_command("cargo", "test", "--test", inert.Derived("audit")) == (
        "cargo test --test audit"
    )
    for bad in ("@audit", "-x", "a b", "$(x)", "a;b", "", "café"):
        result = inert.render_command("cargo", "test", "--test", inert.Derived(bad))
        assert isinstance(result, inert.Omission), bad
        assert result.tokens == (bad,)
    quoted = inert.render_command("npx", "jest", inert.DerivedFilter("two words"))
    assert quoted == 'npx jest "two words"'
    assert isinstance(
        inert.render_command("npx", "jest", inert.DerivedFilter("$(x)")), inert.Omission
    )
    assert isinstance(
        inert.render_command("npx", "jest", inert.DerivedFilter("-x")), inert.Omission
    )


_BUILDER_MODULES = [
    "repo_map.py",
    "repo_map_lang_js.py",
    "repo_map_lang_rust.py",
    "repo_map_lang_python.py",
]
_COMMAND_HEADS = (
    "cargo",
    "uv run",
    "pytest",
    "npx",
    "npm",
    "pnpm",
    "yarn",
    "bun",
    "node",
    "vitest",
    "jest",
    "mocha",
    "python",
)


def _formatted_command_builders(source: str) -> list[str]:
    """String-formatting expressions whose literal part begins with a command head."""
    import ast

    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        literals: list[str] = []
        if isinstance(node, ast.JoinedStr):
            literals = [
                v.value
                for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            ]
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, (ast.Mod, ast.Add))
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
        ):
            literals = [node.left.value]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"format", "join"}
            and isinstance(node.func.value, ast.Constant)
            and isinstance(node.func.value.value, str)
        ):
            literals = [node.func.value.value]
        first = literals[0].lstrip() if literals else ""
        if any(first == head or first.startswith(head + " ") for head in _COMMAND_HEADS):
            hits.append(f"line {node.lineno}: {first!r}")
    return hits


def test_census_no_command_builder_formats_strings_directly() -> None:
    cli = Path(repo_map.__file__).parent
    offenders: dict[str, list[str]] = {}
    for name in _BUILDER_MODULES:
        path = cli / name
        if not path.exists():
            continue
        found = _formatted_command_builders(path.read_text(encoding="utf-8"))
        if found:
            offenders[name] = found
    assert not offenders, offenders  # build commands with repo_map_shell_inert.render_command


def test_census_detects_a_direct_fstring_builder() -> None:
    # positive control: the census must fire on the exact pre-fix shapes
    assert _formatted_command_builders('x = f"cargo test --test {target}"\n')
    assert _formatted_command_builders('x = "npx jest " + path\n')
    assert _formatted_command_builders('x = "uv run pytest %s" % path\n')
    assert not _formatted_command_builders('x = "cargo test"\n')
