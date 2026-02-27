# Future TDD Implementation Plan (v0.3.0)

Based on 2026 industry standards for AI-assisted Test-Driven Development (TDD) in hybrid Python/Rust ecosystems, we will implement three cutting-edge GPU/SIMD text processing features.

## 1. The 2026 TDD Workflow ("Vibe Coding with Tests")
Modern TDD emphasizes flow, tight integration with `pytest` fixtures, and property-based boundary testing. 
The cycle we will follow:
1. **Write the invariant test first** (`tests/unit/test_stringzilla.py`, `test_cudf_jit.py`).
2. **Implement mocked interfaces** to verify routing logic in `Pipeline`.
3. **Build the minimal implementation** to pass parity with standard CPU tools.
4. **Refactor and optimize** via profiling.

## 2. Feature 1: StringZilla Backend (SIMD Exact Matching)
**Concept:** Research shows `StringZilla v4` performs exact string matching up to 109x faster than standard libraries on modern hardware via SIMD and CUDA bounds.
**TDD Steps:**
- Create `tests/unit/test_stringzilla_backend.py` asserting it returns identical counts to ripgrep for exact string `-F` queries.
- Implement `StringZillaBackend` wrapping `stringzilla.File`.
- Update `Pipeline` to route `-F` and `-c` (count) queries to StringZilla when available.

## 3. Feature 2: cuDF JIT (Just-in-Time) Compilation
**Concept:** NVIDIA's latest 2025/2026 whitepapers show that pre-compiled Regex kernels in cuDF are slower than JIT-compiled custom NVRTC text kernels, which achieve 1x-4x speedups.
**TDD Steps:**
- Create `tests/integration/test_cudf_jit.py` validating that string transformations output correctly.
- Update `CuDFBackend` to accept a `use_jit=True` config flag.
- Utilize `cudf.core.column.string.compile_regex_jit()` (or equivalent NVRTC string kernel builder) for massive data chunks.

## 4. Feature 3: GPU LTL (Linear Temporal Logic) Trace Extraction
**Concept:** Recent research (Valizadeh et al., 2024/2026) demonstrates enumerative program synthesis on GPUs evaluating trace logs 2048x faster than CPU. This allows us to search logs not just for text, but for *logical sequences* (e.g., "Event A happened before Event B").
**TDD Steps:**
- Define the CLI interface: `tg search --ltl "A -> eventually B"`
- Create `tests/unit/test_ltl_backend.py`.
- Map sequence dependencies to a CUDA/PyTorch trace matrix.
