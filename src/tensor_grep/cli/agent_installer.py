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
- Symbol blast radius: `tg blast-radius <path> <symbol> --json`
- Checkpoint snapshots: `tg checkpoint create . --label "<label>" --json` / `tg checkpoint undo <id> . --json`
{END_SENTINEL}"""


def _resolve_tg_command(home_dir: Path | None = None) -> str:
    """Resolve the appropriate command string for `tg`, preferring the stable managed shim."""
    exe_name = "tg.exe" if sys.platform == "win32" else "tg"
    base_home = home_dir or Path.home()
    managed_shim = base_home / ".tensor-grep" / "bin" / exe_name
    if managed_shim.is_file():
        return str(managed_shim)

    if sys.prefix != sys.base_prefix:
        scripts_dir = Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin")
        tg_bin = scripts_dir / exe_name
        if tg_bin.is_file():
            resolved = tg_bin.resolve()
            if "runtimes" not in resolved.parts:
                return str(resolved)

    which_tg = shutil.which("tg")
    if which_tg:
        return "tg"

    python_bin_dir = Path(sys.executable).parent
    tg_candidate = python_bin_dir / exe_name
    if tg_candidate.is_file():
        resolved = tg_candidate.resolve()
        if "runtimes" not in resolved.parts:
            return str(resolved)

    return "tg"


def _strip_json_comments_and_trailing_commas(text: str) -> str:
    """Strip UTF-8 BOM, C-style comments and trailing commas from JSONC (string-aware)."""
    s = text.lstrip("\ufeff")
    out: list[str] = []
    i, n = 0, len(s)
    in_str = escaped = False
    while i < n:  # pass 1: drop // and /* */ comments outside strings
        c = s[i]
        if in_str:
            out.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif s.startswith("//", i):
            while i < n and s[i] not in "\r\n":
                i += 1
        elif s.startswith("/*", i):
            end = s.find("*/", i + 2)
            if end == -1:
                # council round 6: never silently drop an unterminated comment (and what follows)
                raise ValueError("Cannot safely parse configuration: unterminated /* comment")
            out.append(" ")  # a comment separates tokens: `1/*c*/2` must NOT become `12`
            i = end + 2
        else:
            out.append(c)
            i += 1
    s = "".join(out)
    res: list[str] = []
    in_str = escaped = False
    for k, c in enumerate(s):  # pass 2: drop trailing commas outside strings
        if in_str:
            res.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "," and s[k + 1 :].lstrip()[:1] in ("]", "}"):
            continue
        res.append(c)
    return "".join(res)


def _atomic_write_text(path: Path, content: str) -> None:
    from tensor_grep.cli._index_lock import atomic_write_bytes

    atomic_write_bytes(path, content.encode("utf-8"))


def _update_json_mcp(
    path: Path,
    add_entry: bool = True,
    *,
    opencode: bool = False,
    home_dir: Path | None = None,
) -> bool:
    data: dict[str, Any] = {}
    if path.exists():
        raw_text = path.read_text(encoding="utf-8")
        if raw_text.strip():
            try:
                data = json.loads(raw_text)
            except (json.JSONDecodeError, ValueError):
                try:
                    data = json.loads(_strip_json_comments_and_trailing_commas(raw_text))
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError(
                        f"Cannot safely parse existing configuration file '{path}': {exc}. "
                        "Refusing to modify to prevent configuration loss."
                    ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            f"Configuration file '{path}' must contain a JSON object; refusing to modify it."
        )
    section_key = "mcp" if opencode else "mcpServers"
    if section_key in data and not isinstance(data[section_key], dict):
        raise ValueError(
            f"'{section_key}' in '{path}' must be a JSON object; refusing to modify it."
        )

    mcp_servers = data.setdefault(section_key, {})
    changed = False

    if opencode:
        legacy_servers = data.get("mcpServers")
        if isinstance(legacy_servers, dict) and "tensor_grep" in legacy_servers:
            del legacy_servers["tensor_grep"]
            if not legacy_servers:
                del data["mcpServers"]
            changed = True

    tg_cmd = _resolve_tg_command(home_dir)
    new_entry: dict[str, Any] = (
        {"type": "local", "command": [tg_cmd, "mcp"]}
        if opencode
        else {"command": tg_cmd, "args": ["mcp"]}
    )
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


def _parse_toml_table_header(line: str) -> dict[str, Any] | None:
    """Parse a single-line TOML table/array-of-tables header using stdlib tomllib."""
    import tomllib

    stripped = line.lstrip(" \t")
    if not stripped.startswith("["):
        return None
    try:
        parsed = tomllib.loads(stripped + "\n")
    except tomllib.TOMLDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _strip_toml_tensor_grep_sections(content: str) -> str:
    """Remove [mcp_servers.tensor_grep] and its subtables without cutting arrays/strings."""
    kept: list[str] = []
    skipping = False
    depth = 0
    in_ml_str = False
    in_ml_lit = False
    for line in content.splitlines(keepends=True):
        if depth == 0 and not in_ml_str and not in_ml_lit:
            parsed_hdr = _parse_toml_table_header(line.rstrip("\r\n"))
            if parsed_hdr is not None:
                mcp_servers = parsed_hdr.get("mcp_servers")
                skipping = isinstance(mcp_servers, dict) and "tensor_grep" in mcp_servers
        if not skipping:
            kept.append(line)
        i, n = 0, len(line)
        in_str = False
        in_lit = False
        esc = False
        while i < n:
            ch = line[i]
            if esc:
                esc = False
            elif in_ml_str:
                if ch == "\\":
                    esc = True
                elif line.startswith('"""', i):
                    extra = 0
                    while extra < 2 and i + 3 < n and line[i + 3] == '"':
                        i += 1
                        extra += 1
                    in_ml_str = False
                    i += 2
            elif in_ml_lit:
                if line.startswith("'''", i):
                    extra = 0
                    while extra < 2 and i + 3 < n and line[i + 3] == "'":
                        i += 1
                        extra += 1
                    in_ml_lit = False
                    i += 2
            elif in_str:
                if ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif in_lit:
                if ch == "'":
                    in_lit = False
            elif line.startswith('"""', i):
                in_ml_str = True
                i += 2
            elif line.startswith("'''", i):
                in_ml_lit = True
                i += 2
            elif ch == '"':
                in_str = True
            elif ch == "'":
                in_lit = True
            elif ch == "#":
                break
            elif ch == "[":
                depth += 1
            elif ch == "]" and depth > 0:
                depth -= 1
            i += 1
    return "".join(kept)


def _update_toml_codex(path: Path, add_entry: bool = True, *, home_dir: Path | None = None) -> bool:
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    stripped = _strip_toml_tensor_grep_sections(content).strip()
    tg_cmd_toml = json.dumps(_resolve_tg_command(home_dir), ensure_ascii=False)
    if add_entry:
        block = f'[mcp_servers.tensor_grep]\ncommand = {tg_cmd_toml}\nargs = ["mcp"]\n'
        new_content = f"{stripped}\n\n{block}" if stripped else block
    else:
        new_content = f"{stripped}\n" if (stripped and stripped != content.strip()) else content
        if stripped != content.strip() and not stripped:
            new_content = ""

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
            new_content = pattern.sub(lambda _m: GUIDANCE_BLOCK, content)
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
            _update_json_mcp(cfg, add_entry=True, home_dir=home)
            _update_markdown_guidance(md, add_entry=True)

    elif target_lower == "cursor":
        cfg = home / ".cursor" / "mcp.json"
        files_modified.append(cfg)
        if not dry_run:
            _update_json_mcp(cfg, add_entry=True, home_dir=home)

    elif target_lower == "codex":
        cfg = home / ".codex" / "config.toml"
        md = home / ".codex" / "AGENTS.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            _update_toml_codex(cfg, add_entry=True, home_dir=home)
            _update_markdown_guidance(md, add_entry=True)

    elif target_lower == "opencode":
        cfg = home / ".config" / "opencode" / "opencode.json"
        md = home / ".config" / "opencode" / "AGENTS.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            _update_json_mcp(cfg, add_entry=True, opencode=True, home_dir=home)
            _update_markdown_guidance(md, add_entry=True)

    elif target_lower == "qwen":
        cfg = home / ".qwen" / "settings.json"
        md = home / ".qwen" / "QWEN.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            _update_json_mcp(cfg, add_entry=True, home_dir=home)
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
                _update_json_mcp(cfg, add_entry=False, home_dir=home)
            if md.exists():
                _update_markdown_guidance(md, add_entry=False)

    elif target_lower == "cursor":
        cfg = home / ".cursor" / "mcp.json"
        files_modified.append(cfg)
        if not dry_run and cfg.exists():
            _update_json_mcp(cfg, add_entry=False, home_dir=home)

    elif target_lower == "codex":
        cfg = home / ".codex" / "config.toml"
        md = home / ".codex" / "AGENTS.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            if cfg.exists():
                _update_toml_codex(cfg, add_entry=False, home_dir=home)
            if md.exists():
                _update_markdown_guidance(md, add_entry=False)

    elif target_lower == "opencode":
        cfg = home / ".config" / "opencode" / "opencode.json"
        md = home / ".config" / "opencode" / "AGENTS.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            if cfg.exists():
                _update_json_mcp(cfg, add_entry=False, opencode=True, home_dir=home)
            if md.exists():
                _update_markdown_guidance(md, add_entry=False)

    elif target_lower == "qwen":
        cfg = home / ".qwen" / "settings.json"
        md = home / ".qwen" / "QWEN.md"
        files_modified.extend([cfg, md])
        if not dry_run:
            if cfg.exists():
                _update_json_mcp(cfg, add_entry=False, home_dir=home)
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
    except (ValueError, OSError) as exc:
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
    except (ValueError, OSError) as exc:
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
