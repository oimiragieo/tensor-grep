from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.cli.formatters.json_fmt import JsonFormatter


def test_json_output_snapshot(sample_log_file, snapshot):
    backend = CPUBackend()
    result = backend.search(str(sample_log_file), "ERROR")
    fmt = JsonFormatter()
    output = fmt.format(result)

    # Normalize line endings before assertion to fix cross-platform issues
    output = output.replace("\\r\\n", "\\n").replace("\\r", "")

    # Replace absolute path with a placeholder for stable snapshot
    # Path format varies by OS, so just do simple replace
    # Need to handle both normal and JSON-escaped paths

    # First get the normal path string
    file_path = str(sample_log_file)

    # Also get the json-escaped version
    import json

    # Strip surrounding quotes from dumped JSON path string
    # We do this instead of replacing file_path directly because Python 3.11/3.12 might 
    # escape the `\r` part in `\runneradmin` inconsistently with `json.dumps()` in earlier tests.
    # To be fully robust, let's normalize separators just for replacement logic.
    escaped_file_path = json.dumps(file_path)[1:-1]
    
    # Use regex to do a case-insensitive, normalized replacement
    import re
    
    # We escape the regex pattern since paths contain \ and . which are special in regex
    # The normal path
    norm_path_pattern = re.escape(file_path)
    output_stable = re.sub(norm_path_pattern, "<FILE>", output, flags=re.IGNORECASE)
    
    # The json-escaped path might have varying amounts of backslashes. 
    # Let's normalize backslashes in the output json itself to forward slashes for the match.
    # Actually, the simplest fix is to deserialize the JSON, replace the path, and re-serialize.
    # We know the output is meant to be valid JSON.
    
    try:
        parsed = json.loads(output)
        for match in parsed.get("matches", []):
            if "file" in match:
                # Just override the path completely
                match["file"] = "<FILE>"
        output_stable = json.dumps(parsed)
    except json.JSONDecodeError:
        # Fallback to string replace if not valid JSON (shouldn't happen here)
        output_stable = output.replace(escaped_file_path, "<FILE>")
        output_stable = output_stable.replace(file_path, "<FILE>")

    snapshot.assert_match(output_stable, "json_output.json")
