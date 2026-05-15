import json

from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.core.result import MatchLine, SearchResult

JSON_OUTPUT_VERSION = 1


def _routing_gpu_chunk_plan(result: SearchResult) -> list[dict[str, int]]:
    return [
        {"device_id": device_id, "chunk_mb": chunk_mb}
        for device_id, chunk_mb in result.routing_gpu_chunk_plan_mb
    ]


def _match_payload(match: MatchLine) -> dict[str, object]:
    payload: dict[str, object] = {
        "file": match.file,
        "line_number": match.line_number,
        "text": match.text,
    }
    if match.range is not None:
        payload["range"] = match.range
    if match.meta_variables is not None:
        payload["metaVariables"] = match.meta_variables
    return payload


def _routing_envelope(result: SearchResult) -> dict[str, object]:
    envelope: dict[str, object] = {
        "version": JSON_OUTPUT_VERSION,
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


class JsonFormatter(OutputFormatter):
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
            "matches": [_match_payload(match) for match in result.matches],
        }
        for key in (
            "gpu_evidence_status",
            "gpu_proof",
            "native_gpu_unavailable",
            "not_gpu_proof_reason",
        ):
            if key in envelope:
                data[key] = envelope[key]
        data = {"version": envelope["version"], **data}
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
        return "\n".join(rows)
