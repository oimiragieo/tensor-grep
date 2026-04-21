from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from collections import Counter
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
TG_BINARY = REPO_ROOT / "rust_core" / "target" / "release" / ("tg.exe" if os.name == "nt" else "tg")
AST_PATTERN = "def $F($$$ARGS): $$$BODY"
PATTERNS = [
    "alpha sentinel",
    "beta timeout",
    "gamma retry budget",
    "delta handshake",
    "epsilon index marker",
    "zeta unicode café",
    "eta 日本語",
    "theta emoji 🔍",
    "iota shard marker",
    "kappa final marker",
]
MATCH_LINE_RE = re.compile(r"^(.*):(\d+):(.*)$")
REQUIRED_ENVELOPE_FIELDS = {"version", "routing_backend", "routing_reason", "sidecar_used"}


@pytest.fixture(scope="session")
def native_tg_binary() -> Path:
    candidate = Path(os.environ.get("TG_NATIVE_TG_BINARY") or TG_BINARY)
    if not candidate.exists():
        pytest.skip(f"native tg binary not found: {candidate}")
    return candidate


def _resolve_rg_binary() -> Path | None:
    env_override = os.environ.get("TG_RG_PATH")
    if env_override:
        candidate = Path(env_override)
        if candidate.exists():
            return candidate

    for candidate_name in ("rg", "rg.exe"):
        resolved = shutil.which(candidate_name)
        if resolved:
            return Path(resolved)

    dev_candidate = REPO_ROOT / "benchmarks" / "ripgrep-14.1.0-x86_64-pc-windows-msvc" / "rg.exe"
    if dev_candidate.exists():
        return dev_candidate

    archive = REPO_ROOT / "benchmarks" / "rg.zip"
    if archive.exists():
        with zipfile.ZipFile(archive) as bundle:
            member = next(
                (name for name in bundle.namelist() if name.endswith("/rg.exe")),
                None,
            )
            if member is not None:
                bundle.extractall(REPO_ROOT / "benchmarks")
                if dev_candidate.exists():
                    return dev_candidate

    return None


@pytest.fixture(scope="session")
def command_env(native_tg_binary: Path) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(SRC_DIR)
    )
    env.setdefault("TG_NATIVE_TG_BINARY", str(native_tg_binary))
    rg_binary = _resolve_rg_binary()
    if rg_binary is None:
        pytest.skip("ripgrep binary not found for cross-backend validation")
    env["TG_RG_PATH"] = str(rg_binary)
    return env


@pytest.fixture()
def native_gpu_available(
    tmp_path: Path,
    native_tg_binary: Path,
    command_env: dict[str, str],
) -> None:
    probe = tmp_path / "gpu_probe.log"
    probe.write_text("gpu probe sentinel\n", encoding="utf-8")
    result = _run_command(
        [
            str(native_tg_binary),
            "search",
            "--gpu-device-ids",
            "0",
            "--json",
            "gpu probe sentinel",
            str(probe),
        ],
        env=command_env,
    )
    if result.returncode != 0:
        pytest.skip(f"native GPU backend unavailable: {result.stderr.strip()}")


def _run_command(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _write_cross_backend_corpus(root: Path, *, file_count: int) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for file_index in range(file_count):
        lines = [f"file={file_index}", "noise line without match"]
        for pattern_index, pattern in enumerate(PATTERNS):
            hit_count = (file_index + pattern_index) % 3
            for hit_index in range(hit_count):
                lines.append(
                    f"{pattern} file={file_index} hit={hit_index} group={pattern_index % 4}"
                )
        (root / f"shard_{file_index:03}.log").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )
    return root


def _write_ast_fixture(root: Path) -> Path:
    file_path = root / "fixture.py"
    file_path.write_text(
        "def alpha(x):\n"
        "    return x + 1\n\n"
        "def beta(y):\n"
        "    if y:\n"
        "        return y\n"
        "    return None\n",
        encoding="utf-8",
    )
    return file_path


def _assert_success(result: subprocess.CompletedProcess[str], *, context: str) -> None:
    assert result.returncode == 0, (
        f"{context} failed with exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def _load_json_payload(
    result: subprocess.CompletedProcess[str], *, context: str
) -> dict[str, object]:
    _assert_success(result, context=context)
    return json.loads(result.stdout)


def _assert_envelope(
    payload: dict[str, object],
    *,
    backend: str,
    sidecar_used: bool,
) -> None:
    assert REQUIRED_ENVELOPE_FIELDS.issubset(payload), payload
    assert payload["version"] == 1
    assert payload["routing_backend"] == backend
    assert isinstance(payload["routing_reason"], str)
    assert payload["routing_reason"]
    assert payload["sidecar_used"] is sidecar_used


def _normalized_match_tuples(payload: dict[str, object]) -> list[tuple[str, int, str]]:
    matches = payload.get("matches")
    assert isinstance(matches, list), payload
    normalized = []
    for match in matches:
        assert isinstance(match, dict), match
        line_number = match.get("line")
        if not isinstance(line_number, int):
            line_number = match.get("line_number")
        assert isinstance(line_number, int), match
        normalized.append((str(match["file"]), line_number, str(match["text"])))
    return sorted(normalized)


def _match_counts_by_file(payload: dict[str, object]) -> Counter[str]:
    return Counter(file_path for file_path, _, _ in _normalized_match_tuples(payload))


def _native_search_json(
    native_tg_binary: Path,
    *,
    corpus: Path,
    pattern: str,
    env: dict[str, str],
    extra_args: list[str],
) -> dict[str, object]:
    command = [str(native_tg_binary), "search", *extra_args, "--json", pattern, str(corpus)]
    return _load_json_payload(
        _run_command(command, env=env),
        context=" ".join(command),
    )


def _python_search_json(corpus: Path, *, pattern: str, env: dict[str, str]) -> dict[str, object]:
    command = [
        sys.executable,
        "-m",
        "tensor_grep.cli.main",
        "search",
        "--json",
        "--no-ignore",
        pattern,
        str(corpus),
    ]
    return _load_json_payload(
        _run_command(command, env=env),
        context=" ".join(command),
    )


def _assert_user_facing_error(
    result: subprocess.CompletedProcess[str],
    *,
    token: str,
    context: str,
) -> None:
    assert result.returncode != 0, context
    stderr = result.stderr.strip()
    assert stderr, context
    assert token in stderr, stderr
    assert "traceback" not in stderr.lower(), stderr
    assert "panic" not in stderr.lower(), stderr


def test_cross_backend_should_match_across_native_cpu_gpu_index_and_rg_passthrough(
    tmp_path: Path,
    native_tg_binary: Path,
    command_env: dict[str, str],
    native_gpu_available: None,
) -> None:
    del native_gpu_available
    corpus = _write_cross_backend_corpus(tmp_path / "corpus", file_count=32)

    for pattern in PATTERNS:
        cpu_payload = _native_search_json(
            native_tg_binary,
            corpus=corpus,
            pattern=pattern,
            env=command_env,
            extra_args=["--cpu", "--no-ignore"],
        )
        gpu_payload = _native_search_json(
            native_tg_binary,
            corpus=corpus,
            pattern=pattern,
            env=command_env,
            extra_args=["--gpu-device-ids", "0", "--no-ignore"],
        )
        index_payload = _native_search_json(
            native_tg_binary,
            corpus=corpus,
            pattern=pattern,
            env=command_env,
            extra_args=["--index", "--no-ignore"],
        )
        rg_payload = _python_search_json(corpus, pattern=pattern, env=command_env)

        _assert_envelope(cpu_payload, backend="NativeCpuBackend", sidecar_used=False)
        _assert_envelope(gpu_payload, backend="NativeGpuBackend", sidecar_used=False)
        _assert_envelope(index_payload, backend="TrigramIndex", sidecar_used=False)
        _assert_envelope(rg_payload, backend="NativeCpuBackend", sidecar_used=False)

        expected_matches = _normalized_match_tuples(cpu_payload)
        assert _normalized_match_tuples(gpu_payload) == expected_matches, pattern
        assert _normalized_match_tuples(index_payload) == expected_matches, pattern
        assert _normalized_match_tuples(rg_payload) == expected_matches, pattern

        expected_counts = _match_counts_by_file(cpu_payload)
        assert _match_counts_by_file(gpu_payload) == expected_counts, pattern
        assert _match_counts_by_file(index_payload) == expected_counts, pattern
        assert _match_counts_by_file(rg_payload) == expected_counts, pattern


def test_cross_backend_should_emit_v1_json_envelopes_for_ast_and_rg_paths(
    tmp_path: Path,
    native_tg_binary: Path,
    command_env: dict[str, str],
) -> None:
    corpus = _write_cross_backend_corpus(tmp_path / "search-corpus", file_count=4)
    python_file = _write_ast_fixture(tmp_path)

    rg_payload = _python_search_json(corpus, pattern=PATTERNS[0], env=command_env)
    _assert_envelope(rg_payload, backend="NativeCpuBackend", sidecar_used=False)

    ast_command = [
        str(native_tg_binary),
        "run",
        "--lang",
        "python",
        "--json",
        AST_PATTERN,
        str(python_file),
    ]
    ast_payload = _load_json_payload(
        _run_command(ast_command, env=command_env),
        context=" ".join(ast_command),
    )
    _assert_envelope(ast_payload, backend="AstBackend", sidecar_used=False)
    assert ast_payload["total_matches"] == 2


def test_native_binary_should_report_pyproject_version(
    native_tg_binary: Path,
    command_env: dict[str, str],
) -> None:
    pyproject_data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected_version = pyproject_data["project"]["version"]

    result = _run_command([str(native_tg_binary), "--version"], env=command_env)

    _assert_success(result, context="native --version")
    assert result.stdout.strip() in {f"tg {expected_version}", f"tensor-grep {expected_version}"}


def test_positional_replace_should_not_mutate_files(
    tmp_path: Path,
    native_tg_binary: Path,
    command_env: dict[str, str],
) -> None:
    target = tmp_path / "replace-target.txt"
    original = "hello world\n"
    target.write_text(original, encoding="utf-8")

    result = _run_command(
        [
            str(native_tg_binary),
            "--replace",
            "hi",
            "hello",
            str(target),
        ],
        env=command_env,
    )

    _assert_success(result, context="positional replace")
    assert target.read_text(encoding="utf-8") == original
    assert "hi world" in result.stdout


def test_cross_backend_gpu_batch_should_match_cpu_file_by_file_on_large_corpus(
    tmp_path: Path,
    native_tg_binary: Path,
    command_env: dict[str, str],
    native_gpu_available: None,
) -> None:
    del native_gpu_available
    corpus = _write_cross_backend_corpus(tmp_path / "large-corpus", file_count=128)
    pattern = PATTERNS[3]

    cpu_payload = _native_search_json(
        native_tg_binary,
        corpus=corpus,
        pattern=pattern,
        env=command_env,
        extra_args=["--cpu", "--no-ignore"],
    )

    gpu_command = [
        str(native_tg_binary),
        "search",
        "--gpu-device-ids",
        "0",
        "--json",
        "--verbose",
        "--no-ignore",
        pattern,
        str(corpus),
    ]
    gpu_result = _run_command(gpu_command, env=command_env)
    gpu_payload = _load_json_payload(gpu_result, context=" ".join(gpu_command))

    assert _normalized_match_tuples(gpu_payload) == _normalized_match_tuples(cpu_payload)
    assert _match_counts_by_file(gpu_payload) == _match_counts_by_file(cpu_payload)
    assert "gpu_batch_files=" in gpu_result.stderr
    assert "gpu_transfer_bytes=" in gpu_result.stderr


def test_cross_backend_should_report_user_facing_errors_for_missing_paths_and_invalid_regex(
    tmp_path: Path,
    native_tg_binary: Path,
    command_env: dict[str, str],
    native_gpu_available: None,
) -> None:
    del native_gpu_available
    corpus = _write_cross_backend_corpus(tmp_path / "error-corpus", file_count=3)
    missing_path = tmp_path / "missing-corpus"
    invalid_regex = "[invalid"

    missing_commands = {
        "native_cpu": [str(native_tg_binary), "search", "--cpu", PATTERNS[0], str(missing_path)],
        "native_gpu": [
            str(native_tg_binary),
            "search",
            "--gpu-device-ids",
            "0",
            PATTERNS[0],
            str(missing_path),
        ],
        "trigram_index": [
            str(native_tg_binary),
            "search",
            "--index",
            PATTERNS[0],
            str(missing_path),
        ],
        "rg_passthrough": [
            sys.executable,
            "-m",
            "tensor_grep.cli.main",
            "search",
            PATTERNS[0],
            str(missing_path),
        ],
    }
    for label, command in missing_commands.items():
        result = _run_command(command, env=command_env)
        _assert_user_facing_error(result, token=str(missing_path), context=label)

    invalid_regex_commands = {
        "native_cpu": [str(native_tg_binary), "search", "--cpu", invalid_regex, str(corpus)],
        "trigram_index": [str(native_tg_binary), "search", "--index", invalid_regex, str(corpus)],
        "rg_passthrough": [
            sys.executable,
            "-m",
            "tensor_grep.cli.main",
            "search",
            invalid_regex,
            str(corpus),
        ],
    }
    for label, command in invalid_regex_commands.items():
        result = _run_command(command, env=command_env)
        _assert_user_facing_error(result, token="regex", context=label)


def test_cross_backend_plain_output_parser_should_handle_windows_paths() -> None:
    match = MATCH_LINE_RE.match(r"C:\repo\file.log:17:text payload")
    assert match is not None
    assert match.group(1) == r"C:\repo\file.log"
    assert match.group(2) == "17"
    assert match.group(3) == "text payload"
