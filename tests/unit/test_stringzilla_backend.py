import pytest

from tensor_grep.backends.stringzilla_backend import StringZillaBackend
from tensor_grep.core.config import SearchConfig


@pytest.fixture
def backend():
    return StringZillaBackend()


def test_stringzilla_availability(backend):
    # Ensure it's installed via pip
    assert backend.is_available() is True


def test_stringzilla_exact_match(backend, tmp_path):
    log_file = tmp_path / "sys.log"
    log_file.write_text("INFO ok\nERROR failure\nDEBUG trace\nERROR timeout\n", encoding="utf-8")

    config = SearchConfig(fixed_strings=True)
    result = backend.search(str(log_file), "ERROR", config=config)

    assert result.total_matches == 2
    assert len(result.matches) == 2
    assert result.matches[0].line_number == 2
    assert result.matches[1].line_number == 4


def test_stringzilla_no_matches(backend, tmp_path):
    log_file = tmp_path / "sys.log"
    log_file.write_text("INFO ok\nDEBUG trace\n", encoding="utf-8")

    config = SearchConfig(fixed_strings=True)
    result = backend.search(str(log_file), "ERROR", config=config)

    assert result.total_matches == 0
    assert len(result.matches) == 0
