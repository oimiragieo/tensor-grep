"""Read-only coverage inventory for repo-map sessions and AST cache entries."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def _digest(path: Path, limit: int) -> str:
    with path.open("rb") as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError("oversize")
    return hashlib.sha256(content).hexdigest()


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _session_entries(root: Path) -> tuple[dict[str, str], float]:
    from tensor_grep.cli.session_store import get_session, list_sessions

    records = list_sessions(str(root))
    if not records:
        return {}, 0.0
    newest = max(records, key=lambda item: item.created_at)
    payload = get_session(newest.session_id, str(root))
    entries = payload.get("snapshot", [])
    if not isinstance(entries, list):
        return {}, 0.0
    result: dict[str, str] = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("path") and entry.get("content_sha256"):
            result[str(Path(str(entry["path"])))] = str(entry["content_sha256"])
    try:
        stamp = datetime.fromisoformat(newest.created_at).timestamp()
    except (TypeError, ValueError, OverflowError):
        stamp = 0.0
    return result, stamp


def _ast_cache_entries(root: Path) -> tuple[dict[str, str], float]:
    cache_path = root / ".tg_cache" / "symbols_v1" / "metadata.sqlite3"
    if not cache_path.is_file():
        return {}, 0.0
    from tensor_grep.cli.symbols_cache_io import read_confined

    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(read_confined(root, cache_path, 32 * 1024 * 1024))
        generation = connection.execute(
            "SELECT id, created FROM generations WHERE scope=? ORDER BY created DESC LIMIT 1",
            (str(root),),
        ).fetchone()
        if generation is None:
            return {}, 0.0
        rows = connection.execute(
            "SELECT path, content FROM files WHERE scope=? AND generation=?",
            (str(root), int(generation[0])),
        ).fetchall()
        return {str(path): str(digest) for path, digest in rows}, float(generation[1])
    finally:
        connection.close()


def _unparsed_files(files: list[Path], root: Path) -> list[dict[str, str]]:
    from tensor_grep.cli import lang_registry
    from tensor_grep.cli.repo_map_coverage_gaps import _classify_uncached

    reasons: dict[str, str] = {}
    buckets = _classify_uncached(files, root)
    for (kind, _language), bucket in buckets.items():
        if kind in {"parse-cap", "syntax-error", "unreadable", "lossy-decode"}:
            reason = {
                "parse-cap": "oversize",
                "syntax-error": "parse_error",
                "unreadable": "unreadable",
                "lossy-decode": "invalid_utf8",
            }[kind]
            reasons.update({str(item): reason for item in bucket["files"]})
    for path in files:
        spec = lang_registry.spec_for_path(path)
        if spec is None or (
            spec.parser_for_path is not None and spec.parser_for_path(path) is None
        ):
            reasons[_display_path(path, root)] = "missing_grammar"
    return [{"path": path, "reason": reason} for path, reason in sorted(reasons.items())]


def check_coverage(path: Path) -> dict[str, Any]:
    """Inspect the repo-map file universe and persisted receipts without creating state."""
    from tensor_grep.cli import repo_map

    root = path.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    scope = root if root.is_dir() else root.parent
    cap = repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT
    walked = repo_map._iter_repo_files(root, max_files=cap + 1)
    cap_hit = len(walked) > cap
    walked = walked[:cap]
    files = [item for item in walked if repo_map._is_repo_context_file(item, scope)]
    displayed = {_display_path(item, scope): item for item in files}
    session, session_time = _session_entries(scope)
    cache, cache_time = _ast_cache_entries(scope)
    receipts = dict(session)
    for name, digest in cache.items():
        if cache_time >= session_time or name not in receipts:
            receipts[name] = digest

    stale: list[dict[str, str]] = []
    unindexed: list[str] = []
    has_index = bool(receipts)
    current_absolute = {str(item) for item in files}
    for name, current in displayed.items():
        old_digest = receipts.get(str(current))
        if old_digest is None:
            unindexed.append(name)
            if has_index:
                stale.append({"path": name, "reason": "added"})
            continue
        try:
            digest = _digest(current, repo_map._max_parse_bytes())
        except ValueError:
            digest = ""
        except OSError:
            digest = ""
        if digest and digest != old_digest:
            stale.append({"path": name, "reason": "modified"})
    for name in receipts:
        if name not in current_absolute:
            stale.append({"path": _display_path(Path(name), scope), "reason": "deleted"})

    unparsed = _unparsed_files(files, scope)
    caps = ["max_repo_files"] if cap_hit else []
    complete = not stale and not unindexed and not unparsed and not caps
    return {
        "path": str(root),
        "files_total": len(files),
        "stale": sorted(stale, key=lambda item: (item["path"], item["reason"])),
        "unindexed": sorted(unindexed),
        "unparsed": unparsed,
        "complete": complete,
        "caps": caps,
    }


def coverage_command(path: str, *, json_output: bool) -> int:
    import typer

    try:
        payload = check_coverage(Path(path))
    except (OSError, ValueError, sqlite3.Error) as exc:
        typer.echo(f"coverage: {exc}", err=True)
        return 1
    rendered = json.dumps(payload, indent=2)
    if json_output:
        typer.echo(rendered)
    else:
        typer.echo(f"{payload['path']}: {payload['files_total']} files")
        for category in ("stale", "unindexed", "unparsed"):
            for entry in payload[category]:
                if isinstance(entry, dict):
                    typer.echo(f"{category}: {entry['path']} ({entry['reason']})")
                else:
                    typer.echo(f"{category}: {entry}")
        if payload["caps"]:
            typer.echo(f"caps: {', '.join(payload['caps'])}")
        typer.echo("complete" if payload["complete"] else "incomplete")
    return 0 if payload["complete"] else 2
