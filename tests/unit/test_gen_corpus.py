import importlib.util
from pathlib import Path

import pytest


def _load_script_module(name: str, rel_path: str):
    root = Path(__file__).resolve().parents[2]
    module_path = root / rel_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("lang", "suffix", "expected_tokens"),
    [
        ("javascript", ".js", ("function generated_", "class Generated_", "const result_")),
        ("typescript", ".ts", ("function generated_", "class Generated_", "const result_")),
        ("rust", ".rs", ("fn generated_", "struct Generated_", "let result_")),
    ],
)
def test_gen_corpus_ast_bench_should_support_multilang(
    lang, suffix, expected_tokens, monkeypatch, tmp_path
):
    module = _load_script_module(f"gen_corpus_{lang}", "benchmarks/gen_corpus.py")
    output_dir = tmp_path / lang

    monkeypatch.setattr(
        "sys.argv",
        [
            "gen_corpus.py",
            "--kind",
            "ast-bench",
            "--lang",
            lang,
            "--out",
            str(output_dir),
            "--files",
            "4",
            "--loc",
            "12",
            "--seed",
            "7",
        ],
    )

    assert module.main() == 0

    generated_files = sorted(output_dir.glob(f"*{suffix}"))
    assert len(generated_files) == 4
    assert {path.suffix for path in generated_files} == {suffix}

    corpus_text = "\n".join(path.read_text(encoding="utf-8") for path in generated_files)
    for token in expected_tokens:
        assert token in corpus_text


def test_gen_corpus_ast_bench_should_keep_python_default_backward_compatible(monkeypatch, tmp_path):
    module = _load_script_module("gen_corpus_python_default", "benchmarks/gen_corpus.py")
    expected_dir = tmp_path / "expected_python"
    actual_dir = tmp_path / "actual_python"

    module.generate_python_ast_bench_corpus(
        expected_dir,
        file_count=3,
        total_loc=9,
        seed=123,
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "gen_corpus.py",
            "--kind",
            "ast-bench",
            "--out",
            str(actual_dir),
            "--files",
            "3",
            "--loc",
            "9",
            "--seed",
            "123",
        ],
    )

    assert module.main() == 0

    expected_files = {
        path.relative_to(expected_dir).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(expected_dir.rglob("*.py"))
    }
    actual_files = {
        path.relative_to(actual_dir).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(actual_dir.rglob("*.py"))
    }

    assert actual_files == expected_files
