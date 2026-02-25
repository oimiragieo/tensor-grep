import typer
import sys
from cudf_grep.backends.cpu_backend import CPUBackend
from cudf_grep.formatters.ripgrep_fmt import RipgrepFormatter

from typing import Optional

app = typer.Typer()

@app.command(name="search")
def search_command(
    pattern: str,
    file_path: str,
    cpu: bool = typer.Option(False, "--cpu", help="Force CPU fallback"),
    format_type: str = typer.Option("rg", "--format", help="Output format: json, table, csv, rg")
) -> None:
    backend = CPUBackend()
    result = backend.search(file_path, pattern)

    if result.is_empty:
        sys.exit(1)

    from cudf_grep.formatters.base import OutputFormatter
    formatter: OutputFormatter

    if format_type == "json":
        from cudf_grep.formatters.json_fmt import JsonFormatter
        formatter = JsonFormatter()
    elif format_type == "table":
        from cudf_grep.formatters.table_fmt import TableFormatter
        formatter = TableFormatter()
    elif format_type == "csv":
        from cudf_grep.formatters.csv_fmt import CsvFormatter
        formatter = CsvFormatter()
    else:
        from cudf_grep.formatters.ripgrep_fmt import RipgrepFormatter
        formatter = RipgrepFormatter()
        
    print(formatter.format(result))

@app.command()
def classify(
    file_path: str,
    format_type: str = typer.Option("json", "--format", help="Output format")
) -> None:
    from cudf_grep.backends.cybert_backend import CybertBackend
    from cudf_grep.io.reader_fallback import FallbackReader
    import json
    import sys
    
    reader = FallbackReader()
    lines = list(reader.read_lines(file_path))
    if not lines:
        sys.exit(1)
        
    backend = CybertBackend()
    results = backend.classify(lines)
    
    if format_type == "json":
        data = {"classifications": results}
        print(json.dumps(data))
    else:
        for r in results:
            print(f"{r['label']} ({r['confidence']:.2f})")

if __name__ == "__main__":
    app()
