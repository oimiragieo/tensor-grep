"""Bounded, read-only diagnosis of a selected checkout's virtual environment.

This deliberately does not import or execute any code from the selected environment.
"""

from __future__ import annotations

import json
import tomllib
from email.parser import Parser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from packaging.utils import canonicalize_name
from packaging.version import Version

from tensor_grep.cli.symbols_cache_io import read_confined

_READ_LIMIT = 1024 * 1024
_ENTRY_LIMIT = 512


def _read(path: Path, root: Path) -> str:
    if not path.resolve().is_relative_to(root):
        raise ValueError("metadata leaves selected checkout")
    try:
        data = read_confined(root, path, _READ_LIMIT)
    except OSError as exc:
        if f"file exceeds {_READ_LIMIT} bytes:" in str(exc):
            raise ValueError("metadata exceeds 1 MiB limit") from exc
        raise
    return data.decode("utf-8-sig")


def _version(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing or empty environment/project version")
    Version(value)
    return value


def diagnose_checkout_environment(path: Path) -> dict[str, Any]:
    root = path.expanduser().resolve()
    result: dict[str, Any] = {"root": str(root), "read_only": True, "status": "missing_project"}
    try:
        project = tomllib.loads(_read(root / "pyproject.toml", root)).get("project", {})
        if canonicalize_name(str(project.get("name", ""))) != "tensor-grep":
            result["status"] = "unrelated_project"
            return result
        result["source_version"] = _version(project.get("version"))
        venv = root / ".venv"
        if not venv.is_dir():
            result["status"] = "missing_environment"
            return result
        # Explicit platform layouts; never recursively walk arbitrary checkout directories.
        sites = [venv / "Lib" / "site-packages"]
        lib = venv / "lib"
        if lib.is_dir():
            for index, child in enumerate(lib.iterdir()):
                if index >= _ENTRY_LIMIT:
                    raise ValueError("environment directory inventory truncated")
                if child.name.startswith("python"):
                    sites.append(child / "site-packages")
        matches = []
        entry_count = 0
        for site in sites:
            if not site.is_dir():
                continue
            for child in site.iterdir():
                entry_count += 1
                if entry_count > _ENTRY_LIMIT:
                    raise ValueError("environment metadata inventory truncated")
                if child.name.lower().startswith((
                    "tensor_grep-",
                    "tensor-grep-",
                )) and child.name.endswith(".dist-info"):
                    matches.append(child)
        if not matches:
            result["status"] = "missing_package"
            return result
        if len(matches) != 1:
            result["status"] = "ambiguous_environment"
            result["metadata_paths"] = [str(item) for item in matches]
            return result
        metadata = matches[0]
        headers = Parser().parsestr(_read(metadata / "METADATA", root))
        if (
            len(headers.get_all("Name", [])) != 1
            or canonicalize_name(headers.get("Name", "")) != "tensor-grep"
        ):
            result["status"] = "unrelated_environment"
            return result
        if len(headers.get_all("Version", [])) != 1:
            raise ValueError("missing or ambiguous package metadata version")
        result["installed_version"] = _version(headers.get("Version"))
        result["metadata_path"] = str(metadata)
        direct_path = metadata / "direct_url.json"
        if not direct_path.exists():
            result["status"] = "unverified_provenance"
            return result
        direct = json.loads(_read(direct_path, root))
        url = urlsplit(str(direct.get("url", "")))
        if (
            url.scheme != "file"
            or url.netloc not in ("", "localhost")
            or direct.get("dir_info", {}).get("editable") is not True
        ):
            result["status"] = "unrelated_environment"
            return result
        origin = unquote(url.path)
        if len(origin) >= 3 and origin[0] == "/" and origin[2] == ":":
            origin = origin[1:]
        result["editable_origin"] = origin
        if Path(origin).resolve() != root:
            result["status"] = "unrelated_environment"
            return result
        result["status"] = (
            "current"
            if result["installed_version"] == result["source_version"]
            else "stale_editable"
        )
        if result["status"] == "stale_editable":
            result["remediation"] = (
                "Run `uv run --refresh-package tensor-grep tg --version` in the selected checkout. For checkout scripts, use that checkout's .venv interpreter after refreshing; do not use an unrelated harness interpreter."
            )
    except FileNotFoundError as exc:
        result["status"] = (
            "missing_metadata" if (root / "pyproject.toml").exists() else "missing_project"
        )
        result["error"] = str(exc)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError) as exc:
        result["status"] = "ambiguous_environment"
        result["error"] = str(exc)
    return result
