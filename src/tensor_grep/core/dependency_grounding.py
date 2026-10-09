"""Bounded dependency evidence. Reading metadata never imports project code."""

from __future__ import annotations

import ast
import json
import re
import time
import tomllib
from pathlib import Path
from typing import Any

from tensor_grep.core.pipeline import ConfigurationError

MAX_METADATA_BYTES = 256 * 1024
MAX_DEPENDENCIES = 32
_MANIFESTS = ("pyproject.toml", "package.json", "Cargo.toml")
_LOCKS = ("uv.lock", "poetry.lock", "package-lock.json", "Cargo.lock")


def validate_grounding(mode: str) -> None:
    if mode not in {"off", "local", "registry"}:
        raise ConfigurationError("grounding must be off, local, or registry")


def bounded_metadata(path: Path, root: Path) -> bytes:
    """Reject links, nonregular inputs, and overlarge metadata before parsing."""
    from tensor_grep.cli.symbols_cache_io import read_confined

    return read_confined(root, path, MAX_METADATA_BYTES)


def _load(path: Path, root: Path) -> dict[str, Any]:
    text = bounded_metadata(path, root).decode("utf-8-sig")
    value = json.loads(text) if path.suffix == ".json" else tomllib.loads(text)
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object")
    return value


def _manifest_rows(name: str, data: dict[str, Any]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    if name == "pyproject.toml":
        project = data.get("project", {})
        requirements = list(project.get("dependencies", []))
        for group in project.get("optional-dependencies", {}).values():
            requirements.extend(group)
        requirements.extend(data.get("dependency-groups", {}).get("dev", []))
        for item in requirements:
            if isinstance(item, str):
                match = re.match(r"([A-Za-z0-9][A-Za-z0-9_.-]*)\s*(.*)", item)
                if match:
                    rows.append(("pypi", match[1], match[2] or "unspecified"))
        for dep, constraint in (
            data.get("tool", {}).get("poetry", {}).get("dependencies", {}).items()
        ):
            if dep != "python":
                rows.append(("pypi", dep, str(constraint)))
    elif name == "package.json":
        for section in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        ):
            for dep, constraint in data.get(section, {}).items():
                rows.append(("npm", dep, str(constraint)))
    else:
        sections = [data]
        sections.extend(data.get("target", {}).values())
        for parent in sections:
            for section in ("dependencies", "dev-dependencies", "build-dependencies"):
                for dep, spec in parent.get(section, {}).items():
                    if isinstance(spec, dict):
                        rows.append((
                            "crates",
                            str(spec.get("package", dep)),
                            str(spec.get("version", "workspace/path/git")),
                        ))
                    else:
                        rows.append(("crates", dep, str(spec)))
    return rows


def _lock_rows(name: str, data: dict[str, Any]) -> list[tuple[str, str, str]]:
    if name == "package-lock.json":
        rows = []
        for location, item in data.get("packages", {}).items():
            if "node_modules/" in location and isinstance(item, dict) and item.get("version"):
                rows.append(("npm", location.rsplit("node_modules/", 1)[1], str(item["version"])))
        return rows
    ecosystem = "crates" if name == "Cargo.lock" else "pypi"
    return [
        (ecosystem, str(item["name"]), str(item["version"]))
        for item in data.get("package", [])
        if isinstance(item, dict) and "name" in item and "version" in item
    ]


def _api_evidence(root: Path, ecosystem: str, name: str) -> dict[str, Any]:
    # Distribution-to-import mapping is explicitly uncertain; never import to discover it.
    if not re.fullmatch(r"(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+", name):
        return {"status": "unavailable", "reason": "noncanonical package name"}
    if ecosystem == "npm":
        candidates = [root / "node_modules" / name / "index.d.ts"]
    elif ecosystem == "pypi":
        module = name.replace("-", "_")
        candidates = [root / ".venv" / "Lib" / "site-packages" / module / "__init__.pyi"]
        candidates.extend(
            root / ".venv" / "lib" / version / "site-packages" / module / "__init__.pyi"
            for version in ("python3.11", "python3.12", "python3.13", "python3.14")
        )
    else:
        return {"status": "unavailable", "reason": "no local vendored API evidence"}
    for path in candidates:
        try:
            text = bounded_metadata(path, root).decode("utf-8")
            if path.suffix == ".pyi":
                symbols = [
                    node.name
                    for node in ast.parse(text).body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]
            else:
                symbols = re.findall(
                    r"\b(?:class|function|interface|type|const)\s+([A-Za-z_$][\w$]*)", text
                )
            return {
                "status": "available",
                "file": str(path),
                "provenance": "local-static-type-stub",
                "symbols": symbols[:20],
                "compatibility_verified": False,
            }
        except (OSError, ValueError, SyntaxError, RecursionError):
            continue
    return {
        "status": "unavailable",
        "reason": "no bounded local type stub found",
        "compatibility_verified": False,
    }


def dependency_grounding(
    path: str | Path,
    mode: str = "local",
    *,
    deadline_monotonic: float | None = None,
    imports: list[dict[str, Any]] | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any] | None:
    validate_grounding(mode)
    if mode == "off":
        return None
    root = Path(path).expanduser().resolve()
    if root.is_file():
        root = root.parent
    # A local request remains bounded even without a caller deadline.
    deadline = min(
        deadline_monotonic or float("inf"), time.monotonic() + (5.0 if mode == "registry" else 2.0)
    )
    dependencies: dict[tuple[str, str], dict[str, Any]] = {}
    diagnostics: list[dict[str, str]] = []
    partial = False
    omitted = 0
    for filename in (*_MANIFESTS, *_LOCKS):
        if time.monotonic() >= deadline:
            partial = True
            break
        try:
            data = _load(root / filename, root)
            rows = (
                _manifest_rows(filename, data)
                if filename in _MANIFESTS
                else _lock_rows(filename, data)
            )
            for ecosystem, name, version in rows:
                key = ecosystem, name.lower().replace("_", "-") if ecosystem == "pypi" else name
                if key not in dependencies and len(dependencies) >= MAX_DEPENDENCIES:
                    omitted += 1
                    continue
                row = dependencies.setdefault(
                    key,
                    {
                        "ecosystem": ecosystem,
                        "name": name,
                        "constraints": [],
                        "resolved_versions": [],
                    },
                )
                entry = {
                    "value": version,
                    "file": str(root / filename),
                    "provenance": "manifest" if filename in _MANIFESTS else "lockfile",
                }
                row["constraints" if filename in _MANIFESTS else "resolved_versions"].append(entry)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError) as exc:
            diagnostics.append({
                "file": filename,
                "status": "unreadable_or_invalid",
                "reason": type(exc).__name__,
            })
            partial = True
    records: list[dict[str, Any]] = []
    output_chars = 0
    for _key, row in sorted(dependencies.items()):
        if time.monotonic() >= deadline:
            partial = True
            omitted += len(dependencies) - len(records)
            break
        row["api_evidence"] = _api_evidence(root, row["ecosystem"], row["name"])
        row["import_mapping"] = {
            "status": "ambiguous",
            "reason": "package name does not prove an import-to-distribution mapping",
        }
        if imports:
            token = row["name"].replace("-", "_")
            uses = [
                {"file": entry.get("file"), "provenance": entry.get("provenance", "unknown")}
                for entry in imports[:512]
                if any(token in str(item) for item in entry.get("imports", []))
            ]
            if uses:
                row["import_mapping"]["candidate_uses"] = uses[:5]
        if len({entry["value"] for entry in row["resolved_versions"]}) > 1:
            row["resolution_status"] = "ambiguous_multiple_versions"
        else:
            row["resolution_status"] = "lockfile" if row["resolved_versions"] else "unresolved"
        cost = len(json.dumps(row))
        if max_tokens is not None and output_chars + cost > max_tokens * 4:
            omitted += len(dependencies) - len(records)
            break
        output_chars += cost
        records.append(row)
    result: dict[str, Any] = {
        "mode": mode,
        "status": "partial" if partial or omitted else "complete",
        "dependencies": records,
        "omitted_dependencies": omitted,
        "diagnostics": diagnostics,
        "api_compatibility_verified": False,
    }
    if mode == "registry":
        from tensor_grep.core.registry_grounding import registry_receipts

        result["registry"] = registry_receipts(records, deadline_monotonic=deadline)
    return result
