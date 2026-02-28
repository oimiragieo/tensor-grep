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

    # Clean out any backslashes first, before JSON parsing
    # The real issue is that `\r` and `\u` in the absolute paths might get incorrectly treated by regex or json
    # so let's simply load JSON, and clean it up.
    
    try:
        parsed = json.loads(output)
        for match in parsed.get("matches", []):
            if "file" in match:
                # Just override the path completely
                match["file"] = "<FILE>"
        output_stable = json.dumps(parsed)
    except json.JSONDecodeError:
        # Fallback to string replace if not valid JSON (shouldn't happen here)
        escaped_file_path = json.dumps(file_path)[1:-1]
        output_stable = output.replace(escaped_file_path, "<FILE>")
        output_stable = output_stable.replace(file_path, "<FILE>")
        # Sometimes Windows paths have escaped sequences like \r or \u that were literal in the path but got converted 
        # to actual escape codes during some string processing. We can just replace the prefix:
        import re
        output_stable = re.sub(r'"[A-Za-z]:\\[^"]+"', '"<FILE>"', output_stable)

    snapshot.assert_match(output_stable, "json_output.json")
