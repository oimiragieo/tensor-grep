import json

from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.core.result import SearchResult

JSON_OUTPUT_VERSION = 1


class JsonFormatter(OutputFormatter):
    def format(self, result: SearchResult) -> str:
        matches = []
        for match in result.matches:
            payload = {"file": match.file, "line_number": match.line_number, "text": match.text}
            if match.range is not None:
                payload["range"] = match.range
            if match.meta_variables is not None:
                payload["metaVariables"] = match.meta_variables
            matches.append(payload)

        data = {
            "version": JSON_OUTPUT_VERSION,
            "total_matches": result.total_matches,
            "total_files": result.total_files,
            "matched_file_paths": result.matched_file_paths,
            "match_counts_by_file": result.match_counts_by_file,
            "sidecar_used": result.sidecar_used,
            "routing_backend": result.routing_backend,
            "routing_reason": result.routing_reason,
            "routing_gpu_device_ids": result.routing_gpu_device_ids,
            "routing_gpu_chunk_plan_mb": [
                {"device_id": device_id, "chunk_mb": chunk_mb}
                for device_id, chunk_mb in result.routing_gpu_chunk_plan_mb
            ],
            "routing_distributed": result.routing_distributed,
            "routing_worker_count": result.routing_worker_count,
            "matches": matches,
        }
        return json.dumps(data)
