def test_backends_package_declares_public_surface() -> None:
    from tensor_grep import backends
    from tensor_grep.backends import ComputeBackend, CPUBackend

    assert {"CPUBackend", "ComputeBackend", "RipgrepBackend"}.issubset(backends.__all__)
    assert CPUBackend.__name__ == "CPUBackend"
    assert ComputeBackend.__name__ == "ComputeBackend"


def test_core_package_declares_public_surface() -> None:
    from tensor_grep import core
    from tensor_grep.core import MatchLine, SearchConfig, SearchResult

    assert {"MatchLine", "SearchConfig", "SearchResult"}.issubset(core.__all__)
    assert SearchConfig.__name__ == "SearchConfig"
    assert MatchLine.__name__ == "MatchLine"
    assert SearchResult.__name__ == "SearchResult"


def test_cli_formatter_package_declares_public_surface() -> None:
    from tensor_grep.cli import formatters
    from tensor_grep.cli.formatters import CSVFormatter, CsvFormatter, JsonFormatter

    assert {"CSVFormatter", "CsvFormatter", "JsonFormatter"}.issubset(formatters.__all__)
    assert CSVFormatter is CsvFormatter
    assert JsonFormatter.__name__ == "JsonFormatter"
