"""Case-insensitive search on non-ASCII text must equal rg or fail closed.

`str.lower()` (StringZilla) and `re.IGNORECASE` (CPU Python loop) are not rg's Unicode case
folding: `lower()` leaves final sigma unfolded; `re.IGNORECASE` equates U+0130/U+0131 with `i`.
The only provably exact domain is pattern ASCII AND file ASCII (backends/unicode_fold.py).
"""

import sys
import types
from unittest.mock import patch

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.backends.ripgrep_backend import RipgrepBackend, _pattern_semantics_flags
from tensor_grep.backends.stringzilla_backend import StringZillaBackend
from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import _enforce_semantics_support

pytestmark = pytest.mark.skipif(resolve_ripgrep_binary() is None, reason="rg not installed")

SIGMA, FINAL_SIGMA = "\u03c3", "\u03c2"
KELVIN, LONG_S, DOTTED_I, DOTLESS_I = "\u212a", "\u017f", "\u0130", "\u0131"

_CASES = [
    ("final sigma vs sigma", FINAL_SIGMA, SIGMA),  # the audit repro: lower() misses it
    ("kelvin vs k", KELVIN, "k"),
    ("long s vs s", LONG_S, "s"),
    ("dotted I vs i", DOTTED_I, "i"),
    ("dotless i vs i", DOTLESS_I, "i"),
    ("ascii control", "FOO", "foo"),
]
_MODES = [
    ("-i", {"ignore_case": True}),
    ("-S", {"smart_case": True}),  # lowercase pattern => insensitive
]


@pytest.fixture
def stringzilla_stub(monkeypatch):
    # the real wheel is optional; the backend only imports it as an availability gate
    monkeypatch.setitem(sys.modules, "stringzilla", types.ModuleType("stringzilla"))


def _rg(path, pattern, kw):
    cfg = SearchConfig(query_pattern=pattern, fixed_strings=True, **kw)
    return RipgrepBackend().search(str(path), pattern, cfg).total_matches


@pytest.mark.parametrize("mode, kw", _MODES, ids=[m[0] for m in _MODES])
@pytest.mark.parametrize("name, content, pattern", _CASES, ids=[c[0] for c in _CASES])
def test_stringzilla_matches_rg(tmp_path, stringzilla_stub, name, content, pattern, mode, kw):
    f = tmp_path / "a.txt"
    f.write_text(content + "\n", encoding="utf-8")
    cfg = SearchConfig(query_pattern=pattern, fixed_strings=True, **kw)
    actual = StringZillaBackend().search(str(f), pattern, cfg).total_matches
    assert actual == _rg(f, pattern, kw), (name, mode)


@pytest.mark.parametrize("name, content, pattern", _CASES, ids=[c[0] for c in _CASES])
def test_cpu_python_loop_matches_rg(monkeypatch, tmp_path, name, content, pattern):
    def native_fault(*args, **kwargs):
        raise RuntimeError("simulated native fault")  # forces the Python-re (fixed) loop

    monkeypatch.setattr(CPUBackend, "_rust_match_set", native_fault)
    f = tmp_path / "a.txt"
    f.write_text(content + "\n", encoding="utf-8")
    kw = {"ignore_case": True}
    cfg = SearchConfig(query_pattern=pattern, fixed_strings=True, **kw)
    assert CPUBackend().search(str(f), pattern, cfg).total_matches == _rg(f, pattern, kw), name


def test_ascii_control_stays_on_the_fast_path(monkeypatch, tmp_path, stringzilla_stub):
    f = tmp_path / "a.txt"
    f.write_text("FOO bar\n", encoding="utf-8")
    cfg = SearchConfig(query_pattern="foo", fixed_strings=True, ignore_case=True)
    with patch.object(RipgrepBackend, "search", side_effect=AssertionError("must not use rg")):
        sz = StringZillaBackend().search(str(f), "foo", cfg)
        assert sz.total_matches == 1
        assert sz.routing_backend == "StringZillaBackend"

        def native_fault(*args, **kwargs):
            raise RuntimeError("fault")

        monkeypatch.setattr(CPUBackend, "_rust_match_set", native_fault)
        cpu = CPUBackend().search(str(f), "foo", cfg)
        assert cpu.total_matches == 1
        assert cpu.routing_backend == "CPUBackend"


@pytest.mark.parametrize("backend_cls", [StringZillaBackend, CPUBackend])
def test_non_ascii_without_rg_fails_closed(monkeypatch, tmp_path, stringzilla_stub, backend_cls):
    def native_fault(*args, **kwargs):
        raise RuntimeError("fault")

    monkeypatch.setattr(CPUBackend, "_rust_match_set", native_fault)
    f = tmp_path / "a.txt"
    f.write_text(FINAL_SIGMA + "\n", encoding="utf-8")
    cfg = SearchConfig(query_pattern=SIGMA, fixed_strings=True, ignore_case=True)
    with patch.object(RipgrepBackend, "is_available", return_value=False):
        with pytest.raises(BackendExecutionError):
            backend_cls().search(str(f), SIGMA, cfg)


@pytest.mark.parametrize("backend_cls", [StringZillaBackend, CPUBackend])
def test_gate_routes_non_ascii_case_insensitive_pattern_to_rg(stringzilla_stub, backend_cls):
    rg = RipgrepBackend()
    cfg = SearchConfig(query_pattern=SIGMA, fixed_strings=True, ignore_case=True)
    backend, reason = _enforce_semantics_support(
        backend_cls(), "x", cfg, _pattern_semantics_flags(cfg), rg, True
    )
    assert backend is rg and reason == "semantics_require_rg"
    ascii_cfg = SearchConfig(query_pattern="foo", fixed_strings=True, ignore_case=True)
    kept, _ = _enforce_semantics_support(
        backend_cls(), "x", ascii_cfg, _pattern_semantics_flags(ascii_cfg), rg, True
    )
    assert isinstance(kept, backend_cls)  # ASCII pattern keeps the fast engine
    # case-SENSITIVE non-ASCII is exact on every engine: not rerouted
    exact = SearchConfig(query_pattern=SIGMA, fixed_strings=True)
    kept2, _ = _enforce_semantics_support(
        backend_cls(), "x", exact, _pattern_semantics_flags(exact), rg, True
    )
    assert isinstance(kept2, backend_cls)
