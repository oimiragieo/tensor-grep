from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tensor_grep.core.retrieval_chunker import chunk_file


def test_chunk_file_skips_binary_nul_files_but_keeps_utf16_bom(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    blob_path = assets_dir / "blob.bin"
    blob_path.write_bytes(b"MAGIC_TOKEN\x00\x01\x02\x03binary_payload")

    utf16_path = tmp_path / "note_utf16.txt"
    utf16_path.write_bytes("def utf16_marker(): pass\n".encode("utf-16"))

    assert chunk_file(str(blob_path)) == []
    assert len(chunk_file(str(utf16_path))) == 1


def test_tg_find_and_find_hybrid_skip_binary_blob(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "blob.bin").write_bytes(
        b"find_binary_target_symbol\x00\x00\xff\xfe\x00binary_data"
    )
    (tmp_path / "app.py").write_text(
        "def find_binary_target_symbol() -> int:\n    return 1\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    for extra_args in ([], ["--hybrid"]):
        res = runner.invoke(
            app,
            ["find", *extra_args, "--json", "find_binary_target_symbol", str(tmp_path)],
        )
        assert res.exit_code == 0, res.output
        payload = json.loads(res.stdout)
        matched_files = [m["file"] for m in payload["matches"]]
        assert any(f.endswith("app.py") for f in matched_files)
        assert not any(f.endswith("blob.bin") for f in matched_files)
