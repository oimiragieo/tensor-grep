from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.cli.formatters.json_fmt import JsonFormatter


def test_json_output_snapshot(sample_log_file, snapshot):
    backend = CPUBackend()
    result = backend.search(str(sample_log_file), "ERROR")
    fmt = JsonFormatter()
    output = fmt.format(result)

    # Normalize line endings before assertion to fix cross-platform issues
    output = output.replace("\r\n", "\n")

    # Replace absolute path with a placeholder for stable snapshot
    # Path format varies by OS, so just do simple replace
    output_stable = output.replace(str(sample_log_file).replace("\\", "\\\\"), "<FILE>")
    output_stable = output_stable.replace(str(sample_log_file), "<FILE>")

    snapshot.assert_match(output_stable, "json_output.json")
