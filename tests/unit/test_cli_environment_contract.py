from pathlib import Path


def test_CliEnvironment_RuntimeOverrides_PreservesLegacySettings() -> None:
    documentation = Path(__file__).resolve().parents[2] / "docs" / "cli_environment.md"

    content = documentation.read_text(encoding="utf-8")

    assert "TG_SIDECAR_PYTHON" in content
    assert "TG_NATIVE_TG_BINARY" in content
    assert "TG_RG_PATH" in content
    assert "TG_FORCE_CPU" in content
    assert "TG_SIDECAR_TIMEOUT_MS" in content
    assert "TENSOR_GREP_DEVICE_IDS" in content
    assert "TENSOR_GREP_CLASSIFY_PROVIDER" in content
    assert "TENSOR_GREP_TRITON_TIMEOUT_SECONDS" in content


def test_CliEnvironment_NativeTimeouts_DocumentsDefaultsAndServerExemptions() -> None:
    documentation = Path(__file__).resolve().parents[2] / "docs" / "cli_environment.md"

    content = documentation.read_text(encoding="utf-8")

    assert "`TG_PASSTHROUGH_TIMEOUT_MS`" in content
    assert "600,000 ms" in content
    assert "It does not apply to `mcp`, `session serve`, or `lsp server` launches." in content
    assert "`TG_LSP_PROVIDER`" in content
    assert "defaults to `native`" in content
    assert "Provider availability alone does not prove LSP behavior." in content


def test_CliEnvironment_SearchSemantics_PreservesLegacyRoutingDetails() -> None:
    documentation = Path(__file__).resolve().parents[2] / "docs" / "cli_environment.md"

    content = documentation.read_text(encoding="utf-8")
    normalized_content = " ".join(content.split())

    assert "validated common rg-compatible subset" in normalized_content
    assert "`--format rg --json`" in content
    assert "ripgrep JSON Lines" in content
    assert "plain `--json` is tensor-grep aggregate JSON" in normalized_content
    assert "`--smart-case`" in content
    assert "`--hidden`" in content
    assert "`--max-depth N`" in content
    assert "`--text`" in content
    assert "native GPU falls back" in normalized_content
    assert "case-insensitive semantics are required" in normalized_content
    assert "a requested switch changes unsupported semantics" in normalized_content


def test_CliEnvironment_LauncherRepair_PreservesExplicitOptIn() -> None:
    documentation = Path(__file__).resolve().parents[2] / "docs" / "cli_environment.md"

    content = documentation.read_text(encoding="utf-8")
    normalized_content = " ".join(content.split())

    assert "`tg repair-launcher --allow-foreign-rename`" in content
    assert "explicitly backs up a foreign Windows `tg.exe`" in normalized_content
    assert "verified tensor-grep front door" in normalized_content
