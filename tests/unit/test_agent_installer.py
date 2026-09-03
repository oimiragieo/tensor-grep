import json
import tempfile
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
