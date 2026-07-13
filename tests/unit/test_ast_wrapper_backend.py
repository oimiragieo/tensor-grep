import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend
from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.config import SearchConfig


def test_raise_for_nonzero_raises_on_truncated_json_from_killed_subprocess():
    """Audit HIGH: a killed/OOM'd sg subprocess emits TRUNCATED JSON that still starts
    with '[' with empty stderr. The old waiver (``stdout.startswith('[')``) masked that as
    a clean 0-match scan; the later json.loads then failed and was swallowed downstream. A
    FULL parse must be required before waiving the nonzero exit."""
    backend = AstGrepWrapperBackend()
    result = subprocess.CompletedProcess(
        args=["sg", "scan"],
        returncode=137,  # 128 + SIGKILL(9): OOM-killer / container memory limit
        stdout='[{"file": "a.py", "range"',  # truncated, invalid JSON
        stderr="",
    )
    with pytest.raises(BackendExecutionError):
        backend._raise_for_nonzero(result)


def test_raise_for_nonzero_waives_complete_json_with_nonzero_exit():
    """A COMPLETE JSON payload with a nonzero exit and no stderr is a real (if quirky)
    result and must still be waived — the fix must not over-raise on valid output."""
    backend = AstGrepWrapperBackend()
    result = subprocess.CompletedProcess(
        args=["sg", "scan"],
        returncode=1,
        stdout="[]",
        stderr="",
    )
    backend._raise_for_nonzero(result)  # must not raise


def test_ast_wrapper_backend_should_use_resolved_binary_path():
    """#130(b): the primary `ast-grep` which()-resolution now also probe-runs
    (see test_ast_grep_backend_probes_primary_name_before_trusting_it below),
    so this must mock subprocess.run to a passing probe -- previously this
    test relied on which() alone and would have gone environment-dependent
    (silently trusting whatever real binary a dev machine happened to have at
    this literal path) instead of exercising the mocked contract."""
    backend = AstGrepWrapperBackend()
    mock_result = MagicMock()
    mock_result.returncode = 0  # a working `ast-grep --version` exits 0 (#90b probe gate)
    mock_result.stdout = "ast-grep 0.39.5\n"
    mock_result.stderr = ""

    with (
        patch("shutil.which") as which,
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        which.side_effect = lambda name: {
            "ast-grep": r"C:\Users\oimir\AppData\Roaming\npm\ast-grep.CMD",
            "ast-grep.exe": None,
            "sg": None,
        }.get(name)

        assert backend._get_binary_name() == r"C:\Users\oimir\AppData\Roaming\npm\ast-grep.CMD"


def test_ast_wrapper_backend_should_ignore_linux_group_sg_binary():
    backend = AstGrepWrapperBackend()
    mock_result = MagicMock()
    mock_result.returncode = 0  # exit 0 -> the rejection must come from the missing marker, not the exit code
    mock_result.stdout = "sg from util-linux 2.39\n"
    mock_result.stderr = ""

    with (
        patch("shutil.which") as which,
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        which.side_effect = lambda name: {
            "ast-grep": None,
            "ast-grep.exe": None,
            "sg.exe": None,
            "sg": "/usr/bin/sg",
        }.get(name)

        assert backend.is_available() is False
        assert backend._get_binary_name() == "ast-grep"


def test_ast_wrapper_backend_should_accept_verified_sg_alias():
    backend = AstGrepWrapperBackend()
    mock_result = MagicMock()
    mock_result.returncode = 0  # a working `sg --version` exits 0 (#90b probe gate)
    mock_result.stdout = "ast-grep 0.39.5\n"
    mock_result.stderr = ""

    with (
        patch("shutil.which") as which,
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        which.side_effect = lambda name: {
            "ast-grep": None,
            "ast-grep.exe": None,
            "sg.exe": None,
            "sg": "/opt/bin/sg",
        }.get(name)

        assert backend.is_available() is True
        assert backend._get_binary_name() == "/opt/bin/sg"


def test_ast_grep_backend_probes_primary_name_before_trusting_it():
    """#130(b): doctor/runtime must not trust a which()-resolved `ast-grep` on
    name alone. A broken npm shim literally named `ast-grep` resolves via
    shutil.which() but is not runnable (e.g. a Windows shim invoked under
    WSL/Linux: execve() on a non-native binary format raises OSError, exactly
    what a real interpreter-less exec failure surfaces as). The primary two
    which()-only branches must be probe-gated exactly like the existing
    sg/sg.exe branches already are -- an unprobed resolution must not be
    trusted."""
    backend = AstGrepWrapperBackend()

    with (
        patch("shutil.which") as which,
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            side_effect=OSError("[Errno 8] Exec format error"),
        ),
    ):
        which.side_effect = lambda name: {
            "ast-grep": "/fake/path/ast-grep",
            "ast-grep.exe": None,
            "sg.exe": None,
            "sg": None,
        }.get(name)

        assert backend.is_available() is False
        assert backend._get_binary_name() == "ast-grep"


def test_ast_grep_backend_still_trusts_working_binary():
    """Regression guard for the probe-gate fix above: a genuinely working
    `ast-grep` binary (which() resolves it AND --version confirms it) must
    still be trusted -- the fix must not over-reject a real install."""
    backend = AstGrepWrapperBackend()
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "ast-grep 0.39.5\n"
    mock_result.stderr = ""

    with (
        patch("shutil.which") as which,
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        which.side_effect = lambda name: {
            "ast-grep": "/real/path/ast-grep",
            "ast-grep.exe": None,
            "sg.exe": None,
            "sg": None,
        }.get(name)

        assert backend.is_available() is True
        assert backend._get_binary_name() == "/real/path/ast-grep"


def test_ast_grep_backend_rejects_shim_that_exits_nonzero_with_marker():
    """#90(b): a broken shim that RUNS (no OSError) but exits NON-ZERO -- e.g. a
    Windows `ast-grep.exe` invoked under WSL that exits 127 -- whose error output
    still contains "ast-grep" must NOT be trusted. The probe requires exit 0, not
    just the marker, so is_available() (and `tg doctor`) stays honest/fail-closed.
    Without the returncode==0 gate this shim would falsely resolve as available."""
    backend = AstGrepWrapperBackend()
    mock_result = MagicMock()
    mock_result.returncode = 127
    mock_result.stdout = ""
    mock_result.stderr = "ast-grep: command not found\n"

    with (
        patch("shutil.which") as which,
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        which.side_effect = lambda name: {
            "ast-grep": "/broken/shim/ast-grep",
            "ast-grep.exe": None,
            "sg.exe": None,
            "sg": None,
        }.get(name)

        assert backend.is_available() is False
        assert backend._get_binary_name() == "ast-grep"


def test_ast_grep_backend_memoizes_binary_probe():
    backend = AstGrepWrapperBackend()
    probe_calls: list[str] = []

    def record_probe(binary: str) -> bool:
        probe_calls.append(binary)
        return True

    with (
        patch("shutil.which", side_effect=lambda name: "/real/path/ast-grep"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend._is_ast_grep_sg_binary",
            side_effect=record_probe,
        ),
    ):
        assert backend._get_binary_name() == "/real/path/ast-grep"
        assert backend._get_binary_name() == "/real/path/ast-grep"

    assert probe_calls == ["/real/path/ast-grep"]


def test_doctor_ast_grep_available_false_when_shim_broken():
    """#130(b): `tg doctor`'s ast_grep status delegates to
    AstGrepWrapperBackend, so the probe-gate fix must be visible through
    _doctor_ast_grep_status() too -- a broken shim must surface
    available:false, not available:true."""
    from tensor_grep.cli.main import _doctor_ast_grep_status

    with (
        patch("shutil.which") as which,
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            side_effect=OSError("[Errno 8] Exec format error"),
        ),
    ):
        which.side_effect = lambda name: {
            "ast-grep": "/fake/path/ast-grep",
            "ast-grep.exe": None,
            "sg.exe": None,
            "sg": None,
        }.get(name)

        payload = {"ast_grep": _doctor_ast_grep_status()}

    assert payload["ast_grep"]["available"] is False


def test_ast_wrapper_backend_should_emit_runtime_routing_metadata():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = (
        '[{"text":"def hello():","range":{"start":{"line":0}}},'
        '{"text":"def world():","range":{"start":{"line":4}}}]'
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search(
            "example.py",
            "function_definition",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert result.total_matches == 2
    assert result.routing_backend == "AstGrepWrapperBackend"
    assert result.routing_reason == "ast_grep_json"
    assert result.routing_distributed is False
    assert result.routing_worker_count == 1


def test_ast_wrapper_backend_should_honor_max_count_on_search():
    """H6: --ast ... --max-count N must cap the returned matches, matching the
    per-file cap semantics cpu_backend/rust already apply, instead of returning
    every structural match ast-grep found."""
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = json.dumps([
        {"text": "def a():", "range": {"start": {"line": 0}}},
        {"text": "def b():", "range": {"start": {"line": 1}}},
        {"text": "def c():", "range": {"start": {"line": 2}}},
    ])

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search(
            "example.py",
            "function_definition",
            config=SearchConfig(ast=True, lang="python", max_count=2),
        )

    assert result.total_matches == 2
    assert [m.line_number for m in result.matches] == [1, 2]


def test_ast_wrapper_backend_should_honor_max_count_on_search_many():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = json.dumps([
        {"text": "def hello():", "file": "a.py", "range": {"start": {"line": 0}}},
        {"text": "class World:", "file": "b.py", "range": {"start": {"line": 4}}},
        {"text": "def again():", "file": "a.py", "range": {"start": {"line": 8}}},
    ])

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search_many(
            ["a.py", "b.py"],
            "function_definition",
            config=SearchConfig(ast=True, lang="python", max_count=1),
        )

    assert result.total_matches == 1


def test_ast_wrapper_backend_should_batch_many_files():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = (
        '[{"text":"def hello():","file":"a.py","range":{"start":{"line":0}}},'
        '{"text":"class World:","file":"b.py","range":{"start":{"line":4}}}]'
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(
            backend,
            "_get_binary_name",
            return_value=r"C:\\Users\\oimir\\AppData\\Roaming\\npm\\ast-grep.CMD",
        ),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        result = backend.search_many(
            ["a.py", "b.py"],
            "function_definition",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert run.call_args.args[0][-2:] == ["a.py", "b.py"]
    assert result.total_matches == 2
    assert result.total_files == 2
    assert result.matched_file_paths == ["a.py", "b.py"]


def test_ast_wrapper_backend_should_forward_ast_grep_run_semantic_options():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search_many(
            ["src"],
            "print($A)",
            config=SearchConfig(
                ast=True,
                lang="python",
                ast_selector="call",
                ast_strictness="relaxed",
                glob=["*.py", "!generated/**"],
            ),
        )

    assert run.call_args.args[0] == [
        "sg",
        "run",
        "--json",
        "-p",
        "print($A)",
        "--lang",
        "python",
        "--selector",
        "call",
        "--strictness",
        "relaxed",
        "--globs",
        "*.py",
        "--globs",
        "!generated/**",
        "--",
        "src",
    ]


def test_ast_wrapper_backend_should_forward_stdin_to_ast_grep_run(monkeypatch):
    backend = AstGrepWrapperBackend()
    monkeypatch.delenv("TG_AST_GREP_TIMEOUT_SECONDS", raising=False)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search_many(
            [],
            "print($A)",
            config=SearchConfig(
                ast=True,
                lang="python",
                ast_stdin=True,
                ast_stdin_input="print('hello')\n",
            ),
        )

    assert run.call_args.args[0] == [
        "sg",
        "run",
        "--json",
        "-p",
        "print($A)",
        "--lang",
        "python",
        "--stdin",
    ]
    assert run.call_args.kwargs["input"] == "print('hello')\n"
    assert run.call_args.kwargs["timeout"] == 60.0


def test_ast_wrapper_backend_should_allow_timeout_env_override(monkeypatch):
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""
    monkeypatch.setenv("TG_AST_GREP_TIMEOUT_SECONDS", "2.5")

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search_many(
            ["src"],
            "print($A)",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert run.call_args.kwargs["timeout"] == 2.5


def test_ast_wrapper_backend_should_surface_ast_grep_timeout():
    backend = AstGrepWrapperBackend()

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["sg"], timeout=60),
        ),
        pytest.raises(RuntimeError, match="ast-grep command timed out after 60s"),
    ):
        backend.search_many(
            ["src"],
            "print($A)",
            config=SearchConfig(ast=True, lang="python"),
        )


def test_ast_wrapper_backend_should_treat_empty_json_nonzero_as_no_match():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search_many(
            ["src"],
            "print($A)",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert result.total_matches == 0


def test_ast_wrapper_backend_should_reject_multiline_semantic_run_options():
    backend = AstGrepWrapperBackend()

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        pytest.raises(RuntimeError, match="multiline"),
    ):
        backend.search_many(
            ["src"],
            "def $NAME():\n    $$$BODY",
            config=SearchConfig(
                ast=True,
                lang="python",
                ast_selector="function_definition",
            ),
        )


def test_ast_wrapper_backend_should_use_rule_file_for_multiline_patterns():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = "[]"

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search(
            "example.py",
            "def $FUNC():\n    $$$BODY",
            config=SearchConfig(ast=True, lang="python"),
        )

    cmd = run.call_args.args[0]
    assert cmd[:4] == ["sg", "scan", "--json", "--rule"]
    assert cmd[-1] == "example.py"


def test_ast_wrapper_backend_should_preserve_range_and_meta_variables():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = (
        '[{"text":"def hello(name):",'
        '"file":"example.py",'
        '"range":{"byteOffset":{"start":0,"end":16},'
        '"start":{"line":0,"column":0},'
        '"end":{"line":0,"column":16}},'
        '"metaVariables":{"single":{"F":{"text":"hello"}},"multi":{"ARGS":[{"text":"name"}]}}}]'
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search(
            "example.py",
            "def $F($$$ARGS):",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert result.total_matches == 1
    assert result.matches[0].range == {
        "byteOffset": {"start": 0, "end": 16},
        "start": {"line": 0, "column": 0},
        "end": {"line": 0, "column": 16},
    }
    assert result.matches[0].meta_variables == {
        "single": {"F": {"text": "hello"}},
        "multi": {"ARGS": [{"text": "name"}]},
    }


def test_ast_wrapper_backend_should_tolerate_non_mapping_range_payload():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = (
        '[{"text":"def hello():","file":"example.py","range":null,'
        '"metaVariables":{"single":{"F":{"text":"hello"}}}}]'
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search(
            "example.py",
            "def $F():",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert result.total_matches == 1
    assert result.matches[0].line_number == 1
    assert result.matches[0].range is None


def test_ast_wrapper_backend_should_sentinel_guard_flag_like_paths_on_run(monkeypatch):
    """CWE-88 / audit #5: a user-supplied path that looks like an ast-grep flag
    (e.g. "-U" / "--update-all") must not be parsed by ast-grep's clap CLI as
    its auto-fix flag -- a "--" sentinel must precede every user path on the
    ``sg run`` argv (site: _build_command, non-multiline branch)."""
    backend = AstGrepWrapperBackend()
    monkeypatch.delenv("TG_AST_GREP_TIMEOUT_SECONDS", raising=False)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search_many(
            ["-U", "--update-all", "src"],
            "print($A)",
            config=SearchConfig(ast=True, lang="python"),
        )

    cmd = run.call_args.args[0]
    assert cmd.count("--") == 1
    sentinel_index = cmd.index("--")
    assert cmd[sentinel_index + 1 :] == ["-U", "--update-all", "src"]


def test_ast_wrapper_backend_should_sentinel_guard_flag_like_path_on_single_search(monkeypatch):
    """Same site as above (_build_command, non-multiline branch), exercised via
    the single-file ``search`` entry point rather than ``search_many``."""
    backend = AstGrepWrapperBackend()
    monkeypatch.delenv("TG_AST_GREP_TIMEOUT_SECONDS", raising=False)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search(
            "--rewrite",
            "print($A)",
            config=SearchConfig(ast=True, lang="python"),
        )

    cmd = run.call_args.args[0]
    assert cmd.count("--") == 1
    sentinel_index = cmd.index("--")
    assert cmd[sentinel_index + 1 :] == ["--rewrite"]


def test_ast_wrapper_backend_should_sentinel_guard_flag_like_path_on_multiline_rule_scan(
    monkeypatch,
):
    """Multiline patterns route through the ``sg scan --rule`` argv (site:
    _build_command, rule-file branch) -- must also sentinel-guard the path."""
    backend = AstGrepWrapperBackend()
    monkeypatch.delenv("TG_AST_GREP_TIMEOUT_SECONDS", raising=False)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search(
            "-U",
            "def $FUNC():\n    $$$BODY",
            config=SearchConfig(ast=True, lang="python"),
        )

    cmd = run.call_args.args[0]
    assert cmd[:4] == ["sg", "scan", "--json", "--rule"]
    assert cmd.count("--") == 1
    sentinel_index = cmd.index("--")
    assert cmd[sentinel_index + 1 :] == ["-U"]


def test_ast_wrapper_backend_should_sentinel_guard_flag_like_root_path_on_project_scan():
    """search_project (site: search_project's sg scan --config argv) must also
    sentinel-guard its root_path."""
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "[]"
    mock_result.stderr = ""

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        backend.search_project("--update-all", "sgconfig.yml")

    cmd = run.call_args.args[0]
    assert cmd == [
        "sg",
        "scan",
        "--json",
        "--config",
        "sgconfig.yml",
        "--",
        "--update-all",
    ]


def test_ast_wrapper_backend_should_group_project_scan_results_by_rule_id():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.stdout = (
        '[{"text":"def hello():","file":"a.py","ruleId":"rule-a","range":{"start":{"line":0}}},'
        '{"text":"def world():","file":"b.py","ruleId":"rule-b","range":{"start":{"line":4}}},'
        '{"text":"def again():","file":"a.py","ruleId":"rule-a","range":{"start":{"line":8}}}]'
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result
        ) as run,
    ):
        results = backend.search_project("project", "sgconfig.yml")

    assert run.call_args.args[0] == [
        "sg",
        "scan",
        "--json",
        "--config",
        "sgconfig.yml",
        "--",
        "project",
    ]
    assert results["rule-a"].total_matches == 2
    assert results["rule-a"].matched_file_paths == ["a.py"]
    assert results["rule-b"].total_matches == 1
    assert results["rule-b"].matched_file_paths == ["b.py"]


def test_ast_wrapper_backend_should_surface_nonzero_search_many_errors():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 2
    mock_result.stdout = ""
    mock_result.stderr = "invalid rule config"

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
        pytest.raises(RuntimeError, match="invalid rule config"),
    ):
        backend.search_many(
            ["example.py"],
            "def $FUNC($$$ARGS):",
            config=SearchConfig(ast=True, lang="python"),
        )


def test_ast_wrapper_backend_should_surface_nonzero_project_scan_errors():
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 2
    mock_result.stdout = "[]"
    mock_result.stderr = "failed to parse config"

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
        pytest.raises(RuntimeError, match="failed to parse config"),
    ):
        backend.search_project("project", "sgconfig.yml")


def test_ast_wrapper_backend_tolerates_per_path_access_warnings_with_findings(capsys):
    # Regression: a single permission-denied / unreadable path in the scan tree
    # makes ast-grep exit nonzero with a warning on stderr while still emitting
    # findings on stdout. tensor-grep must keep the findings (not abort) and
    # forward the warning to stderr, the way ripgrep does.
    backend = AstGrepWrapperBackend()

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = json.dumps([
        {"file": "example.py", "text": "print(x)", "range": {"start": {"line": 0}}}
    ])
    mock_result.stderr = (
        "ERROR: C:\\Users\\me\\AppData\\Local\\Temp\\WinSAT: Access is denied. (os error 5)"
    )

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch("tensor_grep.backends.ast_wrapper_backend.subprocess.run", return_value=mock_result),
    ):
        result = backend.search_many(
            ["example.py"],
            "print($A)",
            config=SearchConfig(ast=True, lang="python"),
        )

    assert result.total_matches == 1
    assert "skipped unreadable paths" in capsys.readouterr().err


# --- Backend Fail-Closed Contract: exit-0 malformed/wrong-shape JSON must raise,
# not silently report a clean 0-match result (MED-3 audit fix). `[]` (or any
# string parsing to a list) remains the ONE legitimate no-match shape -- see the
# regression guard at the bottom of this block.


def test_ast_wrapper_backend_should_raise_on_malformed_json_at_exit_zero_for_search():
    backend = AstGrepWrapperBackend()

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            return_value=MagicMock(returncode=0, stdout='[{"file":"a.py","range"', stderr=""),
        ),
        pytest.raises(BackendExecutionError, match="malformed JSON"),
    ):
        backend.search(
            "example.py",
            "function_definition",
            config=SearchConfig(ast=True, lang="python"),
        )


def test_ast_wrapper_backend_should_raise_on_malformed_json_at_exit_zero_for_search_many():
    backend = AstGrepWrapperBackend()

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            return_value=MagicMock(returncode=0, stdout='[{"file":"a.py","range"', stderr=""),
        ),
        pytest.raises(BackendExecutionError, match="malformed JSON"),
    ):
        backend.search_many(
            ["example.py"],
            "function_definition",
            config=SearchConfig(ast=True, lang="python"),
        )


def test_ast_wrapper_backend_should_raise_on_malformed_json_at_exit_zero_for_search_project():
    backend = AstGrepWrapperBackend()

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            return_value=MagicMock(returncode=0, stdout='[{"file":"a.py","range"', stderr=""),
        ),
        pytest.raises(BackendExecutionError, match="malformed JSON"),
    ):
        backend.search_project("project", "sgconfig.yml")


def test_ast_wrapper_backend_should_raise_on_non_list_json_shape_at_exit_zero_for_search():
    backend = AstGrepWrapperBackend()

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            return_value=MagicMock(returncode=0, stdout='{"error":"sg internal panic"}', stderr=""),
        ),
        pytest.raises(BackendExecutionError, match="expected a list"),
    ):
        backend.search(
            "example.py",
            "function_definition",
            config=SearchConfig(ast=True, lang="python"),
        )


def test_ast_wrapper_backend_should_raise_on_non_list_json_shape_at_exit_zero_for_search_project():
    backend = AstGrepWrapperBackend()

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            return_value=MagicMock(returncode=0, stdout='{"error":"sg internal panic"}', stderr=""),
        ),
        pytest.raises(BackendExecutionError, match="expected a list"),
    ):
        backend.search_project("project", "sgconfig.yml")


def test_ast_wrapper_backend_should_treat_valid_empty_list_as_no_match_not_error():
    """The 'obvious fix is wrong' regression guard: a valid empty list `[]` is the
    ONE legitimate no-match shape and must NOT raise, on both the search() path
    (_parse_result) and the search_project() path (_parse_json_items)."""
    backend = AstGrepWrapperBackend()

    with (
        patch.object(backend, "is_available", return_value=True),
        patch.object(backend, "_get_binary_name", return_value="sg"),
        patch(
            "tensor_grep.backends.ast_wrapper_backend.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="[]", stderr=""),
        ),
    ):
        result = backend.search(
            "example.py",
            "function_definition",
            config=SearchConfig(ast=True, lang="python"),
        )
        assert result.total_matches == 0
        assert result.matched_file_paths == []

        project_result = backend.search_project("project", "sgconfig.yml")
        assert project_result == {}
