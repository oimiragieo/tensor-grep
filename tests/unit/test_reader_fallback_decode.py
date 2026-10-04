import gzip

from tensor_grep.io.reader_fallback import FallbackReader


def _lines(path):
    return list(FallbackReader().read_lines(str(path)))


def test_late_decode_error_does_not_re_yield_earlier_lines(tmp_path):
    p = tmp_path / "big.log"
    p.write_bytes(b"".join(b"line %d\n" % i for i in range(3000)) + b"caf\xe9 tail\n")
    lines = _lines(p)
    assert len(lines) == 3001
    assert lines[:3000] == [f"line {i}\n" for i in range(3000)]
    assert lines[-1] == "caf\xe9 tail\n"


def test_early_decode_error_still_decodes_whole_file_as_latin1(tmp_path):
    p = tmp_path / "small.log"
    p.write_bytes(b"caf\xe9\nsecond\n")
    assert _lines(p) == ["caf\xe9\n", "second\n"]


def test_late_decode_error_in_gzip_does_not_re_yield(tmp_path):
    p = tmp_path / "big.log.gz"
    p.write_bytes(gzip.compress(b"".join(b"line %d\n" % i for i in range(3000)) + b"caf\xe9\n"))
    assert len(_lines(p)) == 3001
