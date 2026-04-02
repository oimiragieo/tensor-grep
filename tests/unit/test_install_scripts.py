from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read_script(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_install_ps1_should_restore_original_directory_in_finally_block():
    content = _read_script("scripts/install.ps1")
    assert "$originalPath = (Get-Location).Path" in content
    assert "finally {" in content
    assert "Set-Location -Path $originalPath" in content
    assert "lsp-setup --json" in content


def test_install_sh_should_capture_original_directory_and_restore_on_exit():
    content = _read_script("scripts/install.sh")
    assert 'ORIGINAL_DIR="$(pwd)"' in content
    assert "trap restore_original_dir EXIT" in content
    assert 'cd "$ORIGINAL_DIR"' in content
    assert 'echo "Returned to original directory: $ORIGINAL_DIR"' in content
    assert "lsp-setup --json" in content
