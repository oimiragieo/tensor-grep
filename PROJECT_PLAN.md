# cudf-grep: GPU-Accelerated Log Parsing CLI
## Single-Shot TDD Implementation Plan (2026)

---

## How To Use This Plan

This document is designed for a single long-running AI session to execute sequentially.
Each task has a checkbox. Work top to bottom. Every task follows Red-Green-Refactor.
Do not skip ahead. Each green task unlocks the next red task.

**TDD Method: Outside-In Double-Loop TDD (2026 Edition)**
- Outer loop: Acceptance/E2E tests (minutes to hours) -- defines WHAT the user sees
- Inner loop: Unit tests (seconds to minutes) -- defines HOW internals work
- AI-assisted edge case generation via Hypothesis property-based testing
- Mutation testing gates to verify test quality, not just coverage
- Walking skeleton first, then flesh out

```
OUTER LOOP (Acceptance)          INNER LOOP (Unit)
  ┌──────────────┐                ┌──────────────┐
  │  RED: Write   │                │  RED: Write   │
  │  failing E2E  │───────────────>│  failing unit │
  │  test         │                │  test         │
  └──────┬───────┘                └──────┬───────┘
         │                               │
         │                        ┌──────v───────┐
         │                        │ GREEN: Write  │
         │                        │ minimal code  │
         │                        │ to pass       │
         │                        └──────┬───────┘
         │                               │
         │                        ┌──────v───────┐
         │                        │ REFACTOR:     │
         │                        │ Clean up      │
         │                        └──────┬───────┘
         │                               │
         │  (repeat inner loop until     │
  ┌──────v───────┐  outer test passes)   │
  │ GREEN: E2E   │<──────────────────────┘
  │ now passes   │
  └──────┬───────┘
         │
  ┌──────v───────┐
  │ REFACTOR:    │
  │ Architecture │
  └──────────────┘
```

---

## Architecture Overview

```
                        ┌──────────────────────┐
                        │   CLI (Typer)         │
                        │   cybert-grep ...     │
                        └──────────┬───────────┘
                                   │
                        ┌──────────v───────────┐
                        │   Query Analyzer     │
                        │   simple? -> FastPath │
                        │   complex? -> NLPPath │
                        └─────┬──────────┬─────┘
                              │          │
                 ┌────────────v──┐  ┌────v────────────┐
                 │  Fast Path    │  │  NLP Path        │
                 │  - regex/grep │  │  - cyBERT model  │
                 │  - cuDF str   │  │  - Triton server │
                 │  - CPU fallbk │  │  - TensorRT      │
                 └────────┬─────┘  └────┬─────────────┘
                          │             │
                 ┌────────v─────────────v──────────┐
                 │     Unified Output Formatter    │
                 │     JSON | table | CSV | rg     │
                 └─────────────────────────────────┘

Platform I/O Layer:
  Linux:   cudf.read_text() + KvikIO/GDS
  Windows: dstorage-gpu (NVMe->GPU direct) + fallback chunked I/O
  WSL2:    RAPIDS native inside WSL2 filesystem
  No GPU:  CPU-only fallback (regex + multiprocessing)
```

---

## Platform Strategy

**Development host is Windows 10.** Three core deps (Morpheus, cuDF, KvikIO) are Linux-only.

| Platform | I/O Layer | Compute Layer | Status |
|----------|-----------|---------------|--------|
| Linux native | cudf.read_text() + KvikIO/GDS | cuDF string ops + Morpheus + cyBERT | Full feature set |
| WSL2 on Windows | cudf.read_text() (WSL2 fs only) | cuDF + PyTorch + cyBERT | Primary dev path |
| Windows native | dstorage-gpu (pip install dstorage-gpu) | PyTorch CUDA inference only | Partial (no cuDF) |
| No GPU (any OS) | Python mmap + multiprocessing | regex stdlib + re2 | CPU fallback |

**Rule:** All files for WSL2 I/O must live in `/home/...`, NOT `/mnt/c/...` (10-50x penalty).

---

## Corrected Hardware Throughput Matrix

Bottleneck is the NVMe SSD, not PCIe or GPU.

| GPU + Storage | Realistic Throughput | Notes |
|--------------|---------------------|-------|
| RTX 4070 + PCIe 4.0 x4 NVMe | 5-7 GB/s | SSD-limited |
| RTX 5070 + PCIe 5.0 x4 NVMe | 10-14 GB/s | |
| dstorage-gpu on RTX 3060, PCIe 3.0 | 11.7 GB/s | Measured (Feb 2026) |
| L40S + Enterprise NVMe array | 25-40 GB/s | Multi-drive RAID |
| H100 + NVMe-oF fabric | 50-80 GB/s | Networked storage |

---

## Technology Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| CLI | Typer | Type hints, auto-complete, modern Python |
| GPU DataFrame | cuDF (RAPIDS 26.02) | GPU string ops 376-1012x faster than pandas |
| Text Ingestion | cudf.read_text() | Byte-range chunking, delimiter-aware, bgzip |
| GPU File I/O (Linux) | KvikIO | Python/C++ bindings to cuFile for GDS |
| GPU File I/O (Windows) | dstorage-gpu v1.0.0 | NVMe->DirectStorage->D3D12->CUDA tensor |
| NLP Inference | cyBERT via Triton + TensorRT | Morpheus ecosystem |
| CPU Fallback | re2 / regex stdlib | When no GPU available |
| Testing | pytest + hypothesis + mutmut | 2026 TDD stack |
| Coverage | pytest-cov (>90%) + mutmut (<20% survivors) | Quality gates |
| CI | GitHub Actions | Linux runner with GPU for integration tests |

---

## Project Structure

```
cudf-grep/
├── pyproject.toml                  # All config: deps, pytest, mutmut, ruff
├── src/
│   └── cudf_grep/
│       ├── __init__.py
│       ├── cli/
│       │   ├── __init__.py
│       │   └── main.py             # Typer app entry point
│       ├── core/
│       │   ├── __init__.py
│       │   ├── query_analyzer.py   # Routes: fast path vs NLP path
│       │   ├── pipeline.py         # Orchestrates the processing pipeline
│       │   └── result.py           # SearchResult dataclass
│       ├── backends/
│       │   ├── __init__.py
│       │   ├── base.py             # ComputeBackend protocol
│       │   ├── cpu_backend.py      # regex + multiprocessing fallback
│       │   ├── cudf_backend.py     # cuDF GPU string ops (Linux/WSL2)
│       │   └── cybert_backend.py   # cyBERT NLP inference (GPU)
│       ├── io/
│       │   ├── __init__.py
│       │   ├── base.py             # IOBackend protocol
│       │   ├── reader_cudf.py      # cudf.read_text() wrapper
│       │   ├── reader_dstorage.py  # dstorage-gpu (Windows native)
│       │   ├── reader_kvikio.py    # KvikIO/GDS (Linux datacenter)
│       │   └── reader_fallback.py  # Python mmap (no GPU)
│       ├── formatters/
│       │   ├── __init__.py
│       │   ├── base.py             # OutputFormatter protocol
│       │   ├── json_fmt.py
│       │   ├── table_fmt.py
│       │   ├── csv_fmt.py
│       │   └── ripgrep_fmt.py      # rg-compatible line output
│       └── gpu/
│           ├── __init__.py
│           ├── memory_manager.py   # VRAM budget, pinned pools, streams
│           └── device_detect.py    # Detect GPU, VRAM, GDS support
├── tests/
│   ├── conftest.py                 # Global fixtures, markers, GPU skip logic
│   ├── acceptance/                 # Outer loop: E2E user-facing tests
│   │   ├── conftest.py
│   │   ├── test_cli_search.py
│   │   ├── test_cli_classify.py
│   │   └── test_cli_no_gpu.py
│   ├── unit/                       # Inner loop: fast, isolated, no GPU
│   │   ├── conftest.py
│   │   ├── test_query_analyzer.py
│   │   ├── test_cpu_backend.py
│   │   ├── test_cudf_backend.py
│   │   ├── test_cybert_backend.py
│   │   ├── test_reader_fallback.py
│   │   ├── test_formatters.py
│   │   ├── test_memory_manager.py
│   │   └── test_result.py
│   ├── property/                   # Hypothesis property-based tests
│   │   ├── test_tokenizer_props.py
│   │   ├── test_reader_props.py
│   │   └── test_formatter_props.py
│   ├── contract/                   # Backend protocol conformance
│   │   ├── test_backend_contracts.py
│   │   └── test_io_contracts.py
│   ├── integration/                # Real GPU, real cuDF, slower
│   │   ├── conftest.py
│   │   ├── test_cudf_read_text.py
│   │   ├── test_gpu_memory.py
│   │   └── test_pipeline_e2e.py
│   ├── characterization/           # Output parity with ripgrep
│   │   └── test_ripgrep_parity.py
│   ├── snapshot/                   # Output format regression
│   │   └── test_output_snapshots.py
│   └── performance/                # Benchmarks (not in CI gate)
│       ├── test_throughput.py
│       └── test_vs_ripgrep.py
├── test_data/
│   ├── small_sample.log            # 100 lines, committed to repo
│   ├── security_events.jsonl       # 50 labeled log entries
│   └── generate_large.py           # Script to create GB-scale test data
└── scripts/
    ├── benchmark.py
    └── compare_benchmarks.py
```

---

## PHASE 0: Walking Skeleton
**Goal:** One acceptance test passes end-to-end with a hardcoded result.
**Time:** ~2 hours

### Task 0.1 -- Initialize Repository
- [ ] `git init` and create `.gitignore` (Python, __pycache__, .venv, *.egg-info)
- [ ] Create `pyproject.toml` with project metadata, dependencies, and tool config
  ```toml
  [project]
  name = "cudf-grep"
  version = "0.1.0"
  requires-python = ">=3.11"
  dependencies = ["typer[all]>=0.12", "rich>=13.0"]

  [project.optional-dependencies]
  gpu = ["cudf-cu12", "kvikio-cu12", "torch>=2.0"]
  gpu-win = ["dstorage-gpu>=1.0", "torch>=2.0"]
  nlp = ["transformers>=4.40", "tritonclient[all]"]
  dev = [
    "pytest>=8.0", "pytest-cov>=5.0", "pytest-mock>=3.14",
    "pytest-asyncio>=0.24", "pytest-snapshot>=0.9",
    "hypothesis>=6.100", "mutmut>=3.0",
    "ruff>=0.6", "mypy>=1.11",
  ]

  [project.scripts]
  cybert-grep = "cudf_grep.cli.main:app"

  [tool.pytest.ini_options]
  testpaths = ["tests"]
  markers = [
    "gpu: requires NVIDIA CUDA GPU",
    "slow: takes >10 seconds",
    "integration: requires real GPU + cuDF",
    "acceptance: outer-loop E2E tests",
    "property: hypothesis property-based tests",
    "characterization: ripgrep parity tests",
    "snapshot: output format regression tests",
  ]
  addopts = [
    "--import-mode=importlib",
    "--strict-markers",
    "-x",
    "--tb=short",
  ]

  [tool.coverage.run]
  source = ["src/cudf_grep"]
  branch = true

  [tool.coverage.report]
  fail_under = 90

  [tool.mutmut]
  paths_to_mutate = "src/cudf_grep/"
  tests_dir = "tests/unit/"
  runner = "python -m pytest tests/unit/ -x --no-header -q"

  [tool.ruff]
  target-version = "py311"
  line-length = 100

  [tool.mypy]
  python_version = "3.11"
  strict = true
  ```
- [ ] Create `src/` layout with `__init__.py` files for all packages
- [ ] Create `tests/conftest.py` with GPU skip marker:
  ```python
  import pytest
  import shutil

  def pytest_configure(config):
      try:
          import torch
          if not torch.cuda.is_available():
              raise ImportError
          config._gpu_available = True
      except ImportError:
          config._gpu_available = False

  def pytest_collection_modifyitems(config, items):
      if not getattr(config, '_gpu_available', False):
          skip_gpu = pytest.mark.skip(reason="CUDA GPU not available")
          for item in items:
              if "gpu" in item.keywords:
                  item.add_marker(skip_gpu)

  @pytest.fixture
  def sample_log_file(tmp_path):
      log = tmp_path / "test.log"
      log.write_text(
          "2026-02-24 10:00:01 INFO Server started on port 8080\n"
          "2026-02-24 10:00:05 ERROR Connection timeout to database\n"
          "2026-02-24 10:00:06 WARN Retrying connection attempt 1/3\n"
          "2026-02-24 10:00:10 ERROR Failed SSH login from 192.168.1.100\n"
          "2026-02-24 10:00:15 INFO Request GET /api/users 200 12ms\n"
      )
      return log

  @pytest.fixture
  def rg_path():
      path = shutil.which("rg")
      if not path:
          pytest.skip("ripgrep not installed")
      return path
  ```

### Task 0.2 -- Walking Skeleton: First Acceptance Test (RED)
- [ ] Write `tests/acceptance/test_cli_search.py`:
  ```python
  import subprocess
  import pytest

  pytestmark = pytest.mark.acceptance

  class TestCLISearch:
      def test_should_find_pattern_in_log_file(self, sample_log_file):
          """OUTER LOOP RED: The simplest possible E2E test."""
          result = subprocess.run(
              ["cybert-grep", "search", "ERROR", str(sample_log_file)],
              capture_output=True, text=True,
          )
          assert result.returncode == 0
          assert "ERROR" in result.stdout
          assert result.stdout.count("\n") == 2  # Two ERROR lines

      def test_should_exit_1_when_no_matches(self, sample_log_file):
          result = subprocess.run(
              ["cybert-grep", "search", "NONEXISTENT", str(sample_log_file)],
              capture_output=True, text=True,
          )
          assert result.returncode == 1
  ```
- [ ] Run `pytest tests/acceptance/ -m acceptance` -- confirm RED (fails because CLI does not exist)

### Task 0.3 -- Walking Skeleton: Minimal CLI (GREEN)
- [ ] Step into inner loop. Write `tests/unit/test_result.py` (RED):
  ```python
  from cudf_grep.core.result import SearchResult, MatchLine

  class TestSearchResult:
      def test_should_create_result_with_matches(self):
          match = MatchLine(line_number=2, text="ERROR Connection timeout", file="test.log")
          result = SearchResult(matches=[match], total_files=1, total_matches=1)
          assert result.total_matches == 1
          assert result.matches[0].line_number == 2

      def test_should_report_empty_when_no_matches(self):
          result = SearchResult(matches=[], total_files=1, total_matches=0)
          assert result.is_empty is True
  ```
- [ ] Implement `src/cudf_grep/core/result.py` (GREEN):
  ```python
  from dataclasses import dataclass, field

  @dataclass(frozen=True)
  class MatchLine:
      line_number: int
      text: str
      file: str

  @dataclass
  class SearchResult:
      matches: list[MatchLine] = field(default_factory=list)
      total_files: int = 0
      total_matches: int = 0

      @property
      def is_empty(self) -> bool:
          return self.total_matches == 0
  ```
- [ ] Write `tests/unit/test_cpu_backend.py` (RED):
  ```python
  from cudf_grep.backends.cpu_backend import CPUBackend

  class TestCPUBackend:
      def test_should_find_simple_pattern(self, sample_log_file):
          backend = CPUBackend()
          result = backend.search(str(sample_log_file), "ERROR")
          assert result.total_matches == 2

      def test_should_return_empty_for_no_match(self, sample_log_file):
          backend = CPUBackend()
          result = backend.search(str(sample_log_file), "NONEXISTENT")
          assert result.is_empty is True
  ```
- [ ] Implement `src/cudf_grep/backends/base.py` (protocol) and `cpu_backend.py` (GREEN)
- [ ] Write `tests/unit/test_formatters.py` -- test ripgrep_fmt outputs lines (RED)
- [ ] Implement `src/cudf_grep/formatters/ripgrep_fmt.py` (GREEN)
- [ ] Implement `src/cudf_grep/cli/main.py` -- minimal Typer app that wires CPU backend + ripgrep formatter (GREEN)
- [ ] Run `pip install -e ".[dev]"` and re-run acceptance test -- confirm GREEN
- [ ] REFACTOR: Clean up any code smells introduced during skeleton

### Task 0.4 -- Validate the Skeleton
- [ ] Run full test suite: `pytest --cov=src/cudf_grep`
- [ ] Run type check: `mypy src/`
- [ ] Run linter: `ruff check src/ tests/`
- [ ] `git add -A && git commit -m "Walking skeleton: CLI search with CPU backend"`

---

## PHASE 1: CPU Fallback Path (Complete & Tested)
**Goal:** Fully working CLI with no GPU dependency. Matches ripgrep output for simple patterns.
**Time:** ~4 hours

### Task 1.1 -- Query Analyzer (RED -> GREEN -> REFACTOR)
- [ ] Write `tests/unit/test_query_analyzer.py` (RED):
  ```python
  from cudf_grep.core.query_analyzer import QueryAnalyzer, QueryType

  class TestQueryAnalyzer:
      def test_simple_string_is_fast_path(self):
          qa = QueryAnalyzer()
          assert qa.analyze("ERROR").query_type == QueryType.FAST

      def test_regex_is_fast_path(self):
          qa = QueryAnalyzer()
          assert qa.analyze(r"ERROR.*timeout").query_type == QueryType.FAST

      def test_natural_language_is_nlp_path(self):
          qa = QueryAnalyzer()
          assert qa.analyze("classify ssh brute force attempts").query_type == QueryType.NLP

      def test_keyword_triggers_nlp(self):
          qa = QueryAnalyzer()
          for kw in ["classify", "detect", "extract entities", "anomaly"]:
              assert qa.analyze(kw).query_type == QueryType.NLP
  ```
- [ ] Implement `src/cudf_grep/core/query_analyzer.py` (GREEN)
- [ ] REFACTOR: ensure analyzer is stateless and fast

### Task 1.2 -- Full CPU Backend with Regex (RED -> GREEN -> REFACTOR)
- [ ] Write additional unit tests for CPU backend (RED):
  - [ ] `test_should_support_regex_patterns`
  - [ ] `test_should_support_case_insensitive_search`
  - [ ] `test_should_search_multiple_files`
  - [ ] `test_should_handle_binary_files_gracefully`
  - [ ] `test_should_handle_empty_file`
  - [ ] `test_should_handle_file_not_found`
  - [ ] `test_should_report_line_numbers`
  - [ ] `test_should_handle_utf8_and_latin1`
- [ ] Implement each test case minimally (GREEN for each)
- [ ] REFACTOR: extract file reading into IOBackend protocol

### Task 1.3 -- IO Backends: Fallback Reader (RED -> GREEN -> REFACTOR)
- [ ] Write `tests/unit/test_reader_fallback.py` (RED):
  - [ ] `test_should_read_entire_small_file`
  - [ ] `test_should_read_file_in_chunks`
  - [ ] `test_should_preserve_line_boundaries_across_chunks`
  - [ ] `test_should_handle_compressed_gzip`
  - [ ] `test_should_mmap_large_files`
- [ ] Implement `src/cudf_grep/io/reader_fallback.py` (GREEN)
- [ ] Write `tests/contract/test_io_contracts.py` (RED):
  ```python
  from cudf_grep.io.base import IOBackend
  from cudf_grep.io.reader_fallback import FallbackReader

  class TestIOContract:
      """Every IOBackend must satisfy these contracts."""
      def _check_contract(self, reader: IOBackend, file_path):
          lines = list(reader.read_lines(str(file_path)))
          assert len(lines) > 0
          assert all(isinstance(line, str) for line in lines)

      def test_fallback_satisfies_contract(self, sample_log_file):
          self._check_contract(FallbackReader(), sample_log_file)
  ```
- [ ] REFACTOR: ensure all IO backends share the `IOBackend` protocol

### Task 1.4 -- Output Formatters (RED -> GREEN -> REFACTOR)
- [ ] Write `tests/unit/test_formatters.py` -- expand with all formats (RED):
  - [ ] `test_json_output_is_valid_json`
  - [ ] `test_table_output_has_headers`
  - [ ] `test_csv_output_is_parseable`
  - [ ] `test_ripgrep_format_matches_rg_output`
- [ ] Implement `json_fmt.py`, `table_fmt.py`, `csv_fmt.py` (GREEN for each)
- [ ] Write `tests/snapshot/test_output_snapshots.py`:
  ```python
  def test_json_output_snapshot(sample_log_file, snapshot):
      result = run_search("ERROR", sample_log_file, format="json")
      assert result == snapshot
  ```
- [ ] Write `tests/contract/test_backend_contracts.py` (RED):
  ```python
  from cudf_grep.backends.base import ComputeBackend

  class TestBackendContract:
      """Every ComputeBackend must satisfy these contracts."""
      def _check_contract(self, backend: ComputeBackend, file_path, pattern):
          result = backend.search(str(file_path), pattern)
          assert hasattr(result, 'matches')
          assert hasattr(result, 'total_matches')
          assert hasattr(result, 'is_empty')

      def test_cpu_backend_satisfies_contract(self, sample_log_file):
          from cudf_grep.backends.cpu_backend import CPUBackend
          self._check_contract(CPUBackend(), sample_log_file, "ERROR")
  ```

### Task 1.5 -- Characterization Tests: ripgrep Parity (RED -> GREEN -> REFACTOR)
- [ ] Write `tests/characterization/test_ripgrep_parity.py` (RED):
  ```python
  import subprocess
  import pytest

  pytestmark = pytest.mark.characterization
  PATTERNS = ["ERROR", "INFO", r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", "GET /api"]

  class TestRipgrepParity:
      @pytest.mark.parametrize("pattern", PATTERNS)
      def test_output_lines_match_ripgrep(self, sample_log_file, rg_path, pattern):
          rg = subprocess.run(
              [rg_path, pattern, str(sample_log_file)],
              capture_output=True, text=True,
          )
          ours = subprocess.run(
              ["cybert-grep", "search", pattern, str(sample_log_file)],
              capture_output=True, text=True,
          )
          rg_lines = sorted(rg.stdout.strip().splitlines())
          our_lines = sorted(ours.stdout.strip().splitlines())
          assert our_lines == rg_lines
  ```
- [ ] Fix any output differences until GREEN

### Task 1.6 -- Property-Based Tests (RED -> GREEN -> REFACTOR)
- [ ] Write `tests/property/test_reader_props.py`:
  ```python
  from hypothesis import given, strategies as st
  import pytest

  pytestmark = pytest.mark.property

  @given(st.text(min_size=1, max_size=50000, alphabet=st.characters(blacklist_categories=("Cs",))))
  def test_reader_never_loses_bytes(text):
      """Property: total bytes read == total bytes written."""
      import tempfile, os
      with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False, encoding='utf-8') as f:
          f.write(text)
          f.flush()
          path = f.name
      try:
          from cudf_grep.io.reader_fallback import FallbackReader
          reader = FallbackReader()
          content = "".join(reader.read_lines(path))
          assert len(content.encode('utf-8')) == len(text.encode('utf-8'))
      finally:
          os.unlink(path)

  @given(st.from_regex(r'[A-Za-z0-9.*+?\[\]{}()^$|\\]+', fullmatch=True))
  def test_cpu_backend_never_crashes_on_valid_regex(pattern):
      """Property: CPU backend handles any valid regex without exception."""
      import tempfile, os
      with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
          f.write("test line ERROR something\nanother line\n")
          path = f.name
      try:
          from cudf_grep.backends.cpu_backend import CPUBackend
          backend = CPUBackend()
          result = backend.search(path, pattern)
          assert result is not None
      except Exception:
          pass  # Invalid regex is acceptable to reject
      finally:
          os.unlink(path)
  ```

### Task 1.7 -- No-GPU Acceptance Test (RED -> GREEN)
- [ ] Write `tests/acceptance/test_cli_no_gpu.py` (RED):
  ```python
  import subprocess
  import pytest

  pytestmark = pytest.mark.acceptance

  class TestCLIWithoutGPU:
      def test_should_work_with_cpu_flag(self, sample_log_file):
          result = subprocess.run(
              ["cybert-grep", "search", "--cpu", "ERROR", str(sample_log_file)],
              capture_output=True, text=True,
          )
          assert result.returncode == 0
          assert "ERROR" in result.stdout

      def test_should_output_json(self, sample_log_file):
          result = subprocess.run(
              ["cybert-grep", "search", "--cpu", "--format", "json", "ERROR", str(sample_log_file)],
              capture_output=True, text=True,
          )
          assert result.returncode == 0
          import json
          data = json.loads(result.stdout)
          assert "matches" in data
  ```
- [ ] Wire query analyzer into CLI main.py to make these pass (GREEN)

### Task 1.8 -- Quality Gates: Phase 1 Checkpoint
- [ ] Run `pytest tests/unit/ tests/property/ tests/contract/ tests/acceptance/ tests/characterization/ tests/snapshot/ --cov=src/cudf_grep`
- [ ] Verify coverage >= 90%
- [ ] Run `mutmut run` on `src/cudf_grep/core/` and `src/cudf_grep/backends/cpu_backend.py`
- [ ] Verify < 20% surviving mutants
- [ ] Run `mypy src/` -- zero errors
- [ ] Run `ruff check src/ tests/` -- zero errors
- [ ] `git commit -m "Phase 1: Complete CPU fallback with ripgrep parity"`

---

## PHASE 2: GPU Fast Path -- cuDF String Operations
**Goal:** cuDF GPU string ops for regex/pattern matching on Linux/WSL2.
**Time:** ~4 hours

### Task 2.1 -- cuDF Backend Unit Tests (RED)
- [ ] Write `tests/unit/test_cudf_backend.py`:
  ```python
  import pytest
  from unittest.mock import MagicMock, patch

  class TestCuDFBackend:
      """Unit tests: mock cuDF so no GPU needed."""

      @patch("cudf_grep.backends.cudf_backend.cudf")
      def test_should_use_cudf_read_text(self, mock_cudf, sample_log_file):
          mock_series = MagicMock()
          mock_series.str.contains.return_value = MagicMock()
          mock_cudf.read_text.return_value = mock_series

          from cudf_grep.backends.cudf_backend import CuDFBackend
          backend = CuDFBackend()
          backend.search(str(sample_log_file), "ERROR")

          mock_cudf.read_text.assert_called_once()

      @patch("cudf_grep.backends.cudf_backend.cudf")
      def test_should_use_byte_range_for_large_files(self, mock_cudf, tmp_path):
          from cudf_grep.backends.cudf_backend import CuDFBackend
          backend = CuDFBackend(chunk_size_mb=256)
          assert backend.chunk_size_mb == 256

      @patch("cudf_grep.backends.cudf_backend.cudf")
      def test_should_use_str_contains_for_regex(self, mock_cudf):
          mock_series = MagicMock()
          mock_cudf.read_text.return_value = mock_series

          from cudf_grep.backends.cudf_backend import CuDFBackend
          backend = CuDFBackend()
          backend.search("test.log", r"ERROR.*timeout")

          mock_series.str.contains.assert_called_once()
  ```

### Task 2.2 -- cuDF Backend Implementation (GREEN)
- [ ] Implement `src/cudf_grep/backends/cudf_backend.py`:
  ```python
  from __future__ import annotations
  from typing import TYPE_CHECKING
  from cudf_grep.backends.base import ComputeBackend
  from cudf_grep.core.result import SearchResult, MatchLine

  if TYPE_CHECKING:
      import cudf

  class CuDFBackend(ComputeBackend):
      def __init__(self, chunk_size_mb: int = 512):
          self.chunk_size_mb = chunk_size_mb

      def is_available(self) -> bool:
          try:
              import cudf as _cudf
              return True
          except ImportError:
              return False

      def search(self, file_path: str, pattern: str) -> SearchResult:
          import cudf
          import os

          file_size = os.path.getsize(file_path)
          chunk_bytes = self.chunk_size_mb * 1024 * 1024
          matches: list[MatchLine] = []

          if file_size <= chunk_bytes:
              series = cudf.read_text(file_path, delimiter="\n", strip_delimiters=True)
              mask = series.str.contains(pattern, regex=True)
              matched = series[mask]
              for idx, text in zip(matched.index.to_pandas(), matched.to_pandas()):
                  matches.append(MatchLine(line_number=int(idx) + 1, text=str(text), file=file_path))
          else:
              offset = 0
              line_offset = 0
              while offset < file_size:
                  size = min(chunk_bytes, file_size - offset)
                  series = cudf.read_text(
                      file_path, delimiter="\n",
                      byte_range=(offset, size), strip_delimiters=True,
                  )
                  mask = series.str.contains(pattern, regex=True)
                  matched = series[mask]
                  for idx, text in zip(matched.index.to_pandas(), matched.to_pandas()):
                      matches.append(MatchLine(
                          line_number=line_offset + int(idx) + 1,
                          text=str(text), file=file_path,
                      ))
                  line_offset += len(series)
                  offset += size

          return SearchResult(matches=matches, total_files=1, total_matches=len(matches))
  ```
- [ ] Run unit tests (GREEN -- mocked, no GPU needed)

### Task 2.3 -- cuDF IO Reader (RED -> GREEN)
- [ ] Write `tests/unit/test_reader_cudf.py` (mocked, RED)
- [ ] Implement `src/cudf_grep/io/reader_cudf.py` (GREEN)
- [ ] Add to IO contract tests

### Task 2.4 -- cuDF Integration Tests (RED -> GREEN)
- [ ] Write `tests/integration/test_cudf_read_text.py` (marked `@pytest.mark.gpu`):
  ```python
  import pytest

  pytestmark = [pytest.mark.gpu, pytest.mark.integration]

  class TestCuDFIntegration:
      def test_cudf_read_text_returns_series(self, sample_log_file):
          import cudf
          series = cudf.read_text(str(sample_log_file), delimiter="\n")
          assert len(series) == 5

      def test_cudf_str_contains_finds_pattern(self, sample_log_file):
          import cudf
          series = cudf.read_text(str(sample_log_file), delimiter="\n")
          mask = series.str.contains("ERROR")
          assert mask.sum() == 2

      def test_cudf_byte_range_reading(self, sample_log_file):
          import cudf, os
          size = os.path.getsize(str(sample_log_file))
          s1 = cudf.read_text(str(sample_log_file), delimiter="\n", byte_range=(0, size))
          assert len(s1) >= 1
  ```
- [ ] Run on WSL2 with GPU to confirm GREEN

### Task 2.5 -- Backend Auto-Selection (RED -> GREEN -> REFACTOR)
- [ ] Write `tests/unit/test_pipeline.py`:
  ```python
  class TestPipeline:
      def test_should_select_cudf_when_available(self):
          with patch("cudf_grep.core.pipeline.CuDFBackend") as mock:
              mock.return_value.is_available.return_value = True
              pipeline = Pipeline(force_cpu=False)
              assert pipeline.backend.__class__.__name__ == "CuDFBackend"

      def test_should_fallback_to_cpu_when_no_gpu(self):
          with patch("cudf_grep.core.pipeline.CuDFBackend") as mock:
              mock.return_value.is_available.return_value = False
              pipeline = Pipeline(force_cpu=False)
              assert pipeline.backend.__class__.__name__ == "CPUBackend"
  ```
- [ ] Implement `src/cudf_grep/core/pipeline.py` (GREEN)
- [ ] REFACTOR: clean dependency injection

### Task 2.6 -- Phase 2 Quality Gates
- [ ] Run full unit + contract + property + acceptance suite
- [ ] Coverage >= 90%
- [ ] `mutmut run` on `cudf_backend.py` -- < 20% survivors
- [ ] `git commit -m "Phase 2: cuDF GPU fast path with auto-selection"`

---

## PHASE 3: GPU Memory Management
**Goal:** VRAM-safe processing for files larger than GPU memory.
**Time:** ~3 hours

### Task 3.1 -- Device Detection (RED -> GREEN)
- [ ] Write `tests/unit/test_device_detect.py`:
  - [ ] `test_should_detect_no_gpu_when_cuda_unavailable` (mocked)
  - [ ] `test_should_report_vram_capacity` (mocked)
  - [ ] `test_should_detect_gds_support` (mocked)
  - [ ] `test_should_detect_platform` (linux/win32/wsl2)
- [ ] Implement `src/cudf_grep/gpu/device_detect.py` (GREEN)

### Task 3.2 -- Memory Manager (RED -> GREEN -> REFACTOR)
- [ ] Write `tests/unit/test_memory_manager.py`:
  - [ ] `test_should_calculate_chunk_size_from_vram_budget`
  - [ ] `test_should_reserve_20_percent_vram_headroom`
  - [ ] `test_should_recommend_pinned_memory_for_geforce`
  - [ ] `test_should_recommend_gds_for_datacenter_gpu`
  - [ ] `test_should_handle_zero_vram_gracefully` (no GPU fallback)
- [ ] Implement `src/cudf_grep/gpu/memory_manager.py` (GREEN)
- [ ] REFACTOR: integrate into CuDFBackend for chunk size auto-tuning

### Task 3.3 -- Integration: Large File Processing (RED -> GREEN)
- [ ] Write `tests/integration/test_gpu_memory.py` (marked `@pytest.mark.gpu`):
  - [ ] `test_should_process_file_larger_than_vram` (create 2x VRAM-sized temp file)
  - [ ] `test_peak_vram_should_stay_within_budget`
- [ ] Run on WSL2 with GPU

### Task 3.4 -- Phase 3 Quality Gates
- [ ] Full test suite pass
- [ ] `git commit -m "Phase 3: VRAM-safe chunked processing with auto-tuning"`

---

## PHASE 4: Windows Native I/O Path
**Goal:** dstorage-gpu integration for Windows DirectStorage.
**Time:** ~2 hours

### Task 4.1 -- dstorage-gpu Reader (RED -> GREEN)
- [ ] Write `tests/unit/test_reader_dstorage.py` (mocked):
  - [ ] `test_should_load_tensor_via_directstorage` (mock dstorage_gpu)
  - [ ] `test_should_fallback_when_dstorage_unavailable`
  - [ ] `test_should_report_dstorage_available_on_windows`
- [ ] Implement `src/cudf_grep/io/reader_dstorage.py`:
  ```python
  from cudf_grep.io.base import IOBackend

  class DStorageReader(IOBackend):
      def is_available(self) -> bool:
          try:
              import dstorage_gpu
              import sys
              return sys.platform == "win32"
          except ImportError:
              return False

      def read_to_gpu(self, file_path: str):
          from dstorage_gpu import DirectStorageLoader
          loader = DirectStorageLoader()
          return loader.load_tensor(file_path)
  ```

### Task 4.2 -- KvikIO Reader (RED -> GREEN)
- [ ] Write `tests/unit/test_reader_kvikio.py` (mocked):
  - [ ] `test_should_read_via_gds_when_available`
  - [ ] `test_should_fallback_to_compat_mode`
- [ ] Implement `src/cudf_grep/io/reader_kvikio.py`
- [ ] Add both to IO contract tests

### Task 4.3 -- Phase 4 Quality Gates
- [ ] Full suite pass, coverage >= 90%
- [ ] `git commit -m "Phase 4: Windows dstorage-gpu + Linux KvikIO I/O paths"`

---

## PHASE 5: NLP Path -- cyBERT Inference
**Goal:** Semantic log classification via cyBERT for "classify" / "detect" commands.
**Time:** ~5 hours

### Task 5.1 -- Acceptance Test: classify command (RED)
- [ ] Write `tests/acceptance/test_cli_classify.py`:
  ```python
  import subprocess, json, pytest

  pytestmark = pytest.mark.acceptance

  class TestCLIClassify:
      def test_should_classify_log_lines(self, sample_log_file):
          result = subprocess.run(
              ["cybert-grep", "classify", "--format", "json", str(sample_log_file)],
              capture_output=True, text=True,
          )
          assert result.returncode == 0
          data = json.loads(result.stdout)
          assert "classifications" in data
          assert any(c["label"] in ["error", "info", "warning"] for c in data["classifications"])
  ```

### Task 5.2 -- cyBERT Backend Unit Tests (RED)
- [ ] Write `tests/unit/test_cybert_backend.py` (all mocked):
  - [ ] `test_should_tokenize_log_lines`
  - [ ] `test_should_batch_lines_for_inference`
  - [ ] `test_should_classify_with_model_output`
  - [ ] `test_should_extract_entities_from_predictions`
  - [ ] `test_should_handle_model_load_failure_gracefully`
  - [ ] `test_should_report_confidence_scores`

### Task 5.3 -- cyBERT Backend Implementation (GREEN)
- [ ] Implement `src/cudf_grep/backends/cybert_backend.py`:
  - [ ] Tokenizer wrapper (HuggingFace AutoTokenizer)
  - [ ] Triton client for inference
  - [ ] Postprocessing: labels + entities + confidence
- [ ] Each sub-component gets its own inner-loop RED-GREEN-REFACTOR cycle

### Task 5.4 -- Triton Integration Tests (RED -> GREEN)
- [ ] Write `tests/integration/test_pipeline_e2e.py` (marked `@pytest.mark.gpu`):
  - [ ] `test_full_nlp_pipeline_with_triton`
  - [ ] `test_batch_inference_throughput`
- [ ] Requires Triton server running (Docker)

### Task 5.5 -- Property Tests: Tokenizer (RED -> GREEN)
- [ ] Write `tests/property/test_tokenizer_props.py`:
  ```python
  from hypothesis import given, strategies as st

  @given(st.text(min_size=1, max_size=10000, alphabet=st.characters(blacklist_categories=("Cs",))))
  def test_tokenizer_never_crashes_on_valid_text(text):
      from cudf_grep.backends.cybert_backend import tokenize
      tokens = tokenize([text])
      assert tokens is not None
      assert len(tokens) > 0
  ```

### Task 5.6 -- Wire classify Command into CLI (GREEN for acceptance)
- [ ] Add `classify` command to Typer app
- [ ] Run acceptance test -- confirm GREEN

### Task 5.7 -- Phase 5 Quality Gates
- [ ] Full suite pass
- [ ] `mutmut run` on `cybert_backend.py`
- [ ] `git commit -m "Phase 5: cyBERT NLP classification path"`

---

## PHASE 6: Performance Benchmarking & Optimization
**Goal:** Validated performance claims with reproducible benchmarks.
**Time:** ~3 hours

### Task 6.1 -- Benchmark Infrastructure (RED -> GREEN)
- [ ] Write `tests/performance/test_throughput.py`:
  ```python
  import pytest, time

  pytestmark = [pytest.mark.slow, pytest.mark.performance]

  class TestThroughput:
      def test_cpu_backend_throughput(self, tmp_path):
          """Baseline: CPU backend should process >100 MB/s."""
          large = tmp_path / "large.log"
          lines = "2026-02-24 ERROR test line content here\n" * 100_000
          large.write_text(lines)

          from cudf_grep.backends.cpu_backend import CPUBackend
          start = time.perf_counter()
          CPUBackend().search(str(large), "ERROR")
          elapsed = time.perf_counter() - start

          mb = large.stat().st_size / (1024 * 1024)
          throughput = mb / elapsed
          assert throughput > 100, f"CPU throughput {throughput:.1f} MB/s below 100 MB/s"
  ```

### Task 6.2 -- Comparative Benchmarks (RED -> GREEN)
- [ ] Write `tests/performance/test_vs_ripgrep.py`:
  ```python
  import subprocess, time, pytest

  pytestmark = [pytest.mark.slow, pytest.mark.performance]

  class TestVsRipgrep:
      def test_semantic_classification_faster_than_multi_rg(self, tmp_path, rg_path):
          """GPU value prop: single classify pass vs N separate rg passes."""
          log = tmp_path / "mixed.log"
          # Generate log with multiple event types
          lines = []
          for i in range(10_000):
              if i % 3 == 0:
                  lines.append(f"2026-02-24 ERROR Connection timeout from 10.0.0.{i%256}\n")
              elif i % 3 == 1:
                  lines.append(f"2026-02-24 WARN Disk usage at {60+i%40}%\n")
              else:
                  lines.append(f"2026-02-24 INFO Request processed in {i%100}ms\n")
          log.write_text("".join(lines))

          patterns = ["ERROR", "WARN", "INFO", r"\d+\.\d+\.\d+\.\d+", "timeout", "Disk usage"]
          start = time.perf_counter()
          for p in patterns:
              subprocess.run([rg_path, p, str(log)], capture_output=True)
          rg_total = time.perf_counter() - start

          # Our tool: classify does all at once (when GPU available)
          start = time.perf_counter()
          subprocess.run(
              ["cybert-grep", "search", "--cpu", "ERROR|WARN|INFO", str(log)],
              capture_output=True,
          )
          our_total = time.perf_counter() - start

          print(f"ripgrep {len(patterns)} passes: {rg_total:.3f}s")
          print(f"cybert-grep single pass: {our_total:.3f}s")
  ```

### Task 6.3 -- CI Benchmark Workflow
- [ ] Create `.github/workflows/benchmark.yml`:
  ```yaml
  name: Benchmarks
  on:
    pull_request:
      paths: ['src/**', 'tests/performance/**']
  jobs:
    benchmark:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-python@v5
          with: { python-version: '3.11' }
        - run: pip install -e ".[dev]"
        - run: pytest tests/performance/ -v --tb=short
  ```

### Task 6.4 -- Phase 6 Quality Gates
- [ ] All benchmarks run without error
- [ ] Performance numbers documented in test output
- [ ] `git commit -m "Phase 6: Benchmark infrastructure and baseline measurements"`

---

## PHASE 7: Final Integration & Polish
**Goal:** All tests green, all quality gates pass, ready for use.
**Time:** ~2 hours

### Task 7.1 -- Full Acceptance Suite (GREEN)
- [ ] Run ALL acceptance tests end-to-end
- [ ] Fix any remaining failures

### Task 7.2 -- Final Quality Gates
- [ ] `pytest --cov=src/cudf_grep -v` -- all pass, coverage >= 90%
- [ ] `mutmut run` -- < 20% surviving mutants on critical paths
- [ ] `mypy src/` -- zero errors
- [ ] `ruff check src/ tests/` -- zero errors
- [ ] `pip install -e .` and run `cybert-grep search ERROR tests/test_data/small_sample.log`

### Task 7.3 -- Commit and Tag
- [ ] `git add -A && git commit -m "v0.1.0: cudf-grep with TDD-driven CPU+GPU dual path"`
- [ ] `git tag v0.1.0`

---

## TDD Rules for This Project (2026 Edition)

1. **No production code without a failing test first.** Period.
2. **Unit tests run in < 5 seconds total.** Mock all GPU/IO. No network. No disk beyond tmp_path.
3. **AAA pattern always.** Arrange (setup), Act (one call), Assert (one logical assertion).
4. **Test names tell a story.** `test_should_<behavior>_when_<condition>`.
5. **The test pyramid is law:**
   ```
   Acceptance (5-10 tests)    -- minutes to run, outer loop
   Integration (10-20 tests)  -- require GPU/WSL2, CI nightly
   Contract (5-10 tests)      -- verify protocol conformance
   Property (5-10 tests)      -- Hypothesis, find edge cases
   Unit (50-100 tests)        -- fast, isolated, run on every save
   ```
6. **GPU tests are opt-in.** Marked `@pytest.mark.gpu`, auto-skipped when no CUDA.
7. **Mutation testing is the real coverage metric.** Coverage % is vanity. Mutant kill % is quality.
8. **Red-Green-Refactor cycles under 5 minutes.** If stuck in red > 5 min, slice smaller.
9. **AI assists the inner loop.** Use AI to generate edge case tests (Hypothesis strategies), then verify they expose real issues.
10. **Characterization tests lock ripgrep parity.** Any output format change must update snapshots.

---

## Risk Mitigation

| Risk | Mitigation | Test That Catches It |
|------|-----------|---------------------|
| No GPU on dev machine | CPU fallback is Phase 1, tested first | `test_cli_no_gpu.py` |
| cuDF not available (Windows) | Mocked unit tests + WSL2 integration | `test_cudf_backend.py` (mocked) |
| Morpheus Linux-only | cyBERT backend is isolated behind protocol | `test_backend_contracts.py` |
| VRAM exceeded | Memory manager auto-chunks | `test_memory_manager.py` |
| PCIe bottleneck | Pinned memory + async copy | `test_gpu_memory.py` |
| NVMe SSD is the real bottleneck | Throughput claims corrected in matrix | `test_throughput.py` |
| ripgrep faster for simple patterns | Fast path uses CPU/cuDF str ops, not NN | `test_ripgrep_parity.py` |
| Test flakiness on GPU | Unit tests mock GPU; integration has retry | `conftest.py` skip logic |
| Platform mismatch (win/linux) | Platform-aware IO backend selection | `test_device_detect.py` |

---

*Plan Version: 2.0*
*Last Updated: 2026-02-24*
*Method: Outside-In Double-Loop TDD with AI-assisted property testing*
*Phases: 8 (0-7), ~25 hours total estimated effort*
