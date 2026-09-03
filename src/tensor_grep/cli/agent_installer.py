"""Agent Integration Installer for tensor-grep.

Configures AI coding agents (Claude Code, Cursor, Codex, OpenCode, Qwen)
to use tensor-grep's built-in MCP server and adds search guidance rules.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import typer

SUPPORTED_TARGETS = ("claude", "cursor", "codex", "opencode", "qwen")

START_SENTINEL = "<!-- >>> TENSOR_GREP_START >>> -->"
END_SENTINEL = "<!-- <<< TENSOR_GREP_END <<< -->"

GUIDANCE_BLOCK = f"""{START_SENTINEL}
## Tensor-Grep Workspace Search & Intelligence
Use `tg` exclusively for searching and locating code patterns:
- Text search: `tg search "<pattern>" <path>`
- Edit readiness: `tg prepare <path> "<task>" --json`
- Symbol blast radius: `tg blast-radius <path> --symbol <name> --json`
- Checkpoint snapshots: `tg checkpoint create <name>` / `tg checkpoint undo <id>`
{END_SENTINEL}"""


def _resolve_tg_command() -> str:
    """Dynamically resolve the appropriate command string for `tg`."""
    if sys.prefix != sys.base_prefix:
        scripts_dir = Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin")
        tg_bin = scripts_dir / ("tg.exe" if sys.platform == "win32" else "tg")
        if tg_bin.exists():
            return str(tg_bin.resolve())

    which_tg = shutil.which("tg")
    if which_tg:
        return "tg"

    python_bin_dir = Path(sys.executable).parent
    tg_candidate = python_bin_dir / ("tg.exe" if sys.platform == "win32" else "tg")
    if tg_candidate.exists():
        return str(tg_candidate.resolve())

    return "tg"


def _strip_json_comments_and_trailing_commas(text: str) -> str:
    """Strip UTF-8 BOM, C-style comments and trailing commas from JSONC for safe parsing."""
    cleaned = text.lstrip("\ufeff")
    cleaned = re.sub(r"/\*[\s\S]*?\*/", "", cleaned)
    cleaned = re.sub(r"//[^\n\r]*", "", cleaned)
    cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)
    return cleaned


def _atomic_write_text(path: Path, content: str) -> None:
    from tensor_grep.cli._index_lock import atomic_write_bytes

    atomic_write_bytes(path, content.encode("utf-8"))


def _update_json_mcp(path: Path, add_entry: bool = True) -> bool:
    data: dict[str, Any] = {}
    if path.exists():
        raw_text = path.read_text(encoding="utf-8")
        if raw_text.strip():
            try:
                data = json.loads(raw_text)
            except Exception:
                try:
                    data = json.loads(_strip_json_comments_and_trailing_commas(raw_text))
                except Exception as exc:
                    raise ValueError(
                        f"Cannot safely parse existing configuration file '{path}': {exc}. "
                        "Refusing to modify to prevent configuration loss."
                    ) from exc

    mcp_servers = data.setdefault("mcpServers", {})
    changed = False

    new_entry = {
        "command": _resolve_tg_command(),
        "args": ["mcp"],
    }
    if add_entry:
        if mcp_servers.get("tensor_grep") != new_entry:
            mcp_servers["tensor_grep"] = new_entry
            changed = True
    else:
        if "tensor_grep" in mcp_servers:
            del mcp_servers["tensor_grep"]
            changed = True

    if changed:
        _atomic_write_text(path, json.dumps(data, indent=2) + "\n")
    return changed


def _update_toml_codex(path: Path, add_entry: bool = True) -> bool:
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(
        r"\[mcp_servers\.tensor_grep(?:\.[^\]]+)?\][\s\S]*?(?=\n\[(?!mcp_servers\.tensor_grep)|\Z)",
        re.MULTILINE,
    )

    tg_cmd = _resolve_tg_command().replace("\\", "\\\\")
    if add_entry:
        block = f'[mcp_servers.tensor_grep]\ncommand = "{tg_cmd}"\nargs = ["mcp"]\n'
        if pattern.search(content):
            new_content = pattern.sub(block.strip(), content)
        else:
            new_content = (content.rstrip() + "\n\n" + block).lstrip()
    else:
        new_content = (
            pattern.sub("", content).strip() + "\n" if pattern.search(content) else content
        )

    if new_content != content:
        _atomic_write_text(path, new_content)
        return True
    return False


def _update_markdown_guidance(path: Path, add_entry: bool = True) -> bool:
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(
        re.escape(START_SENTINEL) + r"[\s\S]*?" + re.escape(END_SENTINEL),
        re.MULTILINE,
    )

    if add_entry:
        if pattern.search(content):
            new_content = pattern.sub(GUIDANCE_BLOCK, content)
        else:
            new_content = (content.rstrip() + "\n\n" + GUIDANCE_BLOCK + "\n").lstrip()
        if new_content != content:
            _atomic_write_text(path, new_content)
            return True
    else:
        if pattern.search(content):
            new_content = pattern.sub("", content)
            new_content = re.sub(r"\n{3,}", "\n\n", new_content).strip()
            if not new_content:
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass
                return True
            new_content = new_content + "\n"
            if new_content != content:
                _atomic_write_text(path, new_content)
                return True
    return False


def install_agent_integration(
    target: str,
    home_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Install tensor-grep MCP integration and search guidance for an agent."""
    home = home_dir or Path.home()
    target_lower = target.lower()

    if target_lower == "all":
        results = {}
        all_files: list[str] = []
        for t in SUPPORTED_TARGETS:
            res = install_agent_integration(t, home_dir=home, dry_run=dry_run)
            results[t] = res
            all_files.extend(res.get("files", []))
        status = "dry_run" if dry_run else "installed"
        return {
            "target": "all",
            "status": status,
            "files": all_files,
            "details": results,
        }

    if target_lower not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported agent target '{target}'. Supported: {SUPPORTED_TARGETS}")

    files_modified = []
    if target_lower == "claude":
        cfg = home / ".claude.json"
        md = home / ".claude" / "CLAUDE.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            _update_json_mcp(cfg, add_entry=True)
            _update_markdown_guidance(md, add_entry=True)

    elif target_lower == "cursor":
        cfg = home / ".cursor" / "mcp.json"
        files_modified.append(cfg)
        if not dry_run:
            _update_json_mcp(cfg, add_entry=True)

    elif target_lower == "codex":
        cfg = home / ".codex" / "config.toml"
        md = home / ".codex" / "AGENTS.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            _update_toml_codex(cfg, add_entry=True)
            _update_markdown_guidance(md, add_entry=True)

    elif target_lower == "opencode":
        cfg = home / ".config" / "opencode" / "opencode.json"
        md = home / ".config" / "opencode" / "AGENTS.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            _update_json_mcp(cfg, add_entry=True)
            _update_markdown_guidance(md, add_entry=True)

    elif target_lower == "qwen":
        cfg = home / ".qwen" / "settings.json"
        md = home / ".qwen" / "QWEN.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            _update_json_mcp(cfg, add_entry=True)
            _update_markdown_guidance(md, add_entry=True)

    status = "dry_run" if dry_run else "installed"
    return {
        "target": target_lower,
        "status": status,
        "files": [str(f) for f in files_modified],
    }


def uninstall_agent_integration(
    target: str,
    home_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Uninstall tensor-grep MCP integration and search guidance for an agent."""
    home = home_dir or Path.home()
    target_lower = target.lower()

    if target_lower == "all":
        results = {}
        all_files: list[str] = []
        for t in SUPPORTED_TARGETS:
            res = uninstall_agent_integration(t, home_dir=home, dry_run=dry_run)
            results[t] = res
            all_files.extend(res.get("files", []))
        status = "dry_run" if dry_run else "uninstalled"
        return {
            "target": "all",
            "status": status,
            "files": all_files,
            "details": results,
        }

    if target_lower not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported agent target '{target}'. Supported: {SUPPORTED_TARGETS}")

    files_modified = []
    if target_lower == "claude":
        cfg = home / ".claude.json"
        md = home / ".claude" / "CLAUDE.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            if cfg.exists():
                _update_json_mcp(cfg, add_entry=False)
            if md.exists():
                _update_markdown_guidance(md, add_entry=False)

    elif target_lower == "cursor":
        cfg = home / ".cursor" / "mcp.json"
        files_modified.append(cfg)
        if not dry_run and cfg.exists():
            _update_json_mcp(cfg, add_entry=False)

    elif target_lower == "codex":
        cfg = home / ".codex" / "config.toml"
        md = home / ".codex" / "AGENTS.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            if cfg.exists():
                _update_toml_codex(cfg, add_entry=False)
            if md.exists():
                _update_markdown_guidance(md, add_entry=False)

    elif target_lower == "opencode":
        cfg = home / ".config" / "opencode" / "opencode.json"
        md = home / ".config" / "opencode" / "AGENTS.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            if cfg.exists():
                _update_json_mcp(cfg, add_entry=False)
            if md.exists():
                _update_markdown_guidance(md, add_entry=False)

    elif target_lower == "qwen":
        cfg = home / ".qwen" / "settings.json"
        md = home / ".qwen" / "QWEN.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            if cfg.exists():
                _update_json_mcp(cfg, add_entry=False)
            if md.exists():
                _update_markdown_guidance(md, add_entry=False)

    status = "dry_run" if dry_run else "uninstalled"
    return {
        "target": target_lower,
        "status": status,
        "files": [str(f) for f in files_modified],
    }


def install_command(
    target: str = typer.Option(
        "all",
        "--target",
        "-t",
        help="Agent target to configure: claude, cursor, codex, opencode, qwen, or all.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate configuration without writing files.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm installation without prompting.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
) -> None:
    """Configure AI coding agents (Claude Code, Cursor, Codex, OpenCode, Qwen) to use tensor-grep's built-in MCP server."""
    try:
        res = install_agent_integration(target=target, dry_run=dry_run)
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    if json_output:
        typer.echo(json.dumps(res, indent=2))
    else:
        typer.echo(f"tensor-grep MCP integration {res['status']} for {res['target']}")
        for f in res.get("files", []):
            typer.echo(f"  configured: {f}")


def uninstall_command(
    target: str = typer.Option(
        "all",
        "--target",
        "-t",
        help="Agent target to remove: claude, cursor, codex, opencode, qwen, or all.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate removal without writing files.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm uninstallation without prompting.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON output.",
    ),
) -> None:
    """Remove tensor-grep MCP integration and search guidance from AI coding agents."""
    try:
        res = uninstall_agent_integration(target=target, dry_run=dry_run)
    except Exception as exc:
        if json_output:
            typer.echo(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    if json_output:
        typer.echo(json.dumps(res, indent=2))
    else:
        typer.echo(f"tensor-grep MCP integration {res['status']} for {res['target']}")
        for f in res.get("files", []):
            typer.echo(f"  uninstalled: {f}")
