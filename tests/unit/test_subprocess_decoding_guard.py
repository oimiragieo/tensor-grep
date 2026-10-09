from __future__ import annotations

import ast
import importlib.util
import json
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_subprocess_decoding.py"
_spec = importlib.util.spec_from_file_location("tg_subprocess_decoding_guard", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = guard
_spec.loader.exec_module(guard)

PRODUCTION = guard.PRODUCTION
check_repository = guard.check_repository
forwarder_key = guard.forwarder_key
policy_for = guard.policy_for
scan_source = guard.scan_source
scan_tree = guard.scan_tree
violations = guard.violations


def test_fingerprints_normalize_only_empty_type_parameters() -> None:
    node = ast.parse("def launch():\n    pass\n").body[0]
    node._fields = tuple(name for name in node._fields if name != "type_params")
    without = guard._fingerprint(node)
    node._fields = (*node._fields, "type_params")
    node.type_params = []
    assert guard._fingerprint(node) == without
    node.type_params = [ast.Name(id="T", ctx=ast.Load())]
    assert guard._fingerprint(node) != without
    node.type_params = None
    assert guard._fingerprint(node) != without


def test_fingerprints_preserve_ordinary_empty_and_none_fields() -> None:
    node = ast.parse("def launch():\n    pass\n").body[0]
    before = guard._fingerprint(node)
    node.decorator_list = None
    assert guard._fingerprint(node) != before
    node.decorator_list = []
    assert guard._fingerprint(node) == before
    node.returns = []
    assert guard._fingerprint(node) != before


def test_fingerprints_ignore_field_order_and_locations_but_keep_list_order() -> None:
    node = ast.parse("def launch():\n    first()\n    second()\n").body[0]
    before = guard._fingerprint(node)
    node._fields = tuple(reversed(node._fields))
    assert guard._fingerprint(node) == before
    ast.increment_lineno(node, 5)
    assert guard._fingerprint(node) == before
    node.body.reverse()
    assert guard._fingerprint(node) != before


def test_guard_resolves_module_and_imported_sink_aliases() -> None:
    source = textwrap.dedent(
        """
        import subprocess as sp
        from subprocess import run as execute

        def first():
            call = sp.run
            return call(["tool"], capture_output=True, text=True)

        def second():
            return execute(["tool"], capture_output=True, text=True)
        """
    )

    rows = scan_source(source, "fixture.py")

    assert [(row.function, row.target) for row in rows] == [
        ("first", "subprocess.run"),
        ("second", "subprocess.run"),
    ]
    assert len(violations(rows)) == 2


def test_freshness_capture_wrapper_is_in_the_output_census() -> None:
    source = (
        "from tensor_grep.cli.freshness_process import capture_probe as capture\n"
        "def freshness():\n"
        "    return capture(['worker'], timeout_seconds=1)\n"
    )
    rows = scan_source(source, "fixture.py")
    assert len(rows) == 1
    assert rows[0].target == "capture_probe"
    assert rows[0].output_captured is True


@pytest.mark.parametrize(
    "options",
    [
        "text=True, encoding='utf-8'",
        "text=True, encoding='utf-8', errors='strict'",
        "encoding='utf-8'",
        "text=False, encoding='utf-8'",
        "universal_newlines=True, encoding='utf-8'",
        "encoding='utf-8', errors='strict'",
        "text=False, universal_newlines=False, encoding='utf-8', errors='strict'",
    ],
)
@pytest.mark.parametrize(
    ("path", "function"),
    [("fixture.py", "launch"), ("cli/audit_manifest.py", "_resolve_git_ref_commit_sha")],
)
def test_guard_rejects_strict_text_capture_before_windows_reader_threads(
    options: str, path: str, function: str
) -> None:
    source = (
        f"import subprocess\ndef {function}():\n"
        f"    return subprocess.run(['tool'], capture_output=True, {options})\n"
    )
    rows = scan_source(source, path)
    assert len(rows) == 1
    assert len(violations(rows, production=True, forwarder_allowlist={})) == 1
    policy, reason = policy_for(rows[0])
    assert policy == "unclassified" and "bytes" in reason
    byte_source = source.replace(options, "text=False")
    assert not violations(scan_source(byte_source, path), production=True, forwarder_allowlist={})


@pytest.mark.parametrize(
    "options",
    [
        "encoding='utf-8', errors='replace'",
        "text=False, encoding='utf-8', errors='replace'",
        "universal_newlines=True, encoding='utf-8', errors='replace'",
    ],
)
def test_guard_classifies_implicit_replacement_text_as_diagnostics(options: str) -> None:
    rows = scan_source(
        f"import subprocess\nsubprocess.run(['tool'], capture_output=True, {options})\n",
        "diagnostic.py",
    )
    assert len(rows) == 1
    assert policy_for(rows[0])[0] == "diagnostic-utf8-replace"
    assert not violations(rows, production=True, forwarder_allowlist={})


def test_guard_resolves_aliases_at_each_call_before_rebinding() -> None:
    source = textwrap.dedent(
        """
        import subprocess as sp

        def launch():
            call = sp.run
            before = call(["tool"], capture_output=True, text=True)
            call = print
            after = call(["not-a-process"])
            return before, after
        """
    )
    rows = scan_source(source, "rebound.py")
    assert [(row.line, row.target) for row in rows] == [(6, "subprocess.run")]


def test_guard_fails_closed_when_branch_rebinding_makes_sink_ambiguous() -> None:
    source = textwrap.dedent(
        """
        import subprocess as sp

        def launch(condition):
            call = sp.run
            if condition:
                call = print
            return call(["tool"], text=True)
        """
    )
    rows = scan_source(source, "branch.py")
    assert len(rows) == 1
    assert rows[0].target == "unresolved-subprocess-alias"
    assert any("ambiguous rebinding" in item for item in violations(rows))


def test_guard_rejects_universal_newlines_and_implicit_error_decoding() -> None:
    source = textwrap.dedent(
        """
        import subprocess
        subprocess.run(["a"], capture_output=True, universal_newlines=True)
        subprocess.run(["b"], capture_output=True, text=False, errors="replace")
        subprocess.run(["c"], capture_output=True, text=TEXT, encoding="codec")
        """
    )
    rows = scan_source(source, "decoder_flags.py")
    problems = violations(rows)
    assert len(problems) == 3
    assert all("UTF-8 encoding" in item for item in problems)


@pytest.mark.parametrize("api", ["getoutput", "getstatusoutput"])
def test_implicit_text_capture_apis_require_explicit_utf8(api: str) -> None:
    rows = scan_source(f"import subprocess\nsubprocess.{api}('tool')\n", "implicit_text.py")
    assert len(rows) == 1
    assert rows[0].output_captured is True
    assert rows[0].text == "True"
    assert any("requires explicit UTF-8 encoding" in problem for problem in violations(rows))
    explicit = scan_source(
        f"import subprocess\nsubprocess.{api}('tool', encoding='utf-8', errors='replace')\n",
        "explicit_text.py",
    )
    assert not violations(explicit)


def test_check_output_records_implicit_bytes_capture() -> None:
    rows = scan_source(
        "import subprocess\nsubprocess.check_output(['tool'])\n", "implicit_bytes.py"
    )
    assert len(rows) == 1
    assert rows[0].output_captured is True
    assert not violations(rows)


@pytest.mark.parametrize("api", ["Popen", "run", "call", "check_call", "check_output"])
@pytest.mark.parametrize(
    "arguments",
    ["['tool'], -1, None, None, None, None, None, True, False, None, None, True", "*options"],
)
def test_guard_rejects_positional_options_that_can_hide_decoding(api: str, arguments: str) -> None:
    rows = scan_source(
        f"from subprocess import {api} as launch\nlaunch({arguments})\n", "positional.py"
    )
    assert len(rows) == 1
    assert policy_for(rows[0])[0] == "unclassified"
    assert any(
        "positional options or *args are unsupported" in item
        for item in violations(rows, production=True, forwarder_allowlist={})
    )
    positive = scan_source(
        f"from subprocess import {api} as launch\nlaunch(args=['tool'], stdout=-1)\n",
        "positional.py",
    )
    assert not violations(positive, production=True, forwarder_allowlist={})


def test_positional_options_cannot_borrow_an_exact_forwarder_exception() -> None:
    rows = scan_source("import subprocess\nsubprocess.run(*args, **kwargs)\n", "forwarder.py")
    key = forwarder_key(rows[0])
    assert key is not None
    allowlist = {key: "test-owned keyword forwarding only"}
    assert policy_for(rows[0], allowlist)[0] == "unclassified"
    assert any(
        "positional options or *args are unsupported" in item
        for item in violations(rows, production=True, forwarder_allowlist=allowlist)
    )


def test_generated_helpers_and_timeout_wrappers_reject_starred_options() -> None:
    source = (
        "import subprocess\n"
        "from tensor_grep.cli.subprocess_policy import run_subprocess\n"
        "run_subprocess(*options)\n"
        "code = 'import subprocess; subprocess.check_output(*options)'\n"
        "subprocess.Popen(['python', '-c', code])\n"
    )
    rows = scan_source(source, "generated_positional.py")
    rejected = [row for row in rows if row.positional_options]
    assert len(rejected) == 2
    assert sum(row.generated_from is not None for row in rejected) == 1
    assert all(policy_for(row)[0] == "unclassified" for row in rejected)
    assert len(violations(rows, production=True, forwarder_allowlist={})) == 4


def test_guard_scans_named_python_argv_aliases_and_utf8_flag_form() -> None:
    source = textwrap.dedent(
        """
        import subprocess as sp
        import sys

        def start():
            code = "import subprocess; subprocess.run(['inner'], text=True)"
            command = [sys.executable, "-X", "utf8", "-c", code]
            launch = sp.Popen
            return launch(args=command)
        """
    )
    rows = scan_source(source, "argv.py")
    generated = [row for row in rows if "<-c:" in row.path]
    assert len(generated) == 1
    assert generated[0].text == "True"
    assert any("UTF-8 encoding" in item for item in violations(rows))


def test_guard_scans_python_argv_built_by_append_and_extend() -> None:
    source = textwrap.dedent(
        """
        import subprocess
        import sys

        def start():
            code = "import subprocess; subprocess.run(['inner'], text=True)"
            command = [sys.executable]
            command.extend(["-X", "utf8"])
            command.append("-c")
            command.append(code)
            return subprocess.Popen(command)
        """
    )
    rows = scan_source(source, "argv_mutation.py")
    assert len([row for row in rows if "<-c:" in row.path]) == 1
    assert any("requires explicit UTF-8 encoding" in item for item in violations(rows))


def test_guard_follows_run_subprocess_dynamic_forwarding_and_fails_closed() -> None:
    source = textwrap.dedent(
        """
        import subprocess

        def wrapper(**opts):
            return subprocess.run(["tool"], **opts)
        """
    )

    rows = scan_source(source, "unknown_wrapper.py")

    assert len(rows) == 1
    assert rows[0].dynamic_kwargs == ("opts",)
    assert "unresolved subprocess **kwargs" in violations(rows)[0]


def test_guard_censuses_literal_generated_python_and_checks_its_decoder() -> None:
    source = textwrap.dedent(
        '''
        import subprocess
        import sys
        import textwrap

        def launch():
            helper = textwrap.dedent("""
                import subprocess
                subprocess.run(["child"], capture_output=True, text=True)
            """).strip()
            return subprocess.Popen([sys.executable, "-c", helper])
        '''
    )

    rows = scan_source(source, "generated.py")

    inner = [row for row in rows if "<-c:" in row.path]
    assert len(inner) == 1
    assert inner[0].target == "subprocess.run"
    assert "requires explicit UTF-8 encoding" in violations(rows)[0]


def test_guard_fails_closed_on_dynamic_generated_python() -> None:
    source = textwrap.dedent(
        """
        import subprocess
        import sys

        def launch(code):
            return subprocess.Popen([sys.executable, "-c", code])
        """
    )

    rows = scan_source(source, "dynamic.py")

    assert any(row.target == "generated-python-unresolved" for row in rows)
    assert any("generated -c source is unresolved or invalid" in item for item in violations(rows))


def test_production_census_and_all_embedded_helpers_are_guarded() -> None:
    rows = scan_tree(PRODUCTION)
    assert len(rows) == 79
    assert not check_repository(rows)
    generated = [row for row in rows if row.generated_from is not None]
    assert len(generated) == 12
    assert sum(row.path.startswith("cli/main.py::<-c:") for row in generated) == 10
    assert sum(row.path.startswith("cli/windows_launcher.py::<-c:") for row in generated) == 2


def test_production_manifest_detects_same_count_sink_swap() -> None:
    rows = scan_tree(PRODUCTION)
    altered = replace(rows[-1], fingerprint="different-call-at-the-same-population-size")
    swapped = [*rows[:-1], altered]
    problems = check_repository(swapped)
    assert any("identity/fingerprint manifest changed" in item for item in problems)


def test_production_manifest_and_forwarders_accept_location_only_changes() -> None:
    rows = scan_tree(PRODUCTION)
    relocated = [replace(row, line=row.line + 5, column=row.column + 4) for row in rows]
    assert not check_repository(relocated)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "policy",
        "rationale",
        "missing-fields",
        "extra-field",
        "bad-location",
        "bad-flag-type",
        "not-list",
    ],
)
def test_strict_manifest_rejects_invalid_inventory_structure_and_classification(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rows = scan_tree(PRODUCTION)
    entries = json.loads(
        (PRODUCTION.parents[1] / "docs/subprocess-output-policy-inventory.json").read_text(
            encoding="utf-8"
        )
    )
    if mutation == "duplicate":
        entries.append(entries[0])
    elif mutation == "policy":
        entries[0]["policy"] = "arbitrary-incorrect-policy"
    elif mutation == "rationale":
        entries[0]["rationale"] = "unverified rationale"
    elif mutation == "missing-fields":
        entries[0].pop("policy")
        entries[0].pop("rationale")
    elif mutation == "extra-field":
        entries[0]["unreviewed"] = True
    elif mutation == "bad-location":
        entries[0]["line"] = None
    elif mutation == "bad-flag-type":
        entries[0]["output_captured"] = int(entries[0]["output_captured"])
    else:
        entries = {"entries": entries}
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/subprocess-output-policy-inventory.json").write_text(
        json.dumps(entries), encoding="utf-8"
    )
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    assert any("manifest" in problem for problem in check_repository(rows))


def test_named_argv_alias_mutation_keeps_the_generated_payload_visible() -> None:
    rows = scan_source(
        textwrap.dedent("""
        import subprocess
        import sys
        original = [sys.executable]
        command = original
        original.extend(["-c", "import subprocess; subprocess.run(['inner'], text=True)"])
        subprocess.Popen(command)
    """),
        "argv_alias_mutation.py",
    )
    assert any(row.generated_from is not None for row in rows)
    assert any("requires explicit UTF-8 encoding" in item for item in violations(rows))


def test_subscript_mutation_does_not_parse_a_stale_safe_generated_payload() -> None:
    rows = scan_source(
        textwrap.dedent("""
        import subprocess
        command = ["python", "-c", "print('safe')"]
        command[-1] = dynamic_code
        subprocess.Popen(command)
    """),
        "argv_subscript.py",
    )
    assert any("generated -c source is unresolved or invalid" in item for item in violations(rows))


def test_known_python_c_without_a_payload_is_rejected() -> None:
    rows = scan_source("import subprocess\nsubprocess.Popen(['python', '-c'])\n", "empty_c.py")
    assert any("generated -c source is unresolved or invalid" in item for item in violations(rows))


def test_unresolved_python_option_cannot_hide_an_earlier_generated_payload() -> None:
    rows = scan_source(
        textwrap.dedent("""
        import subprocess
        subprocess.Popen(["python", dynamic_option,
                          "import subprocess; subprocess.run(['inner'], text=True)",
                          "-c", "print('safe')"])
    """),
        "unknown_python_option.py",
    )
    assert any(
        "generated -c source is unresolved or invalid" in problem for problem in violations(rows)
    )


def test_git_c_configuration_option_is_not_generated_python() -> None:
    rows = scan_source(
        textwrap.dedent("""
        import subprocess
        command = ["git", "-c", "core.quotepath=false"] + ["diff"]
        if staged:
            command.append("--cached")
        subprocess.Popen(command)
    """),
        "git_options.py",
    )
    assert len(rows) == 1
    assert rows[0].target == "subprocess.Popen"
    assert not violations(rows)


@pytest.mark.parametrize(
    "executable", ['"python3"', '"C:/Python/python.exe"', "sys.executable", "dynamic_executable"]
)
def test_possible_python_executables_keep_dynamic_c_payloads_fail_closed(executable: str) -> None:
    rows = scan_source(
        f"import subprocess\nimport sys\nsubprocess.Popen([{executable}, '-c', dynamic_code])\n",
        "python_names.py",
    )
    assert any("generated -c source is unresolved or invalid" in item for item in violations(rows))


def test_finally_sees_possible_alias_before_a_later_try_rebinding() -> None:
    rows = scan_source(
        textwrap.dedent("""
        import subprocess
        try:
            execute = subprocess.run
            risky()
            execute = print
        finally:
            execute(['tool'], text=True)
    """),
        "finally_prefix.py",
    )
    assert any("ambiguous rebinding" in item for item in violations(rows))


@pytest.mark.parametrize(
    "body",
    [
        "if condition:\n    sp = print\nsp.run(['tool'], text=True)\n",
        "if condition:\n    sp = print\nother = sp\nother.run(['tool'], text=True)\n",
        "execute = sp.run\ntry:\n    risky()\nexcept Exception:\n    execute = print\n"
        "execute(['tool'], text=True)\n",
        "try:\n    execute = sp.run\nfinally:\n    execute(['tool'], text=True)\n",
        "try:\n    execute = sp.run\n    risky()\n    execute = print\n"
        "except Exception:\n    execute(['tool'], text=True)\n",
        "try:\n    sp = subprocess\n    risky()\nexcept Exception:\n"
        "    sp.run(['tool'], text=True)\n",
    ],
)
def test_guard_keeps_possible_launches_across_branches_and_exception_paths(body: str) -> None:
    rows = scan_source("import subprocess\nimport subprocess as sp\n" + body, "control_flow.py")
    assert rows, "a possible subprocess launch must not disappear from the census"
    assert any(
        "ambiguous rebinding" in problem or "requires explicit UTF-8 encoding" in problem
        for problem in violations(rows, forwarder_allowlist={})
    )


def test_guard_scans_try_else_using_the_normal_exit_bindings() -> None:
    rows = scan_source(
        "import subprocess\ntry:\n    execute = subprocess.run\nexcept Exception:\n"
        "    pass\nelse:\n    execute(['tool'], text=True)\n",
        "try_else.py",
    )
    assert any("requires explicit UTF-8 encoding" in problem for problem in violations(rows))


def test_finally_sees_alias_before_an_exception_in_try_else() -> None:
    rows = scan_source(
        textwrap.dedent("""
        import subprocess as sp
        try:
            pass
        except Exception:
            pass
        else:
            execute = sp.run
            risky()
            execute = print
        finally:
            execute(['tool'], text=True)
    """),
        "try_else_finally.py",
    )
    assert len(rows) == 1
    assert rows[0].function == "<module>"
    assert any("ambiguous rebinding" in problem for problem in violations(rows))


def test_outer_handler_sees_alias_before_nested_try_else_exception() -> None:
    rows = scan_source(
        textwrap.dedent("""
        import subprocess as sp
        try:
            try:
                pass
            except Exception:
                pass
            else:
                execute = sp.run
                risky()
                execute = print
        except Exception:
            execute(['tool'], text=True)
    """),
        "try_else_outer_handler.py",
    )
    assert len(rows) == 1
    assert any("ambiguous rebinding" in problem for problem in violations(rows))


def test_try_else_exception_states_do_not_feed_the_same_try_handlers() -> None:
    rows = scan_source(
        textwrap.dedent("""
        import subprocess as sp
        execute = print
        try:
            risky()
        except Exception:
            execute(['tool'], text=True)
        else:
            execute = sp.run
            risky()
            execute = print
    """),
        "try_else_handler_boundary.py",
    )
    assert rows == []


@pytest.mark.parametrize(
    "construction",
    [
        "launch = _self.subprocess.run\nif condition:\n    launch = print\n",
        "launch = _self.subprocess.run\ntry:\n    risky()\nexcept Exception:\n    launch = print\n",
        "launch = _self.subprocess.run\nif condition:\n    launch = print\nother = launch\nlaunch = other\n",
    ],
)
def test_injected_callable_aliases_remain_fail_closed_after_joins(construction: str) -> None:
    rows = scan_source(construction + "launch(['tool'], text=True)\n", "injected_alias.py")
    assert len(rows) == 1
    assert any("ambiguous rebinding" in problem for problem in violations(rows))


def test_simple_injected_module_alias_is_censused() -> None:
    rows = scan_source("sp = _self.subprocess\nsp.run(['tool'], text=True)\n", "injected_module.py")
    assert len(rows) == 1
    assert any("requires explicit UTF-8 encoding" in problem for problem in violations(rows))


@pytest.mark.parametrize(
    "module_import",
    [
        "from tensor_grep.cli import subprocess_policy as policy",
        "import tensor_grep.cli.subprocess_policy as policy",
    ],
)
def test_timeout_wrapper_module_import_alias_is_censused(module_import: str) -> None:
    rows = scan_source(
        module_import + "\npolicy.run_subprocess(['tool'], text=True)\n", "wrapper_module.py"
    )
    assert len(rows) == 1
    assert rows[0].target == "run_subprocess"
    assert any("requires explicit UTF-8 encoding" in problem for problem in violations(rows))


def test_timeout_wrapper_module_alias_join_remains_fail_closed() -> None:
    rows = scan_source(
        "from tensor_grep.cli import subprocess_policy as policy\n"
        "if condition:\n    policy = print\n"
        "policy.run_subprocess(['tool'], text=True)\n",
        "wrapper_module_join.py",
    )
    assert len(rows) == 1
    assert any("ambiguous rebinding" in problem for problem in violations(rows))


@pytest.mark.parametrize(
    "addition",
    [
        repr("\nimport subprocess; subprocess.run(['inner'], text=True)"),
        "dynamic_generated_source",
    ],
)
def test_generated_scalar_augmentation_cannot_reuse_a_stale_safe_payload(addition: str) -> None:
    rows = scan_source(
        "import subprocess\ncode = \"print('safe')\"\ncode += "
        + addition
        + "\nsubprocess.Popen(['python', '-c', code])\n",
        "augmented_helper.py",
    )
    assert any(
        "requires explicit UTF-8 encoding" in problem
        or "generated -c source is unresolved or invalid" in problem
        for problem in violations(rows)
    )


@pytest.mark.parametrize(
    "construction",
    [
        "if condition:\n    command = [sys.executable, '-c', code]\nelse:\n"
        "    command = [sys.executable, '--version']\n",
        "if condition:\n    command = [sys.executable, '-c', code]\nelse:\n"
        "    command = [sys.executable, '-X', 'utf8', '-c', code]\n",
        "original = [sys.executable, '-c', code]\ncommand = original\n",
        "command = [sys.executable, '-c', code]\ncommand.extend(dynamic_options)\n",
        "command = [sys.executable, '-c', code]\ncommand += dynamic_options\n",
        "command = [sys.executable, '-c', code]\ncommand = build_dynamic_argv()\n",
        "command = [sys.executable, '-c', code]\ncommand[-1] = dynamic_code\n",
    ],
)
def test_guard_never_loses_a_known_generated_launch_when_argv_becomes_uncertain(
    construction: str,
) -> None:
    source = (
        "import subprocess\nimport sys\n"
        "code = \"import subprocess; subprocess.run(['inner'], text=True)\"\n"
        + construction
        + "subprocess.Popen(command)\n"
    )
    problems = violations(scan_source(source, "uncertain_argv.py"), forwarder_allowlist={})
    assert any(
        "generated -c source is unresolved or invalid" in problem
        or "requires explicit UTF-8 encoding" in problem
        for problem in problems
    )


def test_guard_does_not_invent_generated_code_after_a_definite_argv_overwrite() -> None:
    source = (
        "import subprocess\nimport sys\ncommand = [sys.executable, '-c', code]\n"
        "command = [sys.executable, '--version']\nsubprocess.Popen(command)\n"
    )
    rows = scan_source(source, "overwritten_argv.py")
    assert [row.target for row in rows] == ["subprocess.Popen"]
    assert not violations(rows, forwarder_allowlist={})


def test_exact_forwarder_allowance_cannot_be_reused_for_an_added_sink() -> None:
    rows = scan_tree(PRODUCTION)
    forwarder = next(row for row in rows if row.dynamic_kwargs)
    key = forwarder_key(forwarder)
    assert key is not None
    injected_allowlist = {key: "test-owned exact forwarding boundary"}
    assert any(
        "forwarder exception consumed 2 times" in item
        for item in violations(
            [forwarder, forwarder], production=True, forwarder_allowlist=injected_allowlist
        )
    )
    assert any(
        "unused exact forwarder exception" in item
        for item in violations([], production=True, forwarder_allowlist=injected_allowlist)
    )


@pytest.mark.parametrize("field", ["fingerprint", "producer_fingerprint", "dynamic_kwargs"])
def test_exact_forwarder_allowance_rejects_changed_call_provenance(field: str) -> None:
    source = (
        "import subprocess\ndef wrapper(**opts):\n    return subprocess.run(['tool'], **opts)\n"
    )
    original = scan_source(source, "forwarder_fixture.py")[0]
    key = forwarder_key(original)
    assert key is not None
    allowlist = {key: "test-owned exact forwarding boundary"}
    assert not violations([original], production=True, forwarder_allowlist=allowlist)
    change = ("other_options",) if field == "dynamic_kwargs" else "changed-ast"
    changed = replace(original, **{field: change})
    problems = violations([changed], production=True, forwarder_allowlist=allowlist)
    assert any("unresolved subprocess **kwargs" in item for item in problems)
    assert any("unused exact forwarder exception" in item for item in problems)


def test_actual_source_relocation_preserves_exact_forwarder_fingerprints() -> None:
    source = (
        "import subprocess\ndef wrapper(**opts):\n    return subprocess.run(['tool'], **opts)\n"
    )
    original = scan_source(source, "forwarder_fixture.py")[0]
    relocated = scan_source("\n" * 5 + source, "forwarder_fixture.py")[0]
    assert relocated.line == original.line + 5
    assert forwarder_key(relocated) == forwarder_key(original)
