import json
import re

from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult

JSON_OUTPUT_VERSION = 1


def _column_for_match(match: MatchLine, config: SearchConfig | None = None) -> int | None:
    """Return 1-based column of the match within its line, or None when not derivable.

    Priority:
    1. match.range["start"]["column"] (0-based → 1-based), provided by ast-grep backend.
    2. Pattern-based scan of match.text using config (mirrors RipgrepFormatter logic).
    3. None — caller should omit or null the field rather than emit a wrong value.
    """
    if match.range is not None:
        start = match.range.get("start")
        if isinstance(start, dict):
            column = start.get("column")
            if isinstance(column, int):
                return column + 1

    if config is None:
        return None

    pattern = config.query_pattern or ""
    if not pattern and config.regexp:
        pattern = config.regexp[0]
    if not pattern:
        return None

    if config.fixed_strings:
        index = match.text.find(pattern)
    else:
        try:
            flags = (
                re.IGNORECASE
                if config.ignore_case or (config.smart_case and pattern.islower())
                else 0
            )
            found = re.search(pattern, match.text, flags=flags)
            index = -1 if found is None else found.start()
        except re.error:
            index = match.text.find(pattern)
    if index < 0:
        return None
    # ripgrep/--vimgrep/--json columns are BYTE offsets, not character indices:
    # advance by the UTF-8 width of the text before the match (audit MED parity).
    return len(match.text[:index].encode("utf-8")) + 1


def _routing_gpu_chunk_plan(result: SearchResult) -> list[dict[str, int]]:
    return [
        {"device_id": device_id, "chunk_mb": chunk_mb}
        for device_id, chunk_mb in result.routing_gpu_chunk_plan_mb
    ]


def _match_payload(match: MatchLine, config: SearchConfig | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "file": match.file,
        # audit M1: keep BOTH `line` (the native plain-`--json` field) and `line_number`
        # so a consumer keyed on `matches[].line` does not break the moment `--stats`
        # routes through this Python serializer instead of the native binary. Mirrors
        # NdjsonFormatter.format below.
        "line": match.line_number,
        "line_number": match.line_number,
        "text": match.text,
    }
    column = _column_for_match(match, config)
    if column is not None:
        payload["column"] = column
    if match.range is not None:
        payload["range"] = match.range
    if match.meta_variables is not None:
        payload["metaVariables"] = match.meta_variables
    # audit q6: mirror RipgrepFormatter._submatch_columns -- rg's per-occurrence byte offsets
    # (match.submatches) were parsed onto MatchLine but never read here, so --json lost
    # column/offset info and could not report multiple occurrences on one line. Emit the same
    # dicts rg's own --json submatches use (keys: "match"/"start"/"end"); omit the key entirely
    # (no null/empty noise) for non-rg backends / context lines that have none.
    if match.submatches:
        subs = [dict(sub) for sub in match.submatches if isinstance(sub, dict)]
        if subs:
            payload["submatches"] = subs
    if getattr(match, "container", None) is not None:
        payload["container"] = match.container
    if getattr(match, "why_ranked", None) is not None:
        payload["why_ranked"] = match.why_ranked
    return payload


def _routing_envelope(result: SearchResult) -> dict[str, object]:
    envelope: dict[str, object] = {
        "version": JSON_OUTPUT_VERSION,
        "schema_version": JSON_OUTPUT_VERSION,
        "sidecar_used": result.sidecar_used,
        "routing_backend": result.routing_backend,
        "routing_reason": result.routing_reason,
        "requested_gpu_device_ids": result.requested_gpu_device_ids,
        "routing_gpu_device_ids": result.routing_gpu_device_ids,
        "routing_gpu_chunk_plan_mb": _routing_gpu_chunk_plan(result),
        "routing_distributed": result.routing_distributed,
        "routing_worker_count": result.routing_worker_count,
    }
    envelope.update(_gpu_proof_payload(result))
    # GPU execution telemetry — only emitted when the backend measured them; omit
    # entirely (not null) when not applicable so consumers can detect presence via key
    # existence rather than null checks.
    if result.kernel_time_ms is not None:
        envelope["kernel_time_ms"] = result.kernel_time_ms
    if result.transfer_time_ms is not None:
        envelope["transfer_time_ms"] = result.transfer_time_ms
    if result.staging_bytes is not None:
        envelope["staging_bytes"] = result.staging_bytes
    if result.fallback_reason is not None:
        envelope["fallback_reason"] = result.fallback_reason
    # `--semantic` fail-closed degrade: emitted ONLY when `--semantic` was requested and the dense
    # leg could not run (extra absent, model not fetched, or a shape/dim-mismatch degrade) -- a
    # BM25-only result must never be silently mislabeled "semantic". Omitted entirely (not null)
    # for every other search so the envelope shape stays byte-identical.
    if result.rank_fallback_reason is not None:
        envelope["rank_fallback_reason"] = result.rank_fallback_reason
    # Partial results (rg exit 2) — a machine-visible "suppression != absence" marker so --json/
    # --ndjson agents don't read a truncated result as complete. Emitted only when incomplete, so
    # the envelope shape is byte-identical for normal (complete) results.
    if result.result_incomplete:
        envelope["result_incomplete"] = True
        envelope["incomplete_reason"] = result.incomplete_reason
        # Task #276 slice 1: a closed-vocabulary class ("unreadable_path"/"scan_limit"/
        # "deadline"/"timeout") alongside the free-text reason, so an agent can branch on
        # whether retrying with a bigger budget could plausibly help without string-sniffing.
        # `is not None` (not unconditional): some `result_incomplete=True` producers -- e.g.
        # `tg find`'s multi-cause `incomplete_reasons` concatenation (main.py), which can
        # include a per-file parse/read error that doesn't cleanly map onto the closed
        # vocabulary -- do not always classify their cause. Emitting a `null` value here would
        # be a NEW key with an out-of-vocabulary value on an existing partial payload;
        # omitting the key entirely when unclassified keeps the field meaning "the cause IS
        # one of these four" rather than "the cause is one of these four, or unknown".
        if result.incomplete_reason_class is not None:
            envelope["incomplete_reason_class"] = result.incomplete_reason_class
    if getattr(result, "ast_enrichment_truncated", False):
        envelope["ast_enrichment_truncated"] = True
    if getattr(result, "install_state", None) is not None:
        envelope["install_state"] = result.install_state
    return envelope


def _gpu_proof_payload(result: SearchResult) -> dict[str, object]:
    if not result.requested_gpu_device_ids:
        return {}

    native_gpu_proof = result.routing_backend == "NativeGpuBackend" and result.sidecar_used is False
    if native_gpu_proof:
        return {
            "gpu_evidence_status": "native",
            "gpu_proof": True,
            "native_gpu_unavailable": False,
            "not_gpu_proof_reason": None,
        }

    return {
        "gpu_evidence_status": "unsupported",
        "gpu_proof": False,
        "native_gpu_unavailable": True,
        "not_gpu_proof_reason": (
            "Requested GPU execution did not produce NativeGpuBackend with "
            f"sidecar_used=false (routing_backend={result.routing_backend or 'unknown'}, "
            f"sidecar_used={result.sidecar_used}); this is CPU/sidecar compatibility "
            "output, not GPU acceleration proof."
        ),
    }


def gpu_request_unhonoured(result: SearchResult) -> bool:
    """True iff GPU was EXPLICITLY requested (``--gpu-device-ids``) and this run could not
    produce NativeGpuBackend-with-``sidecar_used=False`` proof — the "explicit GPU request
    that cannot be honoured" condition backlog #22's exit-code contract keys on.

    False in both cases the CLI's exit-code contract must NOT touch:
    * no GPU was requested at all (``requested_gpu_device_ids`` empty) — a CPU search that
      merely served the query is complete, not incomplete;
    * GPU was requested AND honoured (``routing_backend == "NativeGpuBackend"`` and
      ``sidecar_used is False``).

    Delegates to `_gpu_proof_payload` rather than re-deriving the native/sidecar test, so the
    exit-code decision and the `--json` envelope (`native_gpu_unavailable`) agree by
    construction and can never drift apart.
    """
    return bool(_gpu_proof_payload(result).get("native_gpu_unavailable", False))


class JsonFormatter(OutputFormatter):
    def __init__(self, config: SearchConfig | None = None) -> None:
        self.config = config

    def format(self, result: SearchResult) -> str:
        envelope = _routing_envelope(result)
        data = {
            "total_matches": result.total_matches,
            "total_files": result.total_files,
            "matched_file_paths": result.matched_file_paths,
            "match_counts_by_file": result.match_counts_by_file,
            "sidecar_used": envelope["sidecar_used"],
            "routing_backend": envelope["routing_backend"],
            "routing_reason": envelope["routing_reason"],
            "requested_gpu_device_ids": envelope["requested_gpu_device_ids"],
            "routing_gpu_device_ids": envelope["routing_gpu_device_ids"],
            "routing_gpu_chunk_plan_mb": envelope["routing_gpu_chunk_plan_mb"],
            "routing_distributed": envelope["routing_distributed"],
            "routing_worker_count": envelope["routing_worker_count"],
            "matches": [_match_payload(match, self.config) for match in result.matches],
        }
        # Additive-only: absent on an explicitly-scoped search, so an existing consumer's payload
        # is byte-identical. Present ONLY when the caller did not choose the scope, which is the
        # case where `total_matches: 0` is ambiguous on its own.
        if result.path_was_defaulted:
            data["path_was_defaulted"] = True
            if result.scope_note:
                data["scope_note"] = result.scope_note
        for key in (
            "gpu_evidence_status",
            "gpu_proof",
            "native_gpu_unavailable",
            "not_gpu_proof_reason",
            "kernel_time_ms",
            "transfer_time_ms",
            "staging_bytes",
            "fallback_reason",
            "rank_fallback_reason",
            "result_incomplete",
            "incomplete_reason",
            "incomplete_reason_class",
            "ast_enrichment_truncated",
            "install_state",
        ):
            if key in envelope:
                data[key] = envelope[key]
        data = {
            "version": envelope["version"],
            "schema_version": envelope["schema_version"],
            **data,
        }
        return json.dumps(data)


class NdjsonFormatter(OutputFormatter):
    def format(self, result: SearchResult) -> str:
        envelope = _routing_envelope(result)
        rows = []
        for match in result.matches:
            row = {
                **envelope,
                **_match_payload(match),
                # Rust-native NDJSON exposes `line`; keep `line_number` for
                # Python JSON compatibility while preserving the public field.
                "line": match.line_number,
            }
            rows.append(json.dumps(row))
        if not rows:
            # ZERO ROWS MEANS ZERO CARRIERS. The envelope above -- which holds
            # `result_incomplete`, `incomplete_reason_class`, `path_was_defaulted` and
            # `scope_note` -- is merged into each MATCH ROW, so on an empty result every one of
            # those disclosures is silently dropped and the stream is the empty string. A reader
            # then cannot tell "nothing matched" from "the scan died before it could look", which
            # is precisely the distinction those fields exist to carry.
            #
            # Emitted ONLY when there is something to say. A COMPLETE zero-match search has
            # nothing to disclose, and putting a record on every empty stream to serve the rare
            # truncated case is how a disclosure trains its readers to skip it.
            #
            # DELIBERATELY NARROWER THAN THE RUST ENGINE, which emits a `type: "summary"` record on
            # EVERY `--ndjson` run (see `SearchSummaryNdjson`) on the reasoning that a record
            # appearing only on failure is one a streaming reader never learns to expect. That is
            # the better contract and Python should reach it -- but doing so adds a line to every
            # existing stream, which is a WIRE CHANGE several consumers and tests would break on
            # and which likely needs an MCP contract bump. Tracked separately; this closes only the
            # case where the current design loses information outright.
            disclosure: dict[str, object] = {
                key: value
                for key, value in envelope.items()
                if key in ("result_incomplete", "incomplete_reason", "incomplete_reason_class")
            }
            # `path_was_defaulted`/`scope_note` are read off the RESULT, not filtered out of the
            # envelope, because `_routing_envelope` never carried them: #871 added the pair to
            # `JsonFormatter.format` alone, so the Python `--ndjson` surface never had the scope
            # disclosure at all -- not merely on the empty path. Found while fixing the
            # zero-carrier bug above, which is the only reason it surfaced.
            if getattr(result, "path_was_defaulted", False):
                disclosure["path_was_defaulted"] = True
                if getattr(result, "scope_note", None):
                    disclosure["scope_note"] = result.scope_note
            if disclosure:
                rows.append(json.dumps({**envelope, **disclosure}))
        return "\n".join(rows)
