"""Tests for the CPU oracle in run_gpu_native_benchmarks.

The oracle (cpu_oracle_search) is a plain Python fixed-string matcher that acts
as an independent, obviously-correct reference implementation.  Its ground truth
is verified here against manually constructed expected output that matches what
``rg -F -e p1 -e p2 …`` would produce on the same synthetic corpora.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module(name: str):
    root = Path(__file__).resolve().parents[2]
    module_path = root / "benchmarks" / "run_gpu_native_benchmarks.py"
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# cpu_oracle_search — unit tests
# ---------------------------------------------------------------------------


def test_cpu_oracle_single_pattern_finds_matching_lines(tmp_path):
    module = _load_module("oracle_single_pattern")
    (tmp_path / "a.log").write_text(
        "INFO boot\nERROR kernel crash\nINFO shutdown\n", encoding="utf-8"
    )
    (tmp_path / "b.log").write_text("WARN retry\nERROR kernel crash\n", encoding="utf-8")

    sigs = module.cpu_oracle_search(["ERROR kernel crash"], tmp_path)

    assert len(sigs) == 2
    for path, lineno, text in sigs:
        assert "ERROR kernel crash" in text
    # rg -F matches 2nd line of a.log (1-indexed) and 2nd line of b.log
    path_a = module._normalized_match_path(str(tmp_path / "a.log"))
    path_b = module._normalized_match_path(str(tmp_path / "b.log"))
    assert (path_a, 2, "ERROR kernel crash") in sigs
    assert (path_b, 2, "ERROR kernel crash") in sigs


def test_cpu_oracle_empty_corpus_returns_empty(tmp_path):
    module = _load_module("oracle_empty_corpus")
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    sigs = module.cpu_oracle_search(["anything"], corpus)

    assert sigs == []


def test_cpu_oracle_no_patterns_returns_empty(tmp_path):
    module = _load_module("oracle_no_patterns")
    (tmp_path / "a.log").write_text("ERROR something\n", encoding="utf-8")

    sigs = module.cpu_oracle_search([], tmp_path)

    assert sigs == []


def test_cpu_oracle_no_match_returns_empty(tmp_path):
    module = _load_module("oracle_no_match")
    (tmp_path / "a.log").write_text("INFO boot\nINFO shutdown\n", encoding="utf-8")

    sigs = module.cpu_oracle_search(["NONEXISTENT_TOKEN"], tmp_path)

    assert sigs == []


def test_cpu_oracle_multi_pattern_reports_each_line_once(tmp_path):
    """A line matching both patterns appears exactly once — rg -F -e p1 -e p2 semantics."""
    module = _load_module("oracle_multi_pattern_once")
    content = "ERROR alpha beta\nWARN noise\nERROR alpha only\n"
    (tmp_path / "a.log").write_text(content, encoding="utf-8")

    sigs = module.cpu_oracle_search(["ERROR alpha", "ERROR beta"], tmp_path)

    path = module._normalized_match_path(str(tmp_path / "a.log"))
    # Line 1 matches "ERROR alpha" (also contains "beta" but that's part of the line,
    # not the pattern "ERROR beta").  Line 3 matches "ERROR alpha only".
    # Line 2 matches neither.
    assert len(sigs) == 2
    assert (path, 1, "ERROR alpha beta") in sigs
    assert (path, 3, "ERROR alpha only") in sigs


def test_cpu_oracle_multi_pattern_double_match_line_reported_once(tmp_path):
    """A line that contains BOTH distinct patterns is reported exactly once."""
    module = _load_module("oracle_multi_pattern_double_match")
    # Line contains both patterns literally
    (tmp_path / "a.log").write_text("ERROR alpha ERROR beta\nINFO clean\n", encoding="utf-8")

    sigs = module.cpu_oracle_search(["ERROR alpha", "ERROR beta"], tmp_path)

    path = module._normalized_match_path(str(tmp_path / "a.log"))
    assert len(sigs) == 1
    assert sigs[0] == (path, 1, "ERROR alpha ERROR beta")


def test_cpu_oracle_result_is_sorted(tmp_path):
    module = _load_module("oracle_sorted")
    # Create two files; rglob order depends on filesystem, but oracle must sort
    (tmp_path / "z.log").write_text("MATCH line\n", encoding="utf-8")
    (tmp_path / "a.log").write_text("MATCH line\n", encoding="utf-8")

    sigs = module.cpu_oracle_search(["MATCH"], tmp_path)

    assert sigs == sorted(sigs), "cpu_oracle_search must return sorted signatures"


def test_cpu_oracle_line_numbers_are_one_indexed(tmp_path):
    module = _load_module("oracle_line_numbers")
    (tmp_path / "a.log").write_text("noise\nTARGET\nnoise\n", encoding="utf-8")

    sigs = module.cpu_oracle_search(["TARGET"], tmp_path)

    assert len(sigs) == 1
    assert sigs[0][1] == 2  # 1-indexed: TARGET is on line 2


def test_cpu_oracle_normalizes_line_text(tmp_path):
    """Trailing \\r\\n is stripped (same as _normalized_match_text)."""
    module = _load_module("oracle_normalize_text")
    # Write CRLF line endings
    (tmp_path / "a.log").write_bytes(b"noise\r\nERROR sentinel\r\nnoise\r\n")

    sigs = module.cpu_oracle_search(["ERROR sentinel"], tmp_path)

    assert len(sigs) == 1
    # The trailing \r\n should be stripped
    assert sigs[0][2] == "ERROR sentinel"


def test_cpu_oracle_normalizes_path_separators(tmp_path):
    """Forward-slash paths are returned on all platforms."""
    module = _load_module("oracle_normalize_path")
    (tmp_path / "a.log").write_text("MATCH\n", encoding="utf-8")

    sigs = module.cpu_oracle_search(["MATCH"], tmp_path)

    assert len(sigs) == 1
    assert "\\" not in sigs[0][0], "path must use forward slashes"


# ---------------------------------------------------------------------------
# cpu_oracle_search compared against rg-equivalent expected output
# (manually constructed ground truth, no rg subprocess needed)
# ---------------------------------------------------------------------------


def test_cpu_oracle_matches_expected_rg_output_single_pattern(tmp_path):
    """Verify the oracle returns signatures identical to what rg --json -F would produce.

    The expected set is manually constructed using the same (path, lineno, text)
    normalization that _extract_rg_json_match_signatures applies, which is what a
    real rg --json invocation would produce after parsing.
    """
    module = _load_module("oracle_vs_rg_single")
    file_a = tmp_path / "a.log"
    file_b = tmp_path / "b.log"
    file_a.write_text("INFO boot\nERROR gpu sentinel\nINFO end\n", encoding="utf-8")
    file_b.write_text("ERROR gpu sentinel\nWARN noise\n", encoding="utf-8")

    # Build expected signatures exactly as _extract_rg_json_match_signatures would,
    # simulating what rg --json -F "ERROR gpu sentinel" would emit.
    path_a = module._normalized_match_path(str(file_a))
    path_b = module._normalized_match_path(str(file_b))
    expected_rg_sigs = sorted([
        (path_a, 2, "ERROR gpu sentinel"),
        (path_b, 1, "ERROR gpu sentinel"),
    ])

    oracle_sigs = module.cpu_oracle_search(["ERROR gpu sentinel"], tmp_path)

    assert oracle_sigs == expected_rg_sigs


def test_cpu_oracle_matches_expected_rg_output_multi_pattern(tmp_path):
    """Multi-pattern oracle matches the expected signatures for rg -F -e p1 -e p2."""
    module = _load_module("oracle_vs_rg_multi")
    file_a = tmp_path / "a.log"
    file_a.write_text(
        "INFO boot\nERROR alpha\nINFO mid\nERROR beta\nINFO end\n", encoding="utf-8"
    )

    path_a = module._normalized_match_path(str(file_a))
    expected_rg_sigs = sorted([
        (path_a, 2, "ERROR alpha"),
        (path_a, 4, "ERROR beta"),
    ])

    oracle_sigs = module.cpu_oracle_search(["ERROR alpha", "ERROR beta"], tmp_path)

    assert oracle_sigs == expected_rg_sigs


# ---------------------------------------------------------------------------
# Oracle wired into run_correctness_check
# ---------------------------------------------------------------------------


def test_run_correctness_check_includes_oracle_status_on_pass(monkeypatch, tmp_path):
    """run_correctness_check exposes oracle_status==PASS when oracle agrees with rg."""
    module = _load_module("oracle_wired_correctness_check")
    tg_binary = tmp_path / "tg.exe"
    corpus_dir = tmp_path / "corpus"
    tg_binary.write_text("binary", encoding="utf-8")
    corpus_dir.mkdir()
    corpus_file = corpus_dir / "data.log"
    corpus_file.write_text("INFO noise\nERROR sentinel match\nINFO noise\n", encoding="utf-8")
    pattern = "ERROR sentinel match"
    path_str = module._normalized_match_path(str(corpus_file))
    expected_sig = (path_str, 2, "ERROR sentinel match")

    def _fake_run_command(command, **_kwargs):
        command_text = " ".join(str(part) for part in command)
        if "--json" in command_text and "--cpu" in command_text:
            payload = {
                "routing_backend": "NativeCpuBackend",
                "sidecar_used": False,
                "total_matches": 1,
                "total_files": 1,
                "matches": [{"file": str(corpus_file), "line_number": 2, "text": pattern}],
            }
            return module.subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if "--json" in command_text and "--gpu-device-ids" in command_text:
            payload = {
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
                "total_matches": 1,
                "total_files": 1,
                "matches": [{"file": str(corpus_file), "line_number": 2, "text": pattern}],
            }
            return module.subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        # rg --json: emit the same match
        rg_event = {
            "type": "match",
            "data": {
                "path": {"text": str(corpus_file)},
                "line_number": 2,
                "lines": {"text": f"{pattern}\n"},
            },
        }
        return module.subprocess.CompletedProcess(command, 0, json.dumps(rg_event), "")

    monkeypatch.setattr(module, "_run_command", _fake_run_command)

    result = module.run_correctness_check(
        tg_binary=tg_binary,
        rg_binary="rg",
        corpus_dir=corpus_dir,
        pattern=pattern,
        device_id=0,
        env={},
        timeout_s=5,
    )

    # Oracle scans the real corpus_dir — one match on line 2
    assert result["oracle_status"] == "PASS"
    assert result["oracle_total_matches"] == 1
    assert result["oracle_matches_equal"] is True
    assert result["status"] == "PASS"


def test_run_correctness_check_oracle_status_fail_blocks_pass(monkeypatch, tmp_path):
    """status is FAIL when oracle disagrees with rg (oracle returns different matches)."""
    module = _load_module("oracle_blocks_pass")
    tg_binary = tmp_path / "tg.exe"
    corpus_dir = tmp_path / "corpus"
    tg_binary.write_text("binary", encoding="utf-8")
    corpus_dir.mkdir()
    # Corpus is empty — oracle returns [], but we will fake rg to return a match

    def _fake_run_command(command, **_kwargs):
        command_text = " ".join(str(part) for part in command)
        if "--json" in command_text and "--cpu" in command_text:
            payload = {
                "routing_backend": "NativeCpuBackend",
                "sidecar_used": False,
                "total_matches": 1,
                "total_files": 1,
                "matches": [{"file": "fake.log", "line_number": 1, "text": "SENTINEL"}],
            }
            return module.subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if "--json" in command_text and "--gpu-device-ids" in command_text:
            payload = {
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
                "total_matches": 1,
                "total_files": 1,
                "matches": [{"file": "fake.log", "line_number": 1, "text": "SENTINEL"}],
            }
            return module.subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        rg_event = {
            "type": "match",
            "data": {
                "path": {"text": "fake.log"},
                "line_number": 1,
                "lines": {"text": "SENTINEL\n"},
            },
        }
        return module.subprocess.CompletedProcess(command, 0, json.dumps(rg_event), "")

    monkeypatch.setattr(module, "_run_command", _fake_run_command)
    # Oracle scans the REAL empty corpus_dir and returns [] — disagrees with rg

    result = module.run_correctness_check(
        tg_binary=tg_binary,
        rg_binary="rg",
        corpus_dir=corpus_dir,
        pattern="SENTINEL",
        device_id=0,
        env={},
        timeout_s=5,
    )

    # rg (faked) says 1 match, oracle says 0 — oracle_status is FAIL, overall FAIL
    assert result["oracle_status"] == "FAIL"
    assert result["oracle_matches_equal"] is False
    assert result["oracle_total_matches"] == 0
    assert result["status"] == "FAIL"


def test_run_many_pattern_correctness_check_includes_oracle_status(monkeypatch, tmp_path):
    """run_many_pattern_correctness_check exposes oracle_status==PASS."""
    module = _load_module("oracle_wired_many_pattern")
    tg_binary = tmp_path / "tg.exe"
    corpus_dir = tmp_path / "corpus"
    tg_binary.write_text("binary", encoding="utf-8")
    corpus_dir.mkdir()
    corpus_file = corpus_dir / "data.log"
    pattern_a = "ERROR alpha sentinel"
    pattern_b = "ERROR beta sentinel"
    corpus_file.write_text(f"{pattern_a}\n{pattern_b}\n", encoding="utf-8")
    path_str = module._normalized_match_path(str(corpus_file))

    def _fake_run_command(command, **_kwargs):
        command_text = " ".join(str(part) for part in command)
        tg_payload = {
            "routing_backend": "NativeGpuBackend",
            "sidecar_used": False,
            "matches": [
                {"file": str(corpus_file), "line_number": 1, "text": f"{pattern_a}\n"},
                {"file": str(corpus_file), "line_number": 2, "text": f"{pattern_b}\n"},
            ],
        }
        if "--cpu" in command_text:
            tg_payload["routing_backend"] = "NativeCpuBackend"
        if "rg" in command_text:
            events = [
                {
                    "type": "match",
                    "data": {
                        "path": {"text": str(corpus_file)},
                        "line_number": 1,
                        "lines": {"text": f"{pattern_a}\n"},
                    },
                },
                {
                    "type": "match",
                    "data": {
                        "path": {"text": str(corpus_file)},
                        "line_number": 2,
                        "lines": {"text": f"{pattern_b}\n"},
                    },
                },
            ]
            stdout = "\n".join(json.dumps(e) for e in events)
            return module.subprocess.CompletedProcess(command, 0, stdout, "")
        return module.subprocess.CompletedProcess(command, 0, json.dumps(tg_payload), "")

    monkeypatch.setattr(module, "_run_command", _fake_run_command)

    check = module.run_many_pattern_correctness_check(
        tg_binary=tg_binary,
        rg_binary="rg",
        corpus_dir=corpus_dir,
        patterns=[pattern_a, pattern_b],
        device_id=0,
        env={},
        timeout_s=5,
    )

    # Oracle scans real corpus_dir and finds both patterns
    assert check["oracle_status"] == "PASS"
    assert check["oracle_total_matches"] == 2
    assert check["oracle_matches_equal"] is True
    assert check["status"] == "PASS"
