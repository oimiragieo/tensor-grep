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

    escaped_file_path = json.dumps(file_path)[1:-1]  # Strip the surrounding quotes

    output_stable = output.replace(escaped_file_path, "<FILE>")
    output_stable = output_stable.replace(file_path, "<FILE>")

    snapshot.assert_match(output_stable, "json_output.json")
