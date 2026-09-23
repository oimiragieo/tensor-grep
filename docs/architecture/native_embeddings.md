# Pure-Rust Native Embedding Architecture (`rust_core`)

## 1. Overview & Motivation
Currently, whole-repo semantic search (`tg find`) and semantic re-ranking (`tg search --semantic`) rely on Python-side dependencies (`model2vec`, `numpy`) and a local model cache (`~/.tensor-grep/models/potion-code-16M`). When these optional Python extras are missing, `tensor-grep` gracefully falls back to BM25 lexical ranking.

To eliminate Python runtime dependencies and enable zero-dependency standalone native binary execution across Windows, Linux, and macOS, `rust_core` will embed an in-process ONNX neural inference engine.

---

## 2. Engine Selection: `tract` vs `ort`

| Engine | Pros | Cons | Best Fit |
| :--- | :--- | :--- | :--- |
| **`tract`** (Sonos) | • 100% pure Rust (no C/C++ toolchain needed).<br>• Fully static linking without shared library bundles.<br>• Extremely low overhead for small models (~16M-30M parameters). | • CPU-only inference.<br>• Slower for massive batch sizes (>128). | **Default embedded engine** for portable standalone `tg` binaries. |
| **`ort`** (ONNX Runtime) | • Hardware acceleration (DirectML on Windows, CoreML on macOS, CUDA on Linux).<br>• High throughput batch vectorization. | • Requires dynamic C library distribution or vendor DLLs (`onnxruntime.dll`).<br>• Increases binary/distribution footprint. | **Accelerated engine** for GPU/DirectML-enabled builds. |

---

## 3. Architecture & Data Flow

```
[Repository Files / Chunks]
             │
             ▼
   [In-Tree Fast Tokenizer]
   (tokenizers-rs WordPiece/BPE)
             │
             ▼
   [Tract ONNX Inference]
   (potion-code-16M static graph)
             │
             ▼
     [Embedding Vectors]
    (128-dim/256-dim FP32)
             │
             ▼
 [HNSW / SIMD Dot-Product Index]
             │
             ▼
  [Fused BM25 + Dense Results]
```

1. **Tokenizer**: Fast in-process tokenization via Hugging Face's `tokenizers` crate, compiled directly into the binary.
2. **Model Graph**: Embedded or lazily fetched statically-quantized ONNX model graph (e.g. `potion-code-16M-q8.onnx` ~15MB).
3. **Inference Pipeline**:
   - Zero-copy tensor layout directly over chunk string slices.
   - Mean-pooling layer with L2 normalization computed via AVX2 / NEON SIMD intrinsics.
4. **Ranking & Fusion**:
   - In-memory cosine distance matrix combined with BM25 scores via Reciprocal Rank Fusion (RRF).

---

## 4. Migration & Compatibility Plan
- Phase 1: Pure-Rust ONNX inference in `rust_core` exposed via internal CLI subcommand.
- Phase 2: Native `tg find` bypasses Python entirely when native embeddings are enabled.
- Phase 3: Seamless fallback to BM25 if neither native model nor Python environment is present.
