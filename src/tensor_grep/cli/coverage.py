"""Read-only coverage inventory for repo-map sessions and AST cache entries."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
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


def _session_entries(root: Path, floor: Path | None = None) -> tuple[dict[str, str], float]:
    from tensor_grep.cli import session_store
    from tensor_grep.cli.session_provenance import read_session_payload, verified_session

    session_root = session_store._resolve_root(root)
    if floor is not None and not session_root.is_relative_to(floor):
        return {}, 0.0
    records = session_store._load_index(session_root)
    if not records:
        return {}, 0.0
    if len(records) > 256:
        raise ValueError("session index exceeds coverage limit")
    result: dict[str, str] = {}
    stamp = 0.0
    for record in sorted(records, key=lambda item: item.created_at, reverse=True):
        session_path = session_store._session_payload_path(session_root, record.session_id)
        payload = read_session_payload(session_root, session_path)
        if payload.get("session_id") != record.session_id or not verified_session(
            payload, session_root
        ):
            raise ValueError("session snapshot authentication failed")
        entries = payload.get("snapshot", [])
        if not isinstance(entries, list):
            raise ValueError("invalid session snapshot")
        for entry in entries:
            if isinstance(entry, dict) and entry.get("path") and entry.get("content_sha256"):
                candidate = Path(str(entry["path"])).resolve()
                if candidate.is_relative_to(root):
                    result.setdefault(str(candidate), str(entry["content_sha256"]))
        try:
            stamp = max(stamp, datetime.fromisoformat(record.created_at).timestamp())
        except (TypeError, ValueError, OverflowError):
            pass
    return result, stamp


def _ast_cache_entries(root: Path) -> tuple[dict[str, str], float]:
    cache_path = root / ".tg_cache" / "symbols_v1" / "metadata.sqlite3"
    if not cache_path.is_file():
        return {}, 0.0
    from tensor_grep.cli import session_daemon_trust, symbols_cache, symbols_cache_auth

    key_path = symbols_cache_auth._machine_key_path()
    if key_path.is_relative_to(root) or key_path.resolve().is_relative_to(root):
        return {}, 0.0
    secret = session_daemon_trust._read_user_secret(key_path)
    if secret is None:
        return {}, 0.0
    connection = symbols_cache._database(root, cache_path)
    try:
        deadline = time.monotonic() + 0.25
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        generation = connection.execute(
            "SELECT id, created, complete FROM generations WHERE scope=? ORDER BY created DESC LIMIT 1",
            (str(root),),
        ).fetchone()
        if generation is None or not generation[2]:
            return {}, 0.0
        rows = connection.execute(
            "SELECT f.path, f.content, f.entry_key, e.payload, e.receipt "
            "FROM files AS f LEFT JOIN entries AS e ON e.key=f.entry_key "
            "AND e.generation=f.generation WHERE f.scope=? AND f.generation=? LIMIT 8193",
            (str(root), int(generation[0])),
        ).fetchall()
        if len(rows) > 8192:
            raise sqlite3.DatabaseError("symbol cache file count exceeds limit")
        entries: dict[str, str] = {}
        for path, digest, key, payload, receipt in rows:
            candidate = Path(str(path)).resolve()
            if not candidate.is_relative_to(root) or payload is None or receipt is None:
                continue
            symbols_cache._decode_product(str(payload), str(receipt), candidate, secret, str(key))
            entries[str(candidate)] = str(digest)
        return entries, float(generation[1])
    finally:
        connection.close()


def _unparsed_files(files: list[Path], root: Path) -> list[dict[str, str]]:
    from tensor_grep.cli import lang_registry, repo_map
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
        name = _display_path(path, root)
        if spec is None:
            reasons[name] = "missing_grammar"
            continue
        parser = spec.parser_for_path(path) if spec.parser_for_path is not None else None
        if spec.parser_for_path is not None and parser is None:
            reasons[name] = "missing_grammar"
        elif path.suffix.lower() != ".py" and parser is not None and name not in reasons:
            try:
                with path.open("rb") as handle:
                    source = handle.read(repo_map._max_parse_bytes() + 1)
                if len(source) > repo_map._max_parse_bytes():
                    reasons[name] = "oversize"
                elif parser.parse(source).root_node.has_error:
                    reasons[name] = "parse_error"
            except OSError:
                reasons[name] = "unreadable"
    return [{"path": path, "reason": reason} for path, reason in sorted(reasons.items())]


def check_coverage(path: Path, *, confinement_root: Path | None = None) -> dict[str, Any]:
    """Inspect the repo-map file universe and persisted receipts without creating state."""
    from tensor_grep.cli import repo_map

    root = path.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    scope = root if root.is_dir() else root.parent
    cap = repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT
    unreadable = repo_map._UnreadablePathFlag()
    walked = repo_map._iter_repo_files(root, max_files=cap + 1, unreadable_hit=unreadable)
    cap_hit = len(walked) > cap
    walked = walked[:cap]
    files = [item for item in walked if repo_map._is_repo_context_file(item, scope)]
    displayed = {_display_path(item, scope): item for item in files}
    session, session_time = _session_entries(scope, confinement_root)
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
            stale.append({"path": name, "reason": "modified"})
            continue
        except OSError:
            digest = ""
        if digest and digest != old_digest:
            stale.append({"path": name, "reason": "modified"})
    for name in receipts:
        if name not in current_absolute and not Path(name).exists():
            stale.append({"path": _display_path(Path(name), scope), "reason": "deleted"})

    unparsed = _unparsed_files(files, scope)
    caps = ["max_repo_files"] if cap_hit else []
    if unreadable.hit:
        caps.append("unreadable_paths")
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
