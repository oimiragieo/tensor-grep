from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from tensor_grep.backends.ast_backend import get_supported_languages
from tensor_grep.cli.rg_contract import PUBLIC_SEARCH_HELP_FLAGS
from tensor_grep.cli.runtime_paths import resolve_native_tg_binary

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from helpers.byte_parity import split_lines_preserve_cr  # noqa: E402


def _run_and_decode(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    r"""Capture raw bytes -- no `text=True` -- then decode strictly, preserving any real
    `\r` verbatim. `text=True` runs the pipe through Python's universal-newlines
    TextIOWrapper, which translates a real `\r\n` in a launcher's stdout to `\n` on Windows
    before this file's launcher-vs-launcher comparisons (`test_routing_parity_matrix`, the
    help-contract tests) ever see it -- the same lossy transform applied identically to every
    launcher, so a genuine divergence between them (e.g. the native Rust binary's `println!`
    vs the Python launcher's own line-printing) would cancel out and read as parity (task
    #262). `bytes.decode()` (unlike `text=True`) does not perform newline translation, only
    character decoding, so this keeps every other call site's existing string-based logic
    working unchanged while closing the OS-level blind spot at the subprocess boundary.
    """
    completed_bytes = subprocess.run(cmd, capture_output=True, **kwargs)
    return subprocess.CompletedProcess(
        args=completed_bytes.args,
        returncode=completed_bytes.returncode,
        stdout=completed_bytes.stdout.decode("utf-8"),
        stderr=completed_bytes.stderr.decode("utf-8"),
    )


PUBLIC_TOP_LEVEL_COMMANDS = {
    "agent",
    "search",
    "calibrate",
    "upgrade",
    "update",
    "repair-launcher",
    "audit",
    "audit-verify",
    "mcp",
    "classify",
    "run",
    "scan",
    "test",
    "ast-info",
    "new",
    "defs",
    "refs",
    "source",
    "impact",
    "callers",
    "imports",
    "importers",
    "find",
    "blast-radius",
    "blast-radius-render",
    "blast-radius-plan",
    "diff-impact",
    "edit-plan",
    "context-render",
    "route-test",
    "prepare",
    "rulesets",
    "audit-history",
    "audit-diff",
    "review-bundle",
    "evidence",
    "ledger",
    "devices",
    "context",
    "lsp",
    "lsp-setup",
    "map",
    "orient",
    "codemap",
    "file-api",
    "freshness",
    "inventory",
    "docs-coverage",
    "session",
    "doctor",
    "checkpoint",
    "dogfood",
    "install-dense",
    "install",
    "uninstall",
    "repair-env",
    "sql",
}

# Commands that are clap `visible_alias`es of another command (e.g. `update` -> `upgrade`).
# clap renders aliases in a terminal-width/platform-dependent way, so a parity check against
# clap's own rendered help must treat these as OPTIONAL in the parsed set (see
# test_empty_invocation_fallback_help_matches_public_contract).
PUBLIC_TOP_LEVEL_ALIASES = {
    "update",
}


def _get_native_binary() -> str | None:
    try:
        resolve_native_tg_binary.cache_clear()
        native_binary = resolve_native_tg_binary()
    except FileNotFoundError:
        return None
    return str(native_binary) if native_binary is not None else None


def _resolve_cargo_exe() -> Path | None:
    cargo_name = "cargo.exe" if sys.platform == "win32" else "cargo"
    cargo_which = shutil.which("cargo")
    if cargo_which:
        resolved = Path(cargo_which)
        if resolved.name.lower() == cargo_name:
            return resolved
    fallback = Path.home() / ".cargo" / "bin" / cargo_name
    if fallback.exists():
        return fallback
    return None


def _run_native_front_door(
    args: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    native_binary = _get_native_binary()
    if native_binary is not None:
        return _run_and_decode(
            [native_binary, *args],
            cwd=cwd,
            env=env,
        )

    cargo_exe = _resolve_cargo_exe()
    if cargo_exe is None:
        pytest.skip("native front door not available in this environment")

    manifest_path = Path(__file__).resolve().parents[2] / "rust_core" / "Cargo.toml"
    return _run_and_decode(
        [
            str(cargo_exe),
            "run",
            "--quiet",
            "--manifest-path",
            str(manifest_path),
            "--bin",
            "tg",
            "--",
            *args,
        ],
        cwd=cwd,
        env=env,
    )


def _skip_if_native_binary_missing(launcher: str) -> None:
    if launcher == "native" and _get_native_binary() is None:
        pytest.skip("Native binary not built in this environment")


def run_command(
    launcher: str, args: list[str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    if launcher == "python-m":
        cmd = [sys.executable, "-m", "tensor_grep", *args]
    elif launcher == "native":
        native_binary = _get_native_binary()
        assert native_binary is not None, "Native binary not found. Please compile it first."
        cmd = [native_binary, *args]
    elif launcher == "bootstrap":
        cmd = [sys.executable, str(Path("src/tensor_grep/cli/bootstrap.py").resolve()), *args]
    else:
        raise ValueError(f"Unknown launcher {launcher}")

    return _run_and_decode(cmd, cwd=cwd)


def test_new_rule_and_test_scaffolds_match_and_pass_test_across_front_doors(tmp_path: Path):
    _skip_if_native_binary_missing("native")
    generated: dict[str, tuple[bytes, bytes]] = {}

    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        project_dir.mkdir()
        for args in (
            ["new", "project", "--yes"],
            ["new", "rule", "no-eval2", "--lang", "python", "--yes"],
            ["new", "test", "no-eval2", "--yes"],
        ):
            result = run_command(launcher, args, cwd=project_dir)
            assert result.returncode == 0, result.stdout + result.stderr

        generated[launcher] = (
            (project_dir / "rules" / "no-eval2.yml").read_bytes(),
            (project_dir / "tests" / "no-eval2.yml").read_bytes(),
        )
        result = run_command(launcher, ["test"], cwd=project_dir)
        assert result.returncode == 0, result.stdout + result.stderr

        (project_dir / "rules" / "no-eval2.yml").write_text(
            "id: no-eval2\nlanguage: python\nrule:\n  pattern: ''\n", encoding="utf-8"
        )
        result = run_command(launcher, ["test"], cwd=project_dir)
        assert result.returncode == 1
        assert "no-eval2: rule no-eval2 has an empty pattern" in (result.stdout + result.stderr)

    assert generated["python-m"] == generated["native"]


@pytest.mark.parametrize("language", get_supported_languages())
def test_new_test_uses_linked_rule_language_across_front_doors(tmp_path: Path, language: str):
    _skip_if_native_binary_missing("native")
    generated = {}
    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        project_dir.mkdir(exist_ok=True)
        for args in (
            ["new", "project", "--yes"],
            ["new", "rule", "linked", "--lang", language, "--yes"],
            ["new", "test", "linked", "--yes"],
        ):
            result = run_command(launcher, args, cwd=project_dir)
            assert result.returncode == 0, result.stdout + result.stderr
        result = run_command(launcher, ["test"], cwd=project_dir)
        assert result.returncode == 0, result.stdout + result.stderr
        generated[launcher] = (
            (project_dir / "rules" / "linked.yml").read_bytes(),
            (project_dir / "tests" / "linked.yml").read_bytes(),
        )
    assert generated["python-m"] == generated["native"]


@pytest.mark.parametrize(
    ("case", "rule_yaml", "args", "expected_code", "expected_message", "expected_valid"),
    [
        ("project_default", None, [], 0, "Created test scaffold", '"OTHER_IDENTIFIER"'),
        ("no_config", None, [], 0, "Created test scaffold", "other(1)"),
        ("linked_language", "language:  RuSt  \n", [], 0, "Created test scaffold", "other(1)"),
        (
            "linked_merge_block",
            "defaults: &defaults\n  severity: warning\n<<: *defaults\nlanguage: json\n",
            [],
            0,
            "Created test scaffold",
            '"OTHER_IDENTIFIER"',
        ),
        (
            "linked_merge_inline",
            "defaults: &defaults {severity: warning}\n<<: *defaults\nlanguage: json\n",
            [],
            0,
            "Created test scaffold",
            '"OTHER_IDENTIFIER"',
        ),
        ("config_merge", None, [], 0, "Created test scaffold", '"OTHER_IDENTIFIER"'),
        ("missing_language", "id: probe\n", [], 0, "Created test scaffold", '"OTHER_IDENTIFIER"'),
        (
            "blank_language",
            "language: '   '\n",
            [],
            0,
            "Created test scaffold",
            '"OTHER_IDENTIFIER"',
        ),
        (
            "explicit_over_linked",
            "language: json\n",
            ["--lang", " Python "],
            0,
            "Created test scaffold",
            "other(1)",
        ),
        (
            "explicit_over_malformed",
            "language: [\n",
            ["--lang", "python"],
            0,
            "Created test scaffold",
            "other(1)",
        ),
        ("malformed", "language: [\n", [], 1, "cannot parse linked rule rules/probe.yml:", None),
        (
            "duplicate_language",
            "language: json\nlanguage: python\n",
            [],
            1,
            "cannot parse linked rule rules/probe.yml: duplicate key 'language'",
            None,
        ),
        (
            "malformed_config_fallback",
            None,
            [],
            1,
            "cannot parse sgconfig.yml:",
            None,
        ),
        (
            "duplicate_config_language",
            None,
            [],
            1,
            "cannot parse sgconfig.yml: duplicate key 'language'",
            None,
        ),
        (
            "duplicate_config_rule_dirs",
            None,
            [],
            1,
            "cannot parse sgconfig.yml: duplicate key 'ruleDirs'",
            None,
        ),
        (
            "directory_config",
            None,
            [],
            1,
            "sgconfig.yml must be a regular file",
            None,
        ),
        (
            "unsupported",
            "language: unknown-language\n",
            [],
            1,
            "Cannot scaffold AST rule for language",
            None,
        ),
        ("blank_explicit", "language: rust\n", ["--lang", "   "], 1, "Invalid --lang", None),
        ("symlink", "language: rust\n", [], 1, "linked rule must be a regular file", None),
        ("dangling_symlink", None, [], 1, "linked rule must be a regular file", None),
        (
            "explicit_over_symlink",
            "language: rust\n",
            ["--lang", "json"],
            0,
            "Created test scaffold",
            '"OTHER_IDENTIFIER"',
        ),
        (
            "explicit_over_malformed_config",
            "language: rust\n",
            ["--lang", "python"],
            0,
            "Created test scaffold",
            "other(1)",
        ),
    ],
    ids=[
        "project_default",
        "no_config",
        "linked_language",
        "linked_merge_block",
        "linked_merge_inline",
        "config_merge",
        "missing_language",
        "blank_language",
        "explicit_over_linked",
        "explicit_over_malformed",
        "malformed",
        "duplicate_language",
        "malformed_config_fallback",
        "duplicate_config_language",
        "duplicate_config_rule_dirs",
        "directory_config",
        "unsupported",
        "blank_explicit",
        "symlink",
        "dangling_symlink",
        "explicit_over_symlink",
        "explicit_over_malformed_config",
    ],
)
def test_new_test_language_resolution_parity(
    tmp_path: Path,
    case: str,
    rule_yaml: str | None,
    args: list[str],
    expected_code: int,
    expected_message: str,
    expected_valid: str | None,
) -> None:
    _skip_if_native_binary_missing("native")
    outcomes: dict[str, tuple[int, str, dict[str, bytes]]] = {}
    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        rules_dir = project_dir / "rules"
        rules_dir.mkdir(parents=True)
        if case != "no_config":
            config_path = project_dir / "sgconfig.yml"
            if case == "directory_config":
                config_path.mkdir()
            else:
                config_yaml = {
                    "explicit_over_malformed_config": "language: [\n",
                    "malformed_config_fallback": "language: [\n",
                    "duplicate_config_language": "language: json\nlanguage: python\n",
                    "duplicate_config_rule_dirs": "ruleDirs: [rules]\nruleDirs: [other]\n",
                    "config_merge": "defaults: &defaults {severity: warning}\n<<: *defaults\nlanguage: json\n",
                }.get(case, "language: json\n")
                config_path.write_text(config_yaml, encoding="utf-8")
        rule_path = rules_dir / "probe.yml"
        if case in {"symlink", "dangling_symlink", "explicit_over_symlink"}:
            target = project_dir / "target.yml"
            if rule_yaml is not None:
                target.write_text(rule_yaml, encoding="utf-8")
            try:
                rule_path.symlink_to(target)
            except OSError as exc:
                pytest.skip(f"cannot create symlink on this host: {exc}")
        elif rule_yaml is not None:
            rule_path.write_text(rule_yaml, encoding="utf-8")

        before = {
            str(path.relative_to(project_dir)): path.read_bytes()
            for path in project_dir.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        result = run_command(launcher, ["new", "test", "probe", *args, "--yes"], cwd=project_dir)
        message = (result.stdout + result.stderr).strip().replace("tg.exe", "tg")
        written = {
            str(path.relative_to(project_dir)): path.read_bytes()
            for path in project_dir.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        outcomes[launcher] = (result.returncode, message, written)
        assert result.returncode == expected_code, message
        assert expected_message in message
        if case == "duplicate_language":
            assert message == f"Error: {expected_message}"
        if case == "malformed_config_fallback":
            assert message == "Error: cannot parse sgconfig.yml: invalid YAML syntax"
        if case in {"duplicate_config_language", "duplicate_config_rule_dirs", "directory_config"}:
            assert message == f"Error: {expected_message}"
        assert (project_dir / "tests" / "probe.yml").exists() == (expected_code == 0)
        if expected_code == 1:
            assert written == before
            assert not (project_dir / "tests").exists()
            assert "Traceback" not in message
        if expected_valid is not None:
            generated_test = yaml.safe_load(
                (project_dir / "tests" / "probe.yml").read_text(encoding="utf-8")
            )
            assert generated_test["valid"] == [expected_valid]

    assert outcomes["python-m"] == outcomes["native"]


@pytest.mark.parametrize("location", ["linked_rule", "config"])
@pytest.mark.parametrize("merge_form", ["block", "inline"])
def test_new_test_inherits_merged_yaml_language_across_front_doors(
    tmp_path: Path, location: str, merge_form: str
) -> None:
    _skip_if_native_binary_missing("native")
    defaults = (
        "defaults: &d\n  language: json\n"
        if merge_form == "block"
        else "defaults: &d {language: json}\n"
    )
    generated: dict[str, bytes] = {}
    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        rules_dir = project_dir / "rules"
        rules_dir.mkdir(parents=True)
        config = "ruleDirs: [rules]\ntestDirs: [tests]\n"
        if location == "config":
            config += defaults + "<<: *d\n"
        else:
            config += "language: python\n"
            (rules_dir / "probe.yml").write_text(
                defaults + '<<: *d\nid: probe\nrule:\n  pattern: "\\"TODO_REPLACE_ME\\""\n',
                encoding="utf-8",
            )
        (project_dir / "sgconfig.yml").write_text(config, encoding="utf-8")

        result = run_command(launcher, ["new", "test", "probe", "--yes"], cwd=project_dir)
        assert result.returncode == 0, result.stdout + result.stderr
        scaffold = project_dir / "tests" / "probe.yml"
        generated[launcher] = scaffold.read_bytes()
        assert yaml.safe_load(generated[launcher])["valid"] == ['"OTHER_IDENTIFIER"'], launcher

        if location == "config":
            (rules_dir / "probe.yml").write_text(
                'id: probe\nrule:\n  pattern: "\\"TODO_REPLACE_ME\\""\n',
                encoding="utf-8",
            )
        result = run_command(launcher, ["test"], cwd=project_dir)
        assert result.returncode == 0, result.stdout + result.stderr

    assert generated["python-m"] == generated["native"]


@pytest.mark.parametrize("location", ["linked_rule", "config"])
def test_new_test_refuses_duplicate_merge_keys_across_front_doors(
    tmp_path: Path, location: str
) -> None:
    _skip_if_native_binary_missing("native")
    outcomes = []
    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        rules_dir = project_dir / "rules"
        rules_dir.mkdir(parents=True)
        duplicate_merges = "first: &first {language: json}\nsecond: &second {severity: warning}\n<<: *first\n<<: *second\n"
        if location == "linked_rule":
            (project_dir / "sgconfig.yml").write_text("language: python\n", encoding="utf-8")
            (rules_dir / "probe.yml").write_text(duplicate_merges, encoding="utf-8")
            expected = "cannot parse linked rule rules/probe.yml: duplicate key '<<'"
        else:
            (project_dir / "sgconfig.yml").write_text(duplicate_merges, encoding="utf-8")
            expected = "cannot parse sgconfig.yml: duplicate key '<<'"
        before = {
            str(path.relative_to(project_dir)): path.read_bytes()
            for path in project_dir.rglob("*")
            if path.is_file()
        }

        result = run_command(launcher, ["new", "test", "probe", "--yes"], cwd=project_dir)
        message = (result.stdout + result.stderr).strip()
        after = {
            str(path.relative_to(project_dir)): path.read_bytes()
            for path in project_dir.rglob("*")
            if path.is_file()
        }
        assert result.returncode == 1, message
        assert message == f"Error: {expected}"
        assert after == before
        assert not (project_dir / "tests").exists()
        outcomes.append((result.returncode, message, after))

    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("location", ["linked_rule", "config"])
def test_new_test_refuses_boolean_yaml_language_across_front_doors(
    tmp_path: Path, location: str
) -> None:
    _skip_if_native_binary_missing("native")
    outcomes = []
    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        rules_dir = project_dir / "rules"
        rules_dir.mkdir(parents=True)
        if location == "linked_rule":
            (project_dir / "sgconfig.yml").write_text("language: python\n", encoding="utf-8")
            (rules_dir / "probe.yml").write_text("language: on\n", encoding="utf-8")
        else:
            (project_dir / "sgconfig.yml").write_text("language: on\n", encoding="utf-8")
        expected = (
            'Cannot scaffold AST rule for language "on"; run `tg ast-info` for supported languages.'
        )
        before = {
            str(path.relative_to(project_dir)): path.read_bytes()
            for path in project_dir.rglob("*")
            if path.is_file()
        }

        result = run_command(launcher, ["new", "test", "probe", "--yes"], cwd=project_dir)
        message = (result.stdout + result.stderr).strip()
        after = {
            str(path.relative_to(project_dir)): path.read_bytes()
            for path in project_dir.rglob("*")
            if path.is_file()
        }
        assert result.returncode == 1, message
        assert message == f"Error: {expected}"
        assert after == before
        assert not (project_dir / "tests").exists()
        outcomes.append((result.returncode, message, after))

    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("location", ["linked_rule", "config"])
def test_new_test_refuses_deep_yaml_without_recursion_crash(tmp_path: Path, location: str) -> None:
    _skip_if_native_binary_missing("native")
    deep_yaml = "extra: " + "[" * 600 + "0" + "]" * 600 + "\nlanguage: python\n"
    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        rules_dir = project_dir / "rules"
        rules_dir.mkdir(parents=True)
        if location == "linked_rule":
            (project_dir / "sgconfig.yml").write_text("language: python\n", encoding="utf-8")
            (rules_dir / "probe.yml").write_text(deep_yaml, encoding="utf-8")
            expected = "cannot parse linked rule rules/probe.yml: invalid YAML syntax"
        else:
            (project_dir / "sgconfig.yml").write_text(deep_yaml, encoding="utf-8")
            expected = "cannot parse sgconfig.yml: invalid YAML syntax"
        before = {
            str(path.relative_to(project_dir)): path.read_bytes()
            for path in project_dir.rglob("*")
            if path.is_file()
        }

        result = run_command(launcher, ["new", "test", "probe", "--yes"], cwd=project_dir)
        message = (result.stdout + result.stderr).strip()
        after = {
            str(path.relative_to(project_dir)): path.read_bytes()
            for path in project_dir.rglob("*")
            if path.is_file()
        }
        assert result.returncode == 1, message
        assert message == f"Error: {expected}"
        assert after == before
        assert not (project_dir / "tests").exists()


@pytest.mark.parametrize(
    ("config_text", "expected"),
    [
        (
            "language: python\nlanguage: json\n",
            "cannot parse sgconfig.yml: duplicate key 'language'",
        ),
        (
            "first: &first {language: json}\nsecond: &second {severity: warning}\n"
            "<<: *first\n<<: *second\n",
            "cannot parse sgconfig.yml: duplicate key '<<'",
        ),
        (
            "extra: " + "[" * 600 + "0" + "]" * 600 + "\nlanguage: python\n",
            "cannot parse sgconfig.yml: invalid YAML syntax",
        ),
        (
            "language: on\n",
            'Cannot scaffold AST rule for language "on"; run `tg ast-info` for supported languages.',
        ),
    ],
    ids=["duplicate-key", "duplicate-merge", "deep-nesting", "boolean-language"],
)
def test_new_test_explicit_config_yaml_validation_parity(
    tmp_path: Path, config_text: str, expected: str
) -> None:
    _skip_if_native_binary_missing("native")
    outcomes = []
    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        project_dir.mkdir()
        config_path = project_dir / "sgconfig.yml"
        config_path.write_text(config_text, encoding="utf-8")

        result = run_command(
            launcher,
            ["new", "test", "probe", "--config", str(config_path), "--yes"],
            cwd=project_dir,
        )
        message = (result.stdout + result.stderr).strip()
        assert result.returncode == 1, message
        assert message == f"Error: {expected}"
        assert not (project_dir / "tests").exists()
        outcomes.append((result.returncode, message))

    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("kind", ["project", "rule", "test"])
def test_new_refuses_blank_language_identically_across_front_doors(tmp_path: Path, kind: str):
    _skip_if_native_binary_missing("native")
    outcomes = []
    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        project_dir.mkdir()
        args = ["new", kind, *([] if kind == "project" else ["blank"]), "--lang", "   ", "--yes"]
        result = run_command(launcher, args, cwd=project_dir)
        outcomes.append((result.returncode, result.stdout + result.stderr))
        assert list(project_dir.iterdir()) == []
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] != 0


@pytest.mark.parametrize("launcher", ["python-m", "native"])
def test_run_java_rewrite_keeps_baseline_refusal(tmp_path: Path, launcher: str):
    _skip_if_native_binary_missing(launcher)
    (tmp_path / "Test.java").write_text("x", encoding="utf-8")
    result = run_command(launcher, ["run", "--lang", "java", "-p", "x", str(tmp_path), "-r", "y"])
    assert result.returncode == 2
    assert "Unsupported language: java" in (result.stdout + result.stderr)


@pytest.mark.parametrize("language", [" Python ", "PY", "c++"])
def test_new_scaffold_normalizes_language_across_front_doors(tmp_path: Path, language: str):
    _skip_if_native_binary_missing("native")
    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        project_dir.mkdir(exist_ok=True)
        result = run_command(
            launcher, ["new", "rule", "normalized", "--lang", language, "--yes"], cwd=project_dir
        )
        assert result.returncode == 0, result.stdout + result.stderr
        rule = yaml.safe_load(
            (project_dir / "rules" / "normalized.yml").read_text(encoding="utf-8")
        )
        assert rule["language"] == {" Python ": "python", "PY": "python", "c++": "cpp"}[language]


@pytest.mark.parametrize("name", ["null", "true", "1", "~", "a#b"])
def test_new_scaffold_names_round_trip_across_front_doors(tmp_path: Path, name: str):
    _skip_if_native_binary_missing("native")
    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        project_dir.mkdir(exist_ok=True)
        for kind in ("rule", "test"):
            result = run_command(
                launcher, ["new", kind, name, "--lang", "python", "--yes"], cwd=project_dir
            )
            assert result.returncode == 0, result.stdout + result.stderr
        rule = yaml.safe_load((project_dir / "rules" / f"{name}.yml").read_text(encoding="utf-8"))
        case = yaml.safe_load((project_dir / "tests" / f"{name}.yml").read_text(encoding="utf-8"))
        assert rule["id"] == case["id"] == case["ruleId"] == name
        if name == "null":
            (project_dir / "sgconfig.yml").write_text(
                "ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: python\n", encoding="utf-8"
            )
            result = run_command(launcher, ["test"], cwd=project_dir)
            assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", ["a:b", "a b", "a/b", "a\\b"])
def test_new_scaffold_refuses_names_outside_allowed_set_across_front_doors(
    tmp_path: Path, name: str
):
    _skip_if_native_binary_missing("native")
    for launcher in ("python-m", "native"):
        result = run_command(
            launcher, ["new", "rule", name, "--lang", "python", "--yes"], cwd=tmp_path
        )
        assert result.returncode != 0
        assert "Invalid item name" in (result.stdout + result.stderr)
        assert not (tmp_path / "rules" / f"{name}.yml").exists()


@pytest.mark.parametrize("launcher", ["python-m", "native"])
@pytest.mark.parametrize("kind", ["rule", "test", "project"])
def test_new_scaffold_refuses_dangling_symlink_across_front_doors(
    tmp_path: Path, launcher: str, kind: str
):
    _skip_if_native_binary_missing(launcher)
    project_dir = tmp_path / launcher
    directory_name = {"rule": "rules", "test": "tests", "project": ""}[kind]
    target_dir = project_dir / directory_name
    target_dir.mkdir(parents=True)
    target = tmp_path / f"outside-{launcher}.yml"
    destination = target_dir / ("sgconfig.yml" if kind == "project" else "escape.yml")
    try:
        destination.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"cannot create symlink on this host: {exc}")

    result = run_command(
        launcher,
        (
            ["new", "rule", "escape", "--lang", "python", "--yes"]
            if kind == "rule"
            else ["new", "test", "escape", "--yes"]
            if kind == "test"
            else ["new", "project", "--yes"]
        ),
        cwd=project_dir,
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert destination.is_symlink()
    assert not target.exists()


def test_rule_scaffolds_pass_for_every_ast_info_language_and_match_across_front_doors(
    tmp_path: Path,
):
    _skip_if_native_binary_missing("native")
    language_result = run_command("native", ["ast-info", "--json"])
    assert language_result.returncode == 0, language_result.stdout + language_result.stderr
    languages = json.loads(language_result.stdout)["languages"]
    documented_refusals: dict[str, str] = {}
    docs = (Path(__file__).parents[2] / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    assert set(documented_refusals) <= set(languages)
    assert all(
        reason and f"`{language}`" in docs and reason in docs
        for language, reason in documented_refusals.items()
    )
    generated: dict[str, dict[str, tuple[bytes, bytes]]] = {"python-m": {}, "native": {}}
    failures = []

    for language in languages:
        for launcher in ("python-m", "native"):
            project_dir = tmp_path / launcher / language
            project_dir.mkdir(parents=True)
            result = run_command(launcher, ["new", "project", "--yes"], cwd=project_dir)
            if result.returncode != 0:
                failures.append(
                    f"{launcher} {language} new project: {result.stdout}{result.stderr}"
                )
                continue
            result = run_command(
                launcher,
                ["new", "rule", "scaffold-check", "--lang", language, "--yes"],
                cwd=project_dir,
            )
            if result.returncode != 0:
                if language not in documented_refusals or (
                    "no validated pattern example is available for `tg test` on both front doors"
                    not in (result.stdout + result.stderr)
                ):
                    failures.append(
                        f"{launcher} {language} new rule refusal: {result.stdout}{result.stderr}"
                    )
                if (project_dir / "rules" / "scaffold-check.yml").exists():
                    failures.append(f"{launcher} {language} wrote a refused rule scaffold")
                continue
            if language in documented_refusals:
                failures.append(
                    f"{launcher} {language} unexpectedly scaffolded despite documented refusal"
                )
            result = run_command(
                launcher,
                ["new", "test", "scaffold-check", "--lang", language, "--yes"],
                cwd=project_dir,
            )
            if result.returncode != 0:
                failures.append(f"{launcher} {language} new test: {result.stdout}{result.stderr}")
                continue
            result = run_command(launcher, ["test"], cwd=project_dir)
            if result.returncode != 0:
                failures.append(f"{launcher} {language} test: {result.stdout}{result.stderr}")
                continue
            generated[launcher][language] = (
                (project_dir / "rules" / "scaffold-check.yml").read_bytes(),
                (project_dir / "tests" / "scaffold-check.yml").read_bytes(),
            )

    assert not failures, "\n".join(failures)
    assert generated["python-m"] == generated["native"]


def test_native_run_matches_each_scaffold_invalid_snippet_only(tmp_path: Path):
    _skip_if_native_binary_missing("native")
    result = run_command("native", ["ast-info", "--json"])
    assert result.returncode == 0, result.stdout + result.stderr
    for language in json.loads(result.stdout)["languages"]:
        project_dir = tmp_path / language
        project_dir.mkdir()
        result = run_command(
            "native", ["new", "rule", "probe", "--lang", language, "--yes"], cwd=project_dir
        )
        assert result.returncode == 0, f"{language}: {result.stdout}{result.stderr}"
        result = run_command(
            "native", ["new", "test", "probe", "--lang", language, "--yes"], cwd=project_dir
        )
        assert result.returncode == 0, f"{language}: {result.stdout}{result.stderr}"
        pattern = yaml.safe_load((project_dir / "rules" / "probe.yml").read_text(encoding="utf-8"))[
            "rule"
        ]["pattern"]
        cases = yaml.safe_load((project_dir / "tests" / "probe.yml").read_text(encoding="utf-8"))
        for snippet, expected in ((cases["invalid"][0], 1), (cases["valid"][0], 0)):
            fixture = project_dir / "probe.txt"
            fixture.write_text(snippet, encoding="utf-8")
            result = run_command(
                "native", ["run", "--lang", language, "-p", pattern, str(fixture), "--json"]
            )
            if language not in {"python", "javascript", "typescript", "rust"}:
                assert result.returncode == 2
                assert result.stdout == ""
                assert result.stderr == f"Error: Unsupported language: {language}\n"
                continue
            assert result.returncode == (0 if expected else 1), (
                f"{language} {snippet!r}: {result.stdout}{result.stderr}"
            )
            assert json.loads(result.stdout)["total_matches"] == expected


def test_scan_ignores_unused_empty_rule_with_identical_json_across_front_doors(
    tmp_path: Path,
):
    _skip_if_native_binary_missing("native")
    project_dir = tmp_path / "project"
    rules_dir = project_dir / "rules"
    rules_dir.mkdir(parents=True)
    (project_dir / "sgconfig.yml").write_text(
        "ruleDirs: [rules]\nlanguage: python\n", encoding="utf-8"
    )
    (rules_dir / "valid.yml").write_text(
        "id: valid-rule\nlanguage: python\nrule:\n  pattern: ERROR\n", encoding="utf-8"
    )
    (rules_dir / "unused-empty.yml").write_text(
        "id: unused-empty\nlanguage: python\nrule:\n  pattern: ''\n", encoding="utf-8"
    )
    (project_dir / "source.py").write_text("ERROR\n", encoding="utf-8")

    outputs = []
    for launcher in ("python-m", "native"):
        result = run_command(launcher, ["scan", "--json"], cwd=project_dir)

        assert result.returncode == 0, result.stdout + result.stderr
        output = json.loads(result.stdout)
        outputs.append(output)
        assert output["rule_count"] == 1
        assert [finding["rule_id"] for finding in output["findings"]] == ["valid-rule"]

    assert outputs[0] == outputs[1]


def test_test_empty_case_pattern_resolves_linked_rule_across_front_doors(tmp_path: Path):
    _skip_if_native_binary_missing("native")
    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        (project_dir / "rules").mkdir(parents=True)
        (project_dir / "tests").mkdir()
        (project_dir / "sgconfig.yml").write_text(
            "ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: python\n", encoding="utf-8"
        )
        rule_path = project_dir / "rules" / "linked.yml"
        rule_path.write_text(
            "id: linked-rule\nlanguage: python\nrule:\n  pattern: ERROR\n", encoding="utf-8"
        )
        test_path = project_dir / "tests" / "linked.yml"
        test_path.write_text(
            "id: linked-test\nruleId: linked-rule\npattern: ''\nvalid: [PASS]\ninvalid: [ERROR]\n",
            encoding="utf-8",
        )

        result = run_command(launcher, ["test"], cwd=project_dir)
        assert result.returncode == 0, result.stdout + result.stderr


def test_test_merged_empty_linked_rule_pattern_across_front_doors(tmp_path: Path) -> None:
    _skip_if_native_binary_missing("native")
    outcomes = []
    for launcher in ("python-m", "native"):
        project_dir = tmp_path / launcher
        (project_dir / "rules").mkdir(parents=True)
        (project_dir / "tests").mkdir()
        (project_dir / "sgconfig.yml").write_text(
            "ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: python\n", encoding="utf-8"
        )
        (project_dir / "rules" / "probe.yml").write_text(
            "defaults: &defaults {pattern: ''}\nid: probe\nlanguage: python\nrule:\n  <<: *defaults\n",
            encoding="utf-8",
        )
        (project_dir / "tests" / "probe.yml").write_text(
            "id: probe\nruleId: probe\nvalid: [PASS]\ninvalid: [ERROR]\n", encoding="utf-8"
        )

        result = run_command(launcher, ["test"], cwd=project_dir)
        message = result.stdout + result.stderr
        assert result.returncode == 1, message
        assert "probe: rule probe has an empty pattern" in message
        assert "missing pattern or ruleId" not in message
        outcomes.append(message.replace(str(project_dir), "<project>"))

    assert outcomes[0] == outcomes[1]


LAUNCHERS = ["python-m", "native", "bootstrap"]


# A fixture to create a realistic search environment
@pytest.fixture(scope="module")
def parity_env(tmp_path_factory):
    env_dir = tmp_path_factory.mktemp("parity_env")
    (env_dir / "target.txt").write_text("apple\nbanana\napple banana\ncherry\n", encoding="utf-8")
    (env_dir / "other.log").write_text("apple juice\nno match here\n", encoding="utf-8")
    (env_dir / "dir").mkdir()
    (env_dir / "dir" / "nested.txt").write_text("banana apple\n", encoding="utf-8")
    (env_dir / "dir" / "second.txt").write_text("apple tart\n", encoding="utf-8")
    return env_dir


# Format: (args, expected_exit_code, check_stdout_fn, check_stderr_fn)
# We use simple string match or lambda for flexibility.
def assert_json(stdout: str):
    assert stdout.strip() != ""
    for line in stdout.strip().splitlines():
        if not line.startswith("[routing]"):  # rust might print this to stderr, but just in case
            json.loads(line)


def assert_ndjson(stdout: str):
    assert stdout.strip() != ""
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    assert len(lines) > 0
    for line in lines:
        try:
            json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"Invalid NDJSON line: {line}")


def assert_text_lines(stdout: str):
    assert len(stdout.splitlines()) > 0


def assert_empty(stdout: str):
    assert not stdout.strip()


def no_check(out: str):
    pass


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _extract_visible_help_commands(stdout: str) -> set[str]:
    commands: set[str] = set()
    mode: str | None = None
    box_header_prefixes = ("┌", "╭", "┏", "+")
    box_footer_prefixes = ("└", "╰", "┗", "+")
    box_verticals = "│┃║╎┆|"
    for raw_line in stdout.splitlines():
        line = _strip_ansi(raw_line)
        stripped = line.strip()
        if stripped == "Commands:":
            mode = "plain"
            continue
        if "Commands" in stripped and stripped.startswith(box_header_prefixes):
            mode = "box"
            continue
        if mode is None:
            continue
        if mode == "plain":
            if stripped in {"Options:", "Environment overrides:"}:
                break
            cleaned = line.lstrip()
        else:
            if stripped.startswith(box_footer_prefixes):
                break
            if not stripped.startswith(tuple(box_verticals)):
                continue
            cleaned = stripped.strip(box_verticals).strip()
        if not cleaned:
            continue
        match = re.match(r"^([a-z][a-z0-9-]*)\s{2,}", cleaned)
        if match:
            commands.add(match.group(1))
            alias_match = re.search(r"\[aliases?: ([^\]]+)\]", cleaned)
            if alias_match:
                for alias in alias_match.group(1).split(","):
                    normalized = alias.strip()
                    if re.match(r"^[a-z][a-z0-9-]*$", normalized):
                        commands.add(normalized)
    return commands


def test_extract_visible_help_commands_handles_unicode_box_help() -> None:
    stdout = """
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ search               Search files for a regex pattern.                      │
│ doctor               Print diagnostics.                                     │
│ upgrade              Upgrade tensor-grep to the latest version.             │
╰──────────────────────────────────────────────────────────────────────────────╯
"""

    assert _extract_visible_help_commands(stdout) == {"search", "doctor", "upgrade"}


def test_search_force_cpu_alias_matches_cpu_flag(parity_env) -> None:
    force_result = run_command(
        "python-m", ["search", "--force-cpu", "apple", "target.txt"], cwd=parity_env
    )
    cpu_result = run_command("python-m", ["search", "--cpu", "apple", "target.txt"], cwd=parity_env)

    assert force_result.returncode == cpu_result.returncode == 0, (
        f"--force-cpu stdout: {force_result.stdout}\nstderr: {force_result.stderr}\n"
        f"--cpu stdout: {cpu_result.stdout}\nstderr: {cpu_result.stderr}"
    )
    assert force_result.stdout == cpu_result.stdout
    assert force_result.stderr == cpu_result.stderr


def test_native_top_level_pcre2_version_matches_public_contract(parity_env) -> None:
    _skip_if_native_binary_missing("native")

    result = _run_native_front_door(["--pcre2-version"], cwd=parity_env)

    assert result.returncode == 0, result.stderr
    assert "PCRE2" in result.stdout
    assert result.stderr.strip() == ""


def test_native_pcre2_without_ripgrep_fails_closed(parity_env) -> None:
    """Audit #81 finding #9: `--pcre2` must fail closed (exit 2) when rg is unavailable rather
    than silently swapping to the native regex engine, which does not support PCRE2 syntax."""
    _skip_if_native_binary_missing("native")

    env = dict(**os.environ, TG_DISABLE_RG="1")
    result = _run_native_front_door(
        ["search", "--pcre2", "apple", "target.txt"], cwd=parity_env, env=env
    )

    assert result.returncode == 2, (
        f"expected exit 2 (backend unavailable), got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "--pcre2" in result.stderr
    assert result.stdout.strip() == ""


def test_native_search_required_passthrough_flag_without_ripgrep_exits_2_not_1(
    parity_env,
) -> None:
    """Audit #81 finding #7: a passthrough-required flag (e.g. --max-depth) with rg unavailable
    must exit 2 (backend unavailable), not the masked exit 1 ("no match") that bubbling the
    `execute_ripgrep_search` error through `?` to main()'s default Result termination used to
    produce."""
    _skip_if_native_binary_missing("native")

    env = dict(**os.environ, TG_DISABLE_RG="1")
    result = _run_native_front_door(
        ["search", "--max-depth", "2", "apple", "target.txt"], cwd=parity_env, env=env
    )

    assert result.returncode == 2, (
        "expected exit 2 (backend unavailable), not 1 (no-match-shaped); got "
        f"{result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_native_scan_ruleset_json_uses_python_full_contract_without_sgconfig(
    parity_env,
) -> None:
    _skip_if_native_binary_missing("native")
    (parity_env / "safe.py").write_text("def f():\n    return 'ok'\n", encoding="utf-8")

    result = _run_native_front_door(
        ["scan", "--ruleset", "secrets-basic", "--json", "--path", "."],
        cwd=parity_env,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ruleset"] == "secrets-basic"
    assert payload["routing_reason"] == "builtin-ruleset-scan"
    assert payload["path"] == str(parity_env.resolve())


# We check which layer handled it.
# For native rust, search is usually native unless it falls back to Python.
# The public Python/bootstrap entrypoints delegate `run` to the managed native front door
# when one is available, so its advertised edit flags stay route-consistent.
# We don't strictly assert the exact layer unless requested, but we can verify it doesn't crash.

COMMAND_CASES = [
    # --- search ---
    (["search", "--help"], 0, assert_text_lines, no_check),
    (["apple", "target.txt"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "--json"], 0, assert_json, no_check),
    (["search", "apple", "target.txt", "--ndjson"], 0, assert_ndjson, no_check),
    (["search", "--cpu", "apple", "target.txt"], 0, assert_text_lines, no_check),
    (["search", "--cpu", "apple", "target.txt", "--json"], 0, assert_json, no_check),
    (["search", "apple", "target.txt", "-o"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "-r", "orange"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "-F"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "-w"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "-m", "1"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "-c"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "-C", "1"], 0, assert_text_lines, no_check),
    (["search", "apple", "target.txt", "--glob", "*.txt"], 0, assert_text_lines, no_check),
    # missing target
    (
        ["search", "notfoundinanyfile", "target.txt"],
        1,
        no_check,
        no_check,
    ),  # usually exit 1 for no matches
    # --- run / scan / test ---
    (["run", "--help"], 0, assert_text_lines, no_check),
    (["scan", "--help"], 0, assert_text_lines, no_check),
    (["test", "--help"], 0, assert_text_lines, no_check),
    # --- map / doctor / session / checkpoint ---
    (["map", "--help"], 0, assert_text_lines, no_check),
    (["doctor", "--help"], 0, assert_text_lines, no_check),
    (["session", "--help"], 0, assert_text_lines, no_check),
    (["checkpoint", "--help"], 0, assert_text_lines, no_check),
    # --- defs / refs / context ---
    (["defs", "--help"], 0, assert_text_lines, no_check),
    (["refs", "--help"], 0, assert_text_lines, no_check),
    (["context", "--help"], 0, assert_text_lines, no_check),
    (["imports", "--help"], 0, assert_text_lines, no_check),
    (["importers", "--help"], 0, assert_text_lines, no_check),
    # --- blast-radius, rulesets, audit ---
    (["blast-radius", "--help"], 0, assert_text_lines, no_check),
    (["rulesets", "--help"], 0, assert_text_lines, no_check),
    (["audit-verify", "--help"], 0, assert_text_lines, no_check),
    (["audit-history", "--help"], 0, assert_text_lines, no_check),
    (["audit-diff", "--help"], 0, assert_text_lines, no_check),
    (["review-bundle", "--help"], 0, assert_text_lines, no_check),
    (["evidence", "--help"], 0, assert_text_lines, no_check),
]


@pytest.mark.parametrize("args, expected_code, check_stdout, check_stderr", COMMAND_CASES)
def test_routing_parity_matrix(
    parity_env, args: list[str], expected_code: int, check_stdout, check_stderr
):
    if "--ndjson" in args and _get_native_binary() is None:
        pytest.skip("python -m tensor_grep requires native tg support for --ndjson")

    # We will use python-m as the baseline
    baseline_result = run_command("python-m", args, cwd=parity_env)

    if expected_code is not None:
        assert baseline_result.returncode == expected_code, (
            f"Failed on python-m {' '.join(args)}\nstdout: {baseline_result.stdout}\nstderr: {baseline_result.stderr}"
        )

    check_stdout(baseline_result.stdout)
    check_stderr(baseline_result.stderr)

    for launcher in ["native", "bootstrap"]:
        _skip_if_native_binary_missing(launcher)
        result = run_command(launcher, args, cwd=parity_env)

        assert result.returncode == baseline_result.returncode, (
            f"Exit code mismatch for {launcher} vs python-m on args: {args}\npython-m: {baseline_result.returncode}\n{launcher}: {result.returncode}"
        )

        # To compare stdout/stderr we ignore execution time logs, routing reason logs, or other non-deterministic stuff.
        # NOTE: split_lines_preserve_cr, not str.splitlines() -- this feeds the direct
        # `la_stdout == bl_stdout` launcher-vs-launcher comparison below, and
        # str.splitlines() would silently drop a real `\r`, letting a genuine line-ending
        # divergence between launchers (e.g. native `println!` vs the Python launcher's own
        # printing) cancel out identically on both sides (task #262).
        def clean_output(output: str) -> str:
            output = output.replace("bootstrap.py", "python -m tensor_grep")
            lines = [
                line
                for line in split_lines_preserve_cr(output)
                if not line.startswith("[routing]") and not line.startswith("[stats]")
            ]
            return "\n".join(lines)

        bl_stdout = clean_output(baseline_result.stdout)
        la_stdout = clean_output(result.stdout)

        if "--help" in args:
            # Typer and Clap output differently formatted help text. Even between python-m and bootstrap,
            # Typer's word wrapping changes based on the length of sys.argv[0].
            # We assert that both commands successfully generated help text (Usage string is present).
            assert "Usage:" in la_stdout or "usage:" in la_stdout.lower(), (
                f"Help missing Usage:\n{la_stdout}"
            )
            assert "Usage:" in bl_stdout or "usage:" in bl_stdout.lower(), (
                f"Baseline help missing Usage:\n{bl_stdout}"
            )
        else:
            if "--json" in args or "--ndjson" in args:
                # normalize json before comparing
                def _norm_json(out: str) -> str:
                    try:
                        return json.dumps(json.loads(out), sort_keys=True)
                    except json.JSONDecodeError:
                        return out

                def _norm_ndjson(out: str) -> str:
                    return "\n".join(
                        json.dumps(json.loads(line), sort_keys=True) if line.strip() else line
                        for line in out.splitlines()
                    )

                if "--json" in args:
                    assert _norm_json(la_stdout) == _norm_json(bl_stdout), (
                        f"Stdout JSON mismatch for {launcher} vs python-m on args: {args}"
                    )
                else:
                    assert _norm_ndjson(la_stdout) == _norm_ndjson(bl_stdout), (
                        f"Stdout NDJSON mismatch for {launcher} vs python-m on args: {args}"
                    )
            else:
                assert la_stdout == bl_stdout, (
                    f"Stdout mismatch for {launcher} vs python-m on args: {args}"
                )

        bl_stderr = clean_output(baseline_result.stderr)
        la_stderr = clean_output(result.stderr)

        # Only compare stderr if not help/version since typr/clap help might differ slightly
        if "--help" not in args:
            assert la_stderr == bl_stderr, (
                f"Stderr mismatch for {launcher} vs python-m on args: {args}"
            )


@pytest.mark.parametrize("launcher", ["bootstrap"])
def test_routing_parity_glob(parity_env, launcher: str):
    # Test glob filtering across multiple matched files in a nested directory. This avoids
    # relying on platform-specific `**/*.txt` behavior in raw ripgrep passthrough while
    # still exercising bootstrap-to-python launcher parity for glob handling.
    args = ["search", "apple", ".", "--glob", "dir/*.txt"]
    bl_result = run_command("python-m", args, cwd=parity_env)
    la_result = run_command(launcher, args, cwd=parity_env)

    assert la_result.returncode == bl_result.returncode == 0

    def extract_matched_files(stdout: str) -> set[str]:
        files = set()
        for line in stdout.splitlines():
            if ":" in line and not line.startswith("["):
                # Extract filepath, normalizing prefixes and separators for cross-platform
                # comparison between rg passthrough and Python/native launchers.
                filepath = line.split(":", 1)[0].replace(".\\", "").replace("./", "")
                filepath = filepath.replace("\\", "/")
                files.add(filepath)
        return files

    bl_files = extract_matched_files(bl_result.stdout)
    la_files = extract_matched_files(la_result.stdout)

    assert la_files == bl_files


def test_routing_parity_glob_skips_native_when_binary_is_missing(monkeypatch, parity_env):
    monkeypatch.setattr(sys.modules[__name__], "_get_native_binary", lambda: None)

    with pytest.raises(pytest.skip.Exception, match="Native binary not built"):
        _skip_if_native_binary_missing("native")


def test_search_help_exposes_required_public_flags(parity_env):
    python_help = run_command("python-m", ["search", "--help"], cwd=parity_env)
    native_help = _run_native_front_door(["search", "--help"], cwd=parity_env)

    assert python_help.returncode == 0
    assert native_help.returncode == 0

    python_stdout = _strip_ansi(python_help.stdout)
    native_stdout = _strip_ansi(native_help.stdout)

    for flag in PUBLIC_SEARCH_HELP_FLAGS:
        assert flag in python_stdout, f"Missing {flag} in python-m search --help"
        assert flag in native_stdout, f"Missing {flag} in native search --help"


def test_run_help_exposes_native_diff_contract_on_public_routes(parity_env):
    _skip_if_native_binary_missing("native")

    for launcher in LAUNCHERS:
        result = run_command(launcher, ["run", "--help"], cwd=parity_env)

        assert result.returncode == 0, (
            f"{launcher} run --help failed\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "--diff" in _strip_ansi(result.stdout), (
            f"{launcher} run --help is missing native --diff contract"
        )


def test_top_level_help_visible_commands_match_public_contract(parity_env):
    python_help = run_command("python-m", ["--help"], cwd=parity_env)
    native_help = _run_native_front_door(["--help"], cwd=parity_env)

    assert python_help.returncode == 0
    assert native_help.returncode == 0
    python_commands = _extract_visible_help_commands(python_help.stdout)
    native_commands = _extract_visible_help_commands(native_help.stdout)

    assert python_commands == PUBLIC_TOP_LEVEL_COMMANDS
    assert native_commands == PUBLIC_TOP_LEVEL_COMMANDS


def test_empty_invocation_visible_commands_match_public_contract(parity_env):
    python_help = run_command("python-m", [], cwd=parity_env)
    native_help = _run_native_front_door([], cwd=parity_env)

    assert python_help.returncode == 0
    assert native_help.returncode == 0
    python_commands = _extract_visible_help_commands(python_help.stdout)
    native_commands = _extract_visible_help_commands(native_help.stdout)
    assert python_commands == PUBLIC_TOP_LEVEL_COMMANDS
    assert native_commands == PUBLIC_TOP_LEVEL_COMMANDS


def test_public_help_falls_back_to_native_when_python_passthrough_is_broken(parity_env):
    if sys.platform == "win32":
        broken_python = str(shutil.which("powershell.exe"))
    else:
        broken_python = "/bin/sh"
    env = {**os.environ, "TG_SIDECAR_PYTHON": broken_python}

    native_help = _run_native_front_door(["search", "--help"], cwd=parity_env, env=env)

    assert native_help.returncode == 0
    assert "Usage:" in native_help.stdout
    assert "search" in native_help.stdout
    assert native_help.stderr.strip() == ""


def test_empty_invocation_fallback_help_matches_public_contract(parity_env):
    if sys.platform == "win32":
        broken_python = str(shutil.which("powershell.exe"))
    else:
        broken_python = "/bin/sh"
    env = {**os.environ, "TG_SIDECAR_PYTHON": broken_python}

    native_help = _run_native_front_door([], cwd=parity_env, env=env)

    assert native_help.returncode == 0
    # Unlike the passthrough parity tests above (which parse Python/Typer's stable help), this
    # fallback path is rendered by clap's own formatter (`CommandCli::command().print_help()` in
    # `print_native_top_level_help`). clap lays out the `update` visible-alias in a terminal-
    # width/platform-dependent way, so it is not always parsed back out of the rendered text --
    # the SAME byte-identical native binary passed this on one v1.76.10 release run and failed it
    # on the next (PR #616). Assert the load-bearing invariant instead of exact set equality:
    # every REAL command is present and no UNEXPECTED command leaks; only known aliases may be
    # absent. This still catches genuine native<->Python command drift.
    native_commands = _extract_visible_help_commands(native_help.stdout)
    assert native_commands <= PUBLIC_TOP_LEVEL_COMMANDS, (
        "native fallback help exposed commands outside the public contract: "
        f"{sorted(native_commands - PUBLIC_TOP_LEVEL_COMMANDS)}"
    )
    missing_real = (PUBLIC_TOP_LEVEL_COMMANDS - native_commands) - PUBLIC_TOP_LEVEL_ALIASES
    assert not missing_real, (
        f"native fallback help is missing real (non-alias) commands: {sorted(missing_real)}"
    )
    assert native_help.stderr.strip() == ""


def test_public_help_falls_back_to_native_when_python_passthrough_times_out(parity_env):
    if sys.platform == "win32":
        wrapper = parity_env / "wedged-python.cmd"
        wrapper.write_text("@echo off\r\nping -n 9 127.0.0.1 >nul\r\n", encoding="utf-8")
    else:
        wrapper = parity_env / "wedged-python.sh"
        wrapper.write_text("#!/bin/sh\nsleep 8\n", encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

    env = {**os.environ, "TG_SIDECAR_PYTHON": str(wrapper)}
    if _get_native_binary() is None:
        _run_native_front_door(["--version"], cwd=parity_env)

    started = __import__("time").perf_counter()
    native_help = _run_native_front_door(["search", "--help"], cwd=parity_env, env=env)
    elapsed = __import__("time").perf_counter() - started

    assert elapsed < 6.0, f"public help timeout fallback took too long: {elapsed:.2f}s"
    assert native_help.returncode == 0
    assert "Usage:" in native_help.stdout
    assert "search" in native_help.stdout
    assert native_help.stderr.strip() == ""


def test_unknown_first_token_without_explicit_path_behaves_like_bare_search(parity_env):
    search_cwd = parity_env / "bare_token_cwd"
    search_cwd.mkdir()
    (search_cwd / "sample.txt").write_text("help\nother\n", encoding="utf-8")

    default_result = _run_native_front_door(["help"], cwd=search_cwd)
    early_rg_result = _run_native_front_door(
        ["help"],
        cwd=search_cwd,
        env={**os.environ, "TG_RUST_EARLY_POSITIONAL_RG": "1"},
    )

    for native_result in (default_result, early_rg_result):
        assert native_result.returncode == 0
        assert "Usage:" not in native_result.stdout
        assert "help" in native_result.stdout


def test_native_help_fallback_still_surfaces_moat_commands_when_python_passthrough_unavailable(
    parity_env,
):
    """Audit #97 item 1: test_empty_invocation_fallback_help_matches_public_contract already proves
    the clap fallback's auto-generated Commands: list has always contained every public command
    name (capability and maintenance alike) -- that part of the audit's "missing capability commands" framing
    does not hold against current code. What the fallback lacked was a curated, agent-oriented
    pointer to the flagship/capability commands positioned where an agent would actually see it, mirroring
    the Typer help's "AI workflows" section. This asserts that pointer exists and appears before the
    undifferentiated ~40-command wall, not buried after it."""
    if sys.platform == "win32":
        broken_python = str(shutil.which("powershell.exe"))
    else:
        broken_python = "/bin/sh"
    env = {**os.environ, "TG_SIDECAR_PYTHON": broken_python}

    native_help = _run_native_front_door(["--help"], cwd=parity_env, env=env)

    assert native_help.returncode == 0
    stdout = _strip_ansi(native_help.stdout)
    assert native_help.stderr.strip() == ""
    assert "Repository analysis commands" in stdout

    for moat_command in (
        "orient",
        "defs",
        "refs",
        "callers",
        "impact",
        "blast-radius",
        "map",
        "agent",
        "search",
        "mcp",
    ):
        assert moat_command in stdout, (
            f"Missing capability command {moat_command!r} in native fallback help"
        )

    moat_header_pos = stdout.index("Repository analysis commands")
    commands_list_pos = stdout.index("Commands:")
    assert moat_header_pos < commands_list_pos, (
        "the curated capability-commands pointer must appear before the auto-generated Commands: list "
        "so an agent that stops reading early still sees it"
    )


def test_help_probe_timeout_env_override_is_honored(parity_env):
    """Audit #97 item 1: TG_HELP_PROBE_TIMEOUT_MS must override the (now-3000ms) default, mirroring
    how TG_SIDECAR_TIMEOUT_MS overrides the general sidecar timeout. A short override must make the
    fallback trigger fast even against a wedged Python that never responds to --help, proving the
    env var is actually read rather than silently ignored (which would fall through to the raised
    3000ms default and take ~3s instead)."""
    if sys.platform == "win32":
        wrapper = parity_env / "wedged-python-help-probe-override.cmd"
        wrapper.write_text("@echo off\r\nping -n 9 127.0.0.1 >nul\r\n", encoding="utf-8")
    else:
        wrapper = parity_env / "wedged-python-help-probe-override.sh"
        wrapper.write_text("#!/bin/sh\nsleep 8\n", encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

    env_override = {
        **os.environ,
        "TG_SIDECAR_PYTHON": str(wrapper),
        "TG_HELP_PROBE_TIMEOUT_MS": "250",
    }
    env_default = {**os.environ, "TG_SIDECAR_PYTHON": str(wrapper)}
    env_default.pop("TG_HELP_PROBE_TIMEOUT_MS", None)
    if _get_native_binary() is None:
        _run_native_front_door(["--version"], cwd=parity_env)

    # Measure the 250ms override AND the 3000ms default back-to-back against the SAME wedged Python
    # under the SAME machine load, then compare the DELTA rather than an absolute wall-clock bound.
    # An absolute `elapsed < 3.0` is flaky under parallel-spawn contention: process-spawn overhead can
    # push a correctly-honored 250ms probe past 3s (false failure). The delta cancels shared
    # contention -- if the override is honored it MUST fall back markedly faster than the 3000ms
    # default, whatever the absolute machine load. (Wedged Python hangs ~8-9s, so the default probe
    # waits its full 3000ms before falling back; the override waits only ~250ms.)
    started = time.perf_counter()
    native_help = _run_native_front_door(["--help"], cwd=parity_env, env=env_override)
    override_elapsed = time.perf_counter() - started

    started = time.perf_counter()
    default_help = _run_native_front_door(["--help"], cwd=parity_env, env=env_default)
    default_elapsed = time.perf_counter() - started

    assert override_elapsed < default_elapsed - 1.0, (
        f"TG_HELP_PROBE_TIMEOUT_MS=250 override was not honored -- override fallback took "
        f"{override_elapsed:.2f}s vs the 3000ms-default {default_elapsed:.2f}s (delta "
        f"{default_elapsed - override_elapsed:.2f}s <= 1.0s margin), consistent with the 250ms "
        "override being ignored and the raised 3000ms default being used for both."
    )
    # Both paths must still fall back cleanly to the native help.
    for result in (native_help, default_help):
        assert result.returncode == 0
        assert "Usage:" in result.stdout
        assert "Repository analysis commands" in _strip_ansi(result.stdout)
        assert result.stderr.strip() == ""


A90_MATRIX = [
    # (args, expect_refusal, expect_known_users) -- expect_refusal: exit 2 w/ unknown_command.
    (["edit-ready", "--help"], True, None),
    (["edit-ready", "--json"], True, None),
    (["edit-ready", "--help", "--json"], True, None),
    (["verify-edit", "--json"], True, None),
    (["workspace", "--json"], True, None),
    (["hello", "--json"], False, None),
    (["hello", "target.txt", "--json"], False, None),
    (["edit-ready", "target.txt", "--json"], True, None),
    (["edit-ready", "target.txt"], False, None),
    (["qqq", "--json"], False, None),
    # A-04: rank order (edit distance, then name) -- closest first, THEN cap at 5. Recomputed
    # from the fixed `bootstrap._nearest_commands`; both doors must emit the identical list.
    (["qqq", "--help"], True, ["sql", "lsp", "map", "mcp", "new"]),
    (["ru", "--help"], True, ["run", "lsp", "map", "mcp", "new"]),
    (["sq", "--help"], True, ["sql", "lsp", "map", "mcp", "new"]),
    (["scn", "--help"], True, ["scan", "mcp", "run", "sql", "find"]),
    (["search", "workspace", "--json"], False, None),
    (["searhc", "--help"], True, ["search"]),
    (["qqqqzzzz", "--help"], True, []),
    (["--bogus", "--help"], False, None),
]


@pytest.mark.parametrize("args,expect_refusal,expected_nearest", A90_MATRIX)
def test_unknown_command_refusal_parity_across_launchers(
    parity_env, args, expect_refusal, expected_nearest
):
    """A90: the unknown-command refusal must be IDENTICAL across the python-m / native /
    bootstrap launchers. Flag-bearing reserved names refuse (exit 2, unknown_command, stderr);
    unreserved pattern+flag and reserved+positional stay search; `--help` on ANY unknown refuses
    (a nonexistent command has no help); `tg search workspace --json` (explicit command) stays
    search (escape hatch). Third column pins the EXACT nearest[] (via the human help line, which
    carries 'did you mean ...?' when non-empty and omits it when [])."""
    parsed_json_by_launcher = {}
    help_nearest_by_launcher = {}
    for launcher in LAUNCHERS:
        result = run_command(launcher, args, cwd=parity_env)
        if expect_refusal:
            assert result.returncode == 2, (
                f"{launcher} {args}: expected exit 2, got {result.returncode} stdout={result.stdout[:120]!r} stderr={result.stderr[:120]!r}"
            )
            assert result.stdout.strip() == "", (
                f"{launcher} {args}: refusal must keep stdout EMPTY, got {result.stdout[:120]!r}"
            )
            if "--json" in args and "--help" not in args:
                parsed = json.loads(result.stderr.strip().splitlines()[-1])
                assert parsed["error"]["code"] == "unknown_command", (
                    f"{launcher} {args}: wrong error code {parsed}"
                )
                assert isinstance(parsed["error"]["nearest"], list), parsed
                parsed_json_by_launcher[launcher] = parsed
            else:
                assert "unknown command" in result.stderr, (
                    f"{launcher} {args}: human diagnostic missing, stderr={result.stderr[:120]!r}"
                )
                # Exact help-form nearest parity: parse the complete 'did you mean' list and
                # compare EXACTLY (a substring assert would accept "search" matching
                # "search, serve"). Collect across launchers for cross-door equality below.
                if expected_nearest:
                    import re as _re

                    m = _re.search(r"\(did you mean (.+?)\?\)", result.stderr)
                    assert m is not None, (
                        f"{launcher} {args}: 'did you mean' missing, stderr={result.stderr[:160]!r}"
                    )
                    actual = [part.strip() for part in m.group(1).split(",")]
                    assert actual == expected_nearest, (
                        f"{launcher} {args}: exact nearest mismatch, actual={actual} "
                        f"expected={expected_nearest}"
                    )
                    help_nearest_by_launcher[launcher] = actual
                else:
                    assert "did you mean" not in result.stderr, (
                        f"{launcher} {args}: empty nearest must omit the suggestion, "
                        f"stderr={result.stderr[:160]!r}"
                    )
        else:
            # A search shape must NOT be refused as an unknown command. Legit search outcomes
            # (match exit 0/1, or a search-path/broad-scan error at exit 2) are fine — what is
            # forbidden is the unknown_command refusal shape. Assert its ABSENCE on both streams.
            combined = result.stdout + result.stderr
            assert "unknown_command" not in combined, (
                f"{launcher} {args}: search must not emit unknown_command "
                f"stdout={result.stdout[:120]!r} stderr={result.stderr[:120]!r}"
            )
            assert "unknown command" not in combined, (
                f"{launcher} {args}: search must not emit 'unknown command' "
                f"stdout={result.stdout[:120]!r} stderr={result.stderr[:120]!r}"
            )
    if parsed_json_by_launcher:
        # Cross-launcher parity: EVERY door must emit the SAME structured refusal (code + nearest).
        first = next(iter(parsed_json_by_launcher.values()))
        for launcher, parsed in parsed_json_by_launcher.items():
            assert parsed["error"]["code"] == first["error"]["code"], (
                f"{launcher} {args}: refusal code diverged {parsed} vs {first}"
            )
            assert parsed["error"]["nearest"] == first["error"]["nearest"], (
                f"{launcher} {args}: nearest[] diverged {parsed['error']['nearest']} vs "
                f"{first['error']['nearest']}"
            )
    if help_nearest_by_launcher:
        # Help-form cross-launcher nearest parity: every door must suggest the SAME list.
        first_help = next(iter(help_nearest_by_launcher.values()))
        for launcher, actual in help_nearest_by_launcher.items():
            assert actual == first_help, (
                f"{launcher} {args}: help-form nearest diverged {actual} vs {first_help}"
            )
