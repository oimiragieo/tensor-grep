import json
import tempfile
import tomllib
from pathlib import Path

import pytest

from tensor_grep.cli.agent_installer import (
    install_agent_integration,
    uninstall_agent_integration,
)


@pytest.fixture
def fake_home():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def test_install_claude_target(fake_home):
    res = install_agent_integration("claude", home_dir=fake_home)
    assert res["status"] == "installed"

    claude_json = fake_home / ".claude.json"
    assert claude_json.exists()
    data = json.loads(claude_json.read_text(encoding="utf-8"))
    assert "tensor_grep" in data["mcpServers"]
    assert "tg" in data["mcpServers"]["tensor_grep"]["command"]
    assert data["mcpServers"]["tensor_grep"]["args"] == ["mcp"]

    claude_md = fake_home / ".claude" / "CLAUDE.md"
    assert claude_md.exists()
    content = claude_md.read_text(encoding="utf-8")
    assert "TENSOR_GREP_START" in content
    assert "TENSOR_GREP_END" in content


def test_install_cursor_target(fake_home):
    res = install_agent_integration("cursor", home_dir=fake_home)
    assert res["status"] == "installed"

    cursor_mcp = fake_home / ".cursor" / "mcp.json"
    assert cursor_mcp.exists()
    data = json.loads(cursor_mcp.read_text(encoding="utf-8"))
    assert "tensor_grep" in data["mcpServers"]


def test_install_codex_target(fake_home):
    res = install_agent_integration("codex", home_dir=fake_home)
    assert res["status"] == "installed"

    codex_toml = fake_home / ".codex" / "config.toml"
    assert codex_toml.exists()
    toml_content = codex_toml.read_text(encoding="utf-8")
    assert "mcp_servers.tensor_grep" in toml_content

    codex_agents = fake_home / ".codex" / "AGENTS.md"
    assert codex_agents.exists()
    assert "TENSOR_GREP_START" in codex_agents.read_text(encoding="utf-8")


def test_uninstall_claude_target(fake_home):
    # Setup existing config with another tool
    claude_json = fake_home / ".claude.json"
    claude_json.write_text(
        json.dumps({
            "mcpServers": {
                "other_tool": {"command": "other", "args": []},
                "tensor_grep": {"command": "tg", "args": ["mcp"]},
            }
        }),
        encoding="utf-8",
    )

    claude_md = fake_home / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    claude_md.write_text(
        "# Custom Instructions\n\n<!-- >>> TENSOR_GREP_START >>> -->\nstuff\n<!-- <<< TENSOR_GREP_END <<< -->\n\nUser Notes",
        encoding="utf-8",
    )

    res = uninstall_agent_integration("claude", home_dir=fake_home)
    assert res["status"] == "uninstalled"

    # Verify tensor_grep removed but other_tool preserved
    data = json.loads(claude_json.read_text(encoding="utf-8"))
    assert "other_tool" in data["mcpServers"]
    assert "tensor_grep" not in data["mcpServers"]

    # Verify guidance removed but User Notes preserved
    md_content = claude_md.read_text(encoding="utf-8")
    assert "TENSOR_GREP_START" not in md_content
    assert "Custom Instructions" in md_content
    assert "User Notes" in md_content


def test_dry_run_does_not_write(fake_home):
    res = install_agent_integration("claude", home_dir=fake_home, dry_run=True)
    assert res["status"] == "dry_run"
    assert not (fake_home / ".claude.json").exists()


def test_mcp_command_resolution():
    from tensor_grep.cli.agent_installer import _resolve_tg_command

    cmd = _resolve_tg_command()
    assert isinstance(cmd, str)
    assert "tg" in cmd


def test_install_preserves_comments_and_trailing_commas(fake_home):
    claude_json = fake_home / ".claude.json"
    claude_json.write_text(
        """{
        // Custom user configuration comment
        "theme": "dark",
        "mcpServers": {
            "user_tool": { "command": "run", }, /* trailing comma */
        },
    }""",
        encoding="utf-8",
    )

    res = install_agent_integration("claude", home_dir=fake_home)
    assert res["status"] == "installed"
    data = json.loads(claude_json.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
    assert "user_tool" in data["mcpServers"]
    assert "tensor_grep" in data["mcpServers"]


def test_install_corrupt_json_fails_closed_without_overwriting(fake_home):
    claude_json = fake_home / ".claude.json"
    corrupt_content = "NOT_JSON_DATA_AT_ALL {{{ corrupt"
    claude_json.write_text(corrupt_content, encoding="utf-8")

    with pytest.raises(ValueError, match="Cannot safely parse"):
        install_agent_integration("claude", home_dir=fake_home)

    # Ensure file was not clobbered
    assert claude_json.read_text(encoding="utf-8") == corrupt_content


def test_install_all_target_dry_run_status(fake_home):
    res = install_agent_integration("all", home_dir=fake_home, dry_run=True)
    assert res["status"] == "dry_run"
    assert len(res["files"]) > 0


def test_install_handles_utf8_bom(fake_home):
    claude_json = fake_home / ".claude.json"
    # Write with UTF-8 BOM
    claude_json.write_bytes(b"\xef\xbb\xbf" + b'{"existing_key": "val"}')

    res = install_agent_integration("claude", home_dir=fake_home)
    assert res["status"] == "installed"
    data = json.loads(claude_json.read_text(encoding="utf-8"))
    assert data["existing_key"] == "val"
    assert "tensor_grep" in data["mcpServers"]


def test_uninstall_removes_toml_subtables(fake_home):
    codex_toml = fake_home / ".codex" / "config.toml"
    codex_toml.parent.mkdir(parents=True, exist_ok=True)
    codex_toml.write_text(
        """[other_server]
command = "other"

[mcp_servers.tensor_grep]
command = "tg"
args = ["mcp"]

[mcp_servers.tensor_grep.env]
DEBUG = "1"
""",
        encoding="utf-8",
    )

    res = uninstall_agent_integration("codex", home_dir=fake_home)
    assert res["status"] == "uninstalled"
    content = codex_toml.read_text(encoding="utf-8")
    assert "tensor_grep" not in content
    assert "other_server" in content


def test_uninstall_guidance_unlinks_empty_file(fake_home):
    claude_md = fake_home / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    from tensor_grep.cli.agent_installer import GUIDANCE_BLOCK

    claude_md.write_text(GUIDANCE_BLOCK, encoding="utf-8")
    assert claude_md.exists()

    res = uninstall_agent_integration("claude", home_dir=fake_home)
    assert res["status"] == "uninstalled"
    assert not claude_md.exists()


def test_codex_reinstall_keeps_toml_valid_with_windows_path(fake_home, monkeypatch):
    from tensor_grep.cli import agent_installer

    win = "C:\\Users\\me\\.venv\\Scripts\\tg.exe"
    monkeypatch.setattr(agent_installer, "_resolve_tg_command", lambda: win)
    install_agent_integration("codex", home_dir=fake_home)
    install_agent_integration("codex", home_dir=fake_home)
    data = tomllib.loads((fake_home / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert data["mcp_servers"]["tensor_grep"]["command"] == win


def test_json_config_non_object_is_refused_cleanly(fake_home):
    cfg = fake_home / ".claude.json"
    cfg.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        install_agent_integration("claude", home_dir=fake_home)
    assert cfg.read_text(encoding="utf-8") == "[]"
    cfg.write_text('{"mcpServers": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="mcpServers"):
        install_agent_integration("claude", home_dir=fake_home)


@pytest.mark.parametrize("body", ['{"n": 1/*c*/2}', '{"n": 1} /* never closed'])
def test_malformed_jsonc_is_refused_not_rewritten(fake_home, body):
    # a block comment must not glue tokens, and an unterminated one must not vanish
    cfg = fake_home / ".claude.json"
    cfg.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        install_agent_integration("claude", home_dir=fake_home)
    assert cfg.read_text(encoding="utf-8") == body


def test_jsonc_urls_and_escaped_quotes_in_strings_survive(fake_home):
    cfg = fake_home / ".claude.json"
    cfg.write_text(
        '{\n  // keep\n  "$schema": "https://example.com/s.json",\n'
        '  "note": "a,]b /* x */ \\" // y",\n  "mcpServers": {},\n}\n',
        encoding="utf-8",
    )
    install_agent_integration("claude", home_dir=fake_home)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["$schema"] == "https://example.com/s.json"
    assert data["note"] == 'a,]b /* x */ " // y'
    assert "tensor_grep" in data["mcpServers"]


def test_resolve_tg_command_prefers_managed_shim_and_never_pins_runtimes(fake_home, monkeypatch):
    import sys

    from tensor_grep.cli import agent_installer

    exe_name = "tg.exe" if sys.platform == "win32" else "tg"
    shim_path = fake_home / ".tensor-grep" / "bin" / exe_name
    shim_path.parent.mkdir(parents=True, exist_ok=True)
    shim_path.write_text("", encoding="utf-8")

    # Even when running inside a hash-pinned managed runtime venv, prefer the stable shim.
    runtime_prefix = fake_home / ".tensor-grep" / "runtimes" / "abc123" / ".venv"
    scripts_dir = runtime_prefix / ("Scripts" if sys.platform == "win32" else "bin")
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / exe_name).write_text("", encoding="utf-8")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(sys, "prefix", str(runtime_prefix))
    monkeypatch.setattr(sys, "base_prefix", str(fake_home / "base_py"))

    assert agent_installer._resolve_tg_command() == str(shim_path)

    # Even if the shim is absent, never pin a hash-versioned runtimes/<hash>/.venv path.
    shim_path.unlink()
    monkeypatch.setattr(agent_installer.shutil, "which", lambda _name: "/usr/local/bin/tg")
    resolved = agent_installer._resolve_tg_command()
    assert "runtimes" not in Path(resolved).parts
    assert resolved == "tg"


def test_guidance_block_uses_valid_v1142_commands():
    from tensor_grep.cli.agent_installer import GUIDANCE_BLOCK

    assert "tg blast-radius <path> <symbol> --json" in GUIDANCE_BLOCK
    assert "--symbol" not in GUIDANCE_BLOCK
    assert 'tg checkpoint create . --label "<label>" --json' in GUIDANCE_BLOCK
    assert "tg checkpoint undo <id> . --json" in GUIDANCE_BLOCK
    assert "tg checkpoint create <name>" not in GUIDANCE_BLOCK


def test_install_and_uninstall_opencode_schema(fake_home, monkeypatch):
    from tensor_grep.cli import agent_installer

    monkeypatch.setattr(agent_installer, "_resolve_tg_command", lambda: "/stable/tg")
    cfg = fake_home / ".config" / "opencode" / "opencode.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "mcp": {"other_mcp": {"type": "local", "command": ["other", "mcp"]}},
        }),
        encoding="utf-8",
    )

    res = install_agent_integration("opencode", home_dir=fake_home)
    assert res["status"] == "installed"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "mcpServers" not in data
    assert data["$schema"] == "https://opencode.ai/config.json"
    assert data["mcp"]["other_mcp"] == {"type": "local", "command": ["other", "mcp"]}
    assert data["mcp"]["tensor_grep"] == {"type": "local", "command": ["/stable/tg", "mcp"]}

    uninstall_res = uninstall_agent_integration("opencode", home_dir=fake_home)
    assert uninstall_res["status"] == "uninstalled"
    after = json.loads(cfg.read_text(encoding="utf-8"))
    assert after["$schema"] == "https://opencode.ai/config.json"
    assert "tensor_grep" not in after["mcp"]
    assert "other_mcp" in after["mcp"]


def test_uninstall_all_targets_preserves_existing_user_keys(fake_home):
    claude_cfg = fake_home / ".claude.json"
    claude_cfg.write_text(
        json.dumps({"theme": "dark", "mcpServers": {"keep": {"command": "k", "args": []}}}),
        encoding="utf-8",
    )
    cursor_cfg = fake_home / ".cursor" / "mcp.json"
    cursor_cfg.parent.mkdir(parents=True, exist_ok=True)
    cursor_cfg.write_text(
        json.dumps({"mcpServers": {"keep": {"command": "k", "args": []}}}),
        encoding="utf-8",
    )
    codex_cfg = fake_home / ".codex" / "config.toml"
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    codex_cfg.write_text('model = "gpt-5"\n\n[mcp_servers.keep]\ncommand = "k"\n', encoding="utf-8")
    opencode_cfg = fake_home / ".config" / "opencode" / "opencode.json"
    opencode_cfg.parent.mkdir(parents=True, exist_ok=True)
    opencode_cfg.write_text(
        json.dumps({"theme": "opencode", "mcp": {"keep": {"type": "local", "command": ["k"]}}}),
        encoding="utf-8",
    )
    qwen_cfg = fake_home / ".qwen" / "settings.json"
    qwen_cfg.parent.mkdir(parents=True, exist_ok=True)
    qwen_cfg.write_text(
        json.dumps({"ui": {"compact": True}, "mcpServers": {"keep": {"command": "k", "args": []}}}),
        encoding="utf-8",
    )

    install_agent_integration("all", home_dir=fake_home)
    res = uninstall_agent_integration("all", home_dir=fake_home)
    assert res["status"] == "uninstalled"

    claude_data = json.loads(claude_cfg.read_text(encoding="utf-8"))
    assert claude_data["theme"] == "dark"
    assert "keep" in claude_data["mcpServers"]
    assert "tensor_grep" not in claude_data["mcpServers"]

    cursor_data = json.loads(cursor_cfg.read_text(encoding="utf-8"))
    assert "keep" in cursor_data["mcpServers"]
    assert "tensor_grep" not in cursor_data["mcpServers"]

    codex_data = tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
    assert codex_data["model"] == "gpt-5"
    assert "keep" in codex_data["mcp_servers"]
    assert "tensor_grep" not in codex_data["mcp_servers"]

    opencode_data = json.loads(opencode_cfg.read_text(encoding="utf-8"))
    assert opencode_data["theme"] == "opencode"
    assert "keep" in opencode_data["mcp"]
    assert "tensor_grep" not in opencode_data["mcp"]

    qwen_data = json.loads(qwen_cfg.read_text(encoding="utf-8"))
    assert qwen_data["ui"] == {"compact": True}
    assert "keep" in qwen_data["mcpServers"]
    assert "tensor_grep" not in qwen_data["mcpServers"]
