from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlparse

from tensor_grep.cli import repo_map as _repo_map
from tensor_grep.cli.lsp_session import proof_request as _proof_request


def _normalize_semantic_provider(provider: str) -> str:
    normalized = str(provider).strip().lower() or "native"
    if normalized not in {"native", "lsp", "hybrid"}:
        return "native"
    return normalized


def _language_for_path(path: str | _repo_map.Path) -> str:
    # PATH A Stage 0 honesty fix: this used to default unconditionally to "python" for ANY
    # suffix it didn't recognize (e.g. an lsp-provenance fallback label for a .go/.rb/.txt
    # file would be silently stamped "lsp-python") -- dishonest, and only reachable today via
    # the `_provider_language_for_path(...) or _language_for_path(...)` fallback chain for
    # suffixes _provider_language_for_path *also* doesn't recognize. Route through the
    # registry and fall back to "unknown" instead. javascript/typescript are intentionally
    # collapsed to the single "javascript" label here (unchanged from before) since callers of
    # this function have never distinguished the two.
    spec = _repo_map.lang_registry.spec_for_path(path)
    if spec is None:
        return "unknown"
    if spec.language_id in ("javascript", "typescript"):
        return "javascript"
    return spec.language_id


def _provider_language_for_path(path: str | _repo_map.Path) -> str | None:
    suffix = _repo_map.Path(path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".js", ".jsx", ".mjs", ".cjs"}:
        return "javascript"
    if suffix in _repo_map._TS_SUFFIXES:
        return "typescript"
    if suffix in _repo_map._RUST_SUFFIXES:
        return "rust"
    if suffix == ".go":
        return "go"
    if suffix == ".java":
        return "java"
    if suffix == ".c":
        return "c"
    if suffix in {".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}:
        return "cpp"
    if suffix == ".cs":
        return "csharp"
    if suffix == ".php":
        return "php"
    if suffix in {".kt", ".kts"}:
        return "kotlin"
    if suffix == ".swift":
        return "swift"
    if suffix == ".lua":
        return "lua"
    return None


def _path_from_lsp_file_uri(uri: str) -> _repo_map.Path | None:
    if not uri.startswith("file://"):
        return None
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return _repo_map._resolved_input_path(path)


def _lsp_symbol_kind_name(value: object) -> str:
    mapping = {
        5: "class",
        12: "function",
        6: "method",
        2: "module",
        3: "namespace",
        13: "variable",
        14: "constant",
        10: "enum",
        23: "struct",
        11: "interface",
    }
    if isinstance(value, int):
        return mapping.get(value, "symbol")
    return "symbol"


def _symbol_character_in_file(path: _repo_map.Path, line_number: int, symbol: str) -> int:
    try:
        lines = _repo_map._read_text_with_receipt(path).splitlines()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return 0
    if line_number <= 0 or line_number > len(lines):
        return 0
    line = lines[line_number - 1]
    return max(0, line.find(symbol))


def _provider_languages_for_symbol(
    repo_map: dict[str, Any],
    symbol: str,
    definitions: list[dict[str, Any]] | None = None,
) -> list[str]:
    languages: set[str] = set()
    for current in definitions or []:
        language = _repo_map._provider_language_for_path(str(current.get("file", "")))
        if language:
            languages.add(language)
    if languages:
        return sorted(languages)
    for current in repo_map.get("symbols", []):
        if str(current.get("name", "")) == symbol:
            language = _repo_map._provider_language_for_path(str(current.get("file", "")))
            if language:
                languages.add(language)
    if languages:
        return sorted(languages)
    for current in repo_map.get("files", []):
        language = _repo_map._provider_language_for_path(str(current))
        if language:
            languages.add(language)
    return sorted(languages)


def _provider_status_snapshot(
    repo_root: _repo_map.Path,
    *,
    semantic_provider: str,
    languages: list[str],
    fallback_used: bool = False,
) -> dict[str, Any]:
    normalized_provider = _repo_map._normalize_semantic_provider(semantic_provider)
    providers = [
        _repo_map._EXTERNAL_LSP_PROVIDER_MANAGER.provider_status(
            language=language, workspace_root=repo_root
        )
        for language in languages
    ]
    return {
        "mode": normalized_provider,
        "fallback_used": bool(fallback_used),
        "providers": providers,
    }


def _configured_lsp_operation_budget_seconds() -> float:
    raw_value = _repo_map.os.environ.get(_repo_map._LSP_OPERATION_BUDGET_ENV_VAR)
    if raw_value is None:
        return _repo_map._DEFAULT_LSP_OPERATION_BUDGET_SECONDS
    try:
        return max(float(raw_value), 0.0)
    except (TypeError, ValueError):
        return _repo_map._DEFAULT_LSP_OPERATION_BUDGET_SECONDS


def _wait_for_lsp_readiness(
    client: Any,
    deadline_monotonic: float,
    *,
    probe: Any = None,
    no_progress_grace_seconds: float = 1.0,
) -> None:
    """Best-effort P0-2 readiness gate. Tolerates clients without wait_until_ready (duck-typed
    fakes/third-party stubs) â€” the gate is a pre-step that improves first-query completeness;
    it must never be a new failure mode for the query itself."""
    waiter = getattr(client, "wait_until_ready", None)
    if waiter is None:
        return
    waiter(
        deadline_monotonic,
        probe=probe,
        no_progress_grace_seconds=no_progress_grace_seconds,
    )


def _lsp_operation_deadline() -> float:
    return _repo_map.time.monotonic() + _repo_map._configured_lsp_operation_budget_seconds()


def _remaining_lsp_budget_seconds(deadline_monotonic: float) -> float:
    return max(0.0, deadline_monotonic - _repo_map.time.monotonic())


def _run_lsp_with_operation_budget(
    client: Any,
    deadline_monotonic: float,
    operation: Callable[[], Any],
) -> Any:
    remaining = _repo_map._remaining_lsp_budget_seconds(deadline_monotonic)
    if remaining <= 0:
        raise _repo_map.LSPTransportError("LSP operation budget exhausted")
    original_request_timeout = getattr(client, "request_timeout_seconds", None)
    original_initialize_timeout = getattr(client, "initialize_timeout_seconds", None)
    try:
        if original_request_timeout is not None:
            client.request_timeout_seconds = min(float(original_request_timeout), remaining)
        if original_initialize_timeout is not None:
            client.initialize_timeout_seconds = min(float(original_initialize_timeout), remaining)
        return operation()
    finally:
        if original_request_timeout is not None:
            client.request_timeout_seconds = original_request_timeout
        if original_initialize_timeout is not None:
            client.initialize_timeout_seconds = original_initialize_timeout


def _attach_lsp_evidence_status(
    payload: dict[str, Any],
    *,
    semantic_provider: str,
    lsp_count: int,
    fallback_used: bool,
) -> None:
    normalized_provider = _repo_map._normalize_semantic_provider(semantic_provider)
    if normalized_provider == "native":
        payload["lsp_evidence_status"] = "not_requested"
        payload["lsp_proof"] = False
        payload["not_lsp_proof_reason"] = "Semantic provider mode is native."
        return
    if lsp_count > 0:
        payload["lsp_evidence_status"] = "lsp_proof"
        payload["lsp_proof"] = True
        payload.pop("not_lsp_proof_reason", None)
        return
    if fallback_used:
        payload["lsp_evidence_status"] = "fallback_native"
        payload["lsp_proof"] = False
        payload["not_lsp_proof_reason"] = (
            "External LSP provider returned no usable evidence; native fallback was used."
        )
        return
    payload["lsp_evidence_status"] = "no_lsp_evidence"
    payload["lsp_proof"] = False
    payload["not_lsp_proof_reason"] = "External LSP provider returned no usable evidence."


def _is_lsp_proof_row(row: dict[str, Any]) -> bool:
    provenance = str(row.get("provenance", ""))
    return (
        row.get("lsp_provider_response") is True
        and provenance.startswith("lsp-")
        and "-fallback" not in provenance
    )


def _merge_navigation_duplicate(
    existing: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    existing_is_lsp = _repo_map._is_lsp_proof_row(existing)
    candidate_is_lsp = _repo_map._is_lsp_proof_row(candidate)
    if candidate_is_lsp and not existing_is_lsp:
        merged = {**existing, **candidate}
    elif existing_is_lsp and not candidate_is_lsp:
        merged = {**candidate, **existing}
    else:
        merged = {**existing, **candidate}
    if _repo_map._is_lsp_proof_row(merged):
        merged["lsp_proof"] = True
    return dict(merged)


def _lsp_proof_row_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if _repo_map._is_lsp_proof_row(row))


def _copy_lsp_evidence_status(payload: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("lsp_evidence_status", "lsp_proof", "not_lsp_proof_reason"):
        if key in source:
            payload[key] = source[key]


def _merge_agreement_status(
    *,
    semantic_provider: str,
    native_count: int,
    lsp_count: int,
    merged_count: int,
    fallback_used: bool,
) -> dict[str, Any]:
    normalized_provider = _repo_map._normalize_semantic_provider(semantic_provider)
    if normalized_provider == "native":
        agreement_status = "native-only"
    elif fallback_used and lsp_count == 0:
        agreement_status = "fallback-native"
    elif normalized_provider == "lsp":
        if lsp_count == 0:
            agreement_status = "native-fallback"
        elif native_count > lsp_count:
            # native found strictly MORE refs than the LSP index proved -> the LSP result is
            # incomplete. Report the divergence honestly instead of a clean lsp-only proof (P0-1:
            # dogfood's 2-of-14 partial was masked as authoritative lsp-only). "diverged" was
            # structurally unreachable in lsp mode before this. When native<=lsp (they agree, or LSP
            # proved everything native saw) it stays lsp-only.
            agreement_status = "diverged"
        else:
            agreement_status = "lsp-only"
    elif native_count > 0 and lsp_count > 0 and merged_count == native_count == lsp_count:
        agreement_status = "agreed"
    elif native_count > 0 and lsp_count > 0:
        agreement_status = "diverged"
    elif lsp_count > 0:
        agreement_status = "lsp-only"
    else:
        agreement_status = "native-only"
    return {
        "mode": normalized_provider,
        "agreement_status": agreement_status,
        "native_count": native_count,
        "lsp_count": lsp_count,
        "merged_count": merged_count,
        "fallback_used": fallback_used,
    }


def _default_provider_metadata(
    repo_root: _repo_map.Path,
    repo_map: dict[str, Any],
    symbol: str,
    *,
    semantic_provider: str,
    definitions: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized_provider = _repo_map._normalize_semantic_provider(semantic_provider)
    return (
        _repo_map._merge_agreement_status(
            semantic_provider=normalized_provider,
            native_count=len(definitions or []),
            lsp_count=0,
            merged_count=len(definitions or []),
            fallback_used=normalized_provider == "lsp",
        ),
        _repo_map._provider_status_snapshot(
            repo_root,
            semantic_provider=normalized_provider,
            languages=_repo_map._provider_languages_for_symbol(repo_map, symbol, definitions),
            fallback_used=normalized_provider == "lsp",
        ),
    )


def _external_workspace_symbols(
    repo_root: _repo_map.Path,
    symbol: str,
    *,
    repo_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    deadline_monotonic = _repo_map._lsp_operation_deadline()
    current_repo_map = repo_map or _repo_map.build_repo_map(repo_root)
    languages = _repo_map._provider_languages_for_symbol(current_repo_map, symbol)
    for language in sorted(languages):
        if _repo_map._remaining_lsp_budget_seconds(deadline_monotonic) <= 0:
            break
        try:
            client = _repo_map._EXTERNAL_LSP_PROVIDER_MANAGER.get_client(
                language=language, workspace_root=repo_root
            )

            # P0-2 (zero-grace variant): wait out an advertised indexing round; silent servers
            # proceed instantly.
            _repo_map._wait_for_lsp_readiness(
                client, deadline_monotonic, no_progress_grace_seconds=0.0
            )

            def _request_symbols(
                *,
                current_client: Any = client,
                query: str = symbol,
            ) -> Any:
                return _proof_request(current_client, "workspace/symbol", {"query": query})

            result = _repo_map._run_lsp_with_operation_budget(
                client,
                deadline_monotonic,
                _request_symbols,
            )
        except (FileNotFoundError, _repo_map.LSPTransportError, ValueError):
            continue
        if not isinstance(result, list):
            continue
        for current in result:
            if not isinstance(current, dict) or str(current.get("name", "")) != symbol:
                continue
            location = current.get("location")
            if not isinstance(location, dict):
                continue
            payload_range = location.get("range")
            if not isinstance(payload_range, dict):
                continue
            payload_start = payload_range.get("start")
            payload_end = payload_range.get("end")
            if not isinstance(payload_start, dict) or not isinstance(payload_end, dict):
                continue
            uri = str(location.get("uri", ""))
            resolved_path = _repo_map._path_from_lsp_file_uri(uri)
            if resolved_path is None:
                continue
            matches.append({
                "name": symbol,
                "kind": _repo_map._lsp_symbol_kind_name(current.get("kind")),
                "file": str(resolved_path),
                "line": int(payload_start.get("line") or 0) + 1,
                "end_line": int(payload_end.get("line") or payload_start.get("line") or 0) + 1,
                "provenance": f"lsp-{language}",
                "lsp_provider_response": True,
                "lsp_operation": "workspace/symbol",
            })
    matches.sort(key=lambda item: (str(item["file"]), int(item["line"]), str(item["kind"])))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for current in matches:
        key = (
            str(current["file"]),
            int(current["line"]),
            int(current["end_line"]),
            str(current["kind"]),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(current)
    return deduped


def _iter_lsp_definition_locations(result: object) -> list[tuple[str, dict[str, Any]]]:
    raw_items: list[object]
    if isinstance(result, list):
        raw_items = result
    elif isinstance(result, dict):
        raw_items = [result]
    else:
        return []

    locations: list[tuple[str, dict[str, Any]]] = []
    for current in raw_items:
        if not isinstance(current, dict):
            continue
        if isinstance(current.get("targetUri"), str):
            uri = str(current["targetUri"])
            payload_range = current.get("targetSelectionRange") or current.get("targetRange")
        else:
            uri = str(current.get("uri", ""))
            payload_range = current.get("range")
        if not uri or not isinstance(payload_range, dict):
            continue
        start = payload_range.get("start")
        end = payload_range.get("end")
        if not isinstance(start, dict) or not isinstance(end, dict):
            continue
        locations.append((uri, payload_range))
    return locations


def _dedupe_lsp_definition_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _repo_map._dedupe_definition_rows(rows)


def _definition_file_display_path(file_value: Any) -> str:
    file_raw = str(file_value or "")
    if file_raw.startswith("file:"):
        parsed = urlparse(file_raw)
        if parsed.scheme == "file":
            path = unquote(parsed.path)
            if _repo_map.os.name == "nt" and re.match(r"^/[A-Za-z]:", path):
                path = path[1:]
            if parsed.netloc and parsed.netloc not in {"", "localhost"}:
                path = f"//{parsed.netloc}{path}"
            file_raw = path

    try:
        return str(_repo_map._resolved_input_path(file_raw))
    except (OSError, ValueError):
        return file_raw


def _definition_file_dedupe_key(file_value: Any) -> str:
    file_key = _repo_map._definition_file_display_path(file_value)

    if _repo_map.os.name == "nt":
        file_key = file_key.lower().replace("/", "\\")
    return file_key


def _definition_dedupe_key(row: dict[str, Any]) -> tuple[str, int, str]:
    # LSP backends may emit file:// URIs or slash-normalized paths while the
    # native backend emits OS-native paths. Normalize before merging hybrid rows.
    file_key = _repo_map._definition_file_dedupe_key(row.get("file"))
    line = int(row.get("line", row.get("start_line", 0)) or 0)
    return (file_key, line, str(row.get("name", "")))


def _merge_definition_duplicate(
    existing: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    existing_is_lsp = _repo_map._is_lsp_proof_row(existing)
    candidate_is_lsp = _repo_map._is_lsp_proof_row(candidate)
    if candidate_is_lsp and not existing_is_lsp:
        merged = dict(existing)
        merged.update(candidate)
        for key in ("text", "source", "source_line"):
            if key in existing and key not in merged:
                merged[key] = existing[key]
        return merged
    if existing_is_lsp and not candidate_is_lsp:
        merged = dict(candidate)
        merged.update(existing)
        for key in ("text", "source", "source_line"):
            if key in candidate and key not in merged:
                merged[key] = candidate[key]
        return merged
    return dict(existing)


def _dedupe_definition_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: dict[tuple[str, int, str], dict[str, Any]] = {}
    for current in rows:
        current = dict(current)
        current["file"] = _repo_map._definition_file_display_path(current.get("file"))
        key = _repo_map._definition_dedupe_key(current)
        if key in seen:
            seen[key] = _repo_map._merge_definition_duplicate(seen[key], current)
            continue
        seen[key] = dict(current)
    deduped = list(seen.values())
    deduped.sort(key=lambda item: (str(item["file"]), int(item["line"]), str(item["kind"])))
    return deduped


def _external_definitions(
    repo_root: _repo_map.Path,
    symbol: str,
    native_definitions: list[dict[str, Any]],
    *,
    repo_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    workspace_matches = _repo_map._external_workspace_symbols(repo_root, symbol, repo_map=repo_map)
    if workspace_matches or not native_definitions:
        return workspace_matches

    definitions: list[dict[str, Any]] = []
    deadline_monotonic = _repo_map._lsp_operation_deadline()
    for native_definition in native_definitions:
        if _repo_map._remaining_lsp_budget_seconds(deadline_monotonic) <= 0:
            break
        current_path = _repo_map._resolved_input_path(str(native_definition.get("file", "")))
        if not _repo_map._is_file_with_receipt(current_path):
            continue
        language = _repo_map._provider_language_for_path(
            current_path
        ) or _repo_map._language_for_path(current_path)
        try:
            client = _repo_map._EXTERNAL_LSP_PROVIDER_MANAGER.get_client(
                language=language, workspace_root=repo_root
            )
            document_uri = current_path.as_uri()
            document_text = _repo_map._read_text_with_receipt(current_path)
            definition_line = int(
                native_definition.get(
                    "line",
                    native_definition.get("start_line", 1),
                )
                or 1
            )
            definition_character = _repo_map._symbol_character_in_file(
                current_path,
                definition_line,
                symbol,
            )

            def _ensure_document(
                *,
                current_client: Any = client,
                uri: str = document_uri,
                text: str = document_text,
                language_id: str = language,
            ) -> None:
                current_client.ensure_document(uri=uri, text=text, language_id=language_id)

            _repo_map._run_lsp_with_operation_budget(client, deadline_monotonic, _ensure_document)

            # P0-2 (zero-grace variant): progress-advertising servers wait for their indexing
            # round to end; silent servers proceed instantly (no latency tax on the 2s budget).
            _repo_map._wait_for_lsp_readiness(
                client, deadline_monotonic, no_progress_grace_seconds=0.0
            )

            def _request_definition(
                *,
                current_client: Any = client,
                uri: str = document_uri,
                line: int = definition_line,
                character: int = definition_character,
            ) -> Any:
                return _proof_request(
                    current_client,
                    "textDocument/definition",
                    {
                        "textDocument": {"uri": uri},
                        "position": {
                            "line": line - 1,
                            "character": character,
                        },
                    },
                )

            result = _repo_map._run_lsp_with_operation_budget(
                client,
                deadline_monotonic,
                _request_definition,
            )
        except (
            FileNotFoundError,
            OSError,
            UnicodeDecodeError,
            _repo_map.LSPTransportError,
            ValueError,
        ):
            continue

        for uri, payload_range in _repo_map._iter_lsp_definition_locations(result):
            resolved_path = _repo_map._path_from_lsp_file_uri(uri)
            if resolved_path is None:
                continue
            start = dict(payload_range.get("start", {}))
            end = dict(payload_range.get("end", {}))
            line = int(start.get("line") or 0) + 1
            definitions.append({
                "name": symbol,
                "kind": str(native_definition.get("kind", "symbol")),
                "file": str(resolved_path),
                "line": line,
                "start_line": line,
                "end_line": int(end.get("line") or start.get("line") or 0) + 1,
                "provenance": f"lsp-{language}",
                "lsp_provider_response": True,
                "lsp_proof": True,
                "lsp_operation": "textDocument/definition",
                "lsp_resolution_basis": "native-definition-anchor",
            })

    return _repo_map._dedupe_lsp_definition_rows(definitions) or workspace_matches


def _external_references(
    repo_root: _repo_map.Path, symbol: str, definitions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    deadline_monotonic = _repo_map._lsp_operation_deadline()
    for definition in definitions:
        if _repo_map._remaining_lsp_budget_seconds(deadline_monotonic) <= 0:
            break
        current_path = _repo_map._resolved_input_path(str(definition["file"]))
        language = _repo_map._provider_language_for_path(
            current_path
        ) or _repo_map._language_for_path(current_path)
        try:
            client = _repo_map._EXTERNAL_LSP_PROVIDER_MANAGER.get_client(
                language=language, workspace_root=repo_root
            )
            document_uri = current_path.as_uri()
            document_text = _repo_map._read_text_with_receipt(current_path)
            definition_line = int(definition.get("line", 1))
            definition_character = _repo_map._symbol_character_in_file(
                current_path, definition_line, symbol
            )

            def _ensure_document(
                *,
                current_client: Any = client,
                uri: str = document_uri,
                text: str = document_text,
                language_id: str = language,
            ) -> None:
                current_client.ensure_document(uri=uri, text=text, language_id=language_id)

            _repo_map._run_lsp_with_operation_budget(
                client,
                deadline_monotonic,
                _ensure_document,
            )

            # P0-2 readiness gate: wait for the server's workspace index to settle before the
            # references query â€” firing immediately answers from a half-built index (the 2-of-14
            # under-return). Probe = workspace/symbol hit-count stability for servers that never
            # emit workDoneProgress. A timeout here is honest-partial territory (the P0-1 union +
            # diverged stamps make the result truthful); it must NOT read as a provider failure.
            def _symbol_count_probe(
                *, current_client: Any = client, query: str = symbol
            ) -> int | None:
                hits = current_client.request("workspace/symbol", {"query": query})
                return len(hits) if isinstance(hits, list) else 0

            def _bounded_probe(
                *,
                current_client: Any = client,
                deadline: float = deadline_monotonic,
                probe_fn: Any = _symbol_count_probe,
            ) -> int | None:
                probed = _repo_map._run_lsp_with_operation_budget(
                    current_client, deadline, probe_fn
                )
                return probed if isinstance(probed, int) else None

            _repo_map._wait_for_lsp_readiness(client, deadline_monotonic, probe=_bounded_probe)

            def _request_references(
                *,
                current_client: Any = client,
                uri: str = document_uri,
                line: int = definition_line,
                character: int = definition_character,
            ) -> Any:
                return _proof_request(
                    current_client,
                    "textDocument/references",
                    {
                        "textDocument": {"uri": uri},
                        "position": {
                            "line": line - 1,
                            "character": character,
                        },
                        "context": {"includeDeclaration": True},
                    },
                )

            result = _repo_map._run_lsp_with_operation_budget(
                client,
                deadline_monotonic,
                _request_references,
            )
        except (
            FileNotFoundError,
            OSError,
            UnicodeDecodeError,
            _repo_map.LSPTransportError,
            ValueError,
        ):
            continue
        if not isinstance(result, list):
            continue
        for current in result:
            if not isinstance(current, dict):
                continue
            location_range = current.get("range")
            if not isinstance(location_range, dict):
                continue
            start = location_range.get("start")
            end = location_range.get("end")
            if not isinstance(start, dict) or not isinstance(end, dict):
                continue
            uri = str(current.get("uri", ""))
            resolved_path = _repo_map._path_from_lsp_file_uri(uri)
            if resolved_path is None:
                continue
            try:
                lines = _repo_map._read_text_with_receipt(resolved_path).splitlines()
            except (FileNotFoundError, OSError, UnicodeDecodeError):
                lines = []
            line_number = int(start.get("line", 0)) + 1
            text = lines[line_number - 1].strip() if 0 < line_number <= len(lines) else symbol
            references.append({
                "name": symbol,
                "kind": "reference",
                "file": str(resolved_path),
                "line": line_number,
                "end_line": int(end.get("line") or start.get("line") or 0) + 1,
                "text": text,
                "provenance": f"lsp-{language}",
                "lsp_provider_response": True,
                "lsp_proof": True,
                "lsp_operation": "textDocument/references",
            })
    references.sort(key=lambda item: (str(item["file"]), int(item["line"])))
    deduped: list[dict[str, Any]] = []
    seen_refs: set[tuple[str, int, int]] = set()
    for current in references:
        key = (str(current["file"]), int(current["line"]), int(current["end_line"]))
        if key in seen_refs:
            continue
        seen_refs.add(key)
        deduped.append(current)
    return deduped
