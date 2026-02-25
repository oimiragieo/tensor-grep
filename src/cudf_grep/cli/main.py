import typer
import sys
from cudf_grep.backends.cpu_backend import CPUBackend
from cudf_grep.formatters.ripgrep_fmt import RipgrepFormatter

app = typer.Typer()

@app.command()
def search(pattern: str, file_path: str) -> None:
    backend = CPUBackend()
    result = backend.search(file_path, pattern)

    if result.is_empty:
        sys.exit(1)

    formatter = RipgrepFormatter()
    print(formatter.format(result))

if __name__ == "__main__":
    app()
