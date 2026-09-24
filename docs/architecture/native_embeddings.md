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

---

## 5. Correction (2026-09-23): model2vec is static, and a native Rust crate now exists

Sections 1-4 above scoped the native path through ONNX (`tract`/`ort`) because that is what a
generic "run a neural model in Rust" plan reaches for. That premise does not hold for
`potion-code-16M`: **model2vec produces static embeddings** (a fixed per-token lookup table
distilled from a larger model, summed/mean-pooled with no forward pass through transformer
layers at inference time) -- it is not a model needing an ONNX runtime at all.

- **The official Rust crate is `model2vec-rs`** (MinishLab, v0.2.1, <https://github.com/MinishLab/model2vec-rs>,
  also on crates.io). It loads a model2vec `.safetensors`/tokenizer pair directly and computes the
  same static lookup+pooling in Rust with no ONNX graph, no `tract`, and no `ort` dependency.
  **This supersedes Section 2's tract/ONNX route for the model2vec (`potion-code-16M`) path
  specifically** -- the engine-selection tradeoff table in Section 2 still applies to any FUTURE
  transformer-style (non-static) embedding model this project might adopt, but it is the wrong
  frame for the model actually in use today.
- **License caveat, verify before adopting:** the crate's GitHub repository states MIT, but its
  crates.io package metadata reports the SPDX license field as `"non-standard"` at the time of this
  writing. Confirm the actual `Cargo.toml` `license`/`license-file` fields and reconcile the two
  before taking a dependency on it -- do not assume the GitHub README's MIT statement alone clears
  `supply-chain-hardening`'s license-floor gate.
- This correction does not change Section 4's phased migration shape; it changes what Phase 1
  embeds. A future implementer should re-derive `model2vec-rs`'s current version and license
  status rather than trusting this paragraph's date-stamped snapshot.

### `--focus` goal-conditioned skimming: demand-gated, not adopted

SWE-Pruner (arXiv:2601.16746) is a trained ~0.6B-parameter skimmer that cuts SWE-Bench-Verified
context tokens by roughly 23-54% by learning what to keep for a given task/goal. It is a real,
measured result, but it requires shipping and running a dedicated ~0.6B model -- heavy for a
CPU-only, zero-Python-dependency product whose whole native-embeddings story (Sections 1-4 above)
exists specifically to avoid exactly that kind of runtime weight. A goal-conditioned `--focus` flag
built on this approach stays demand-gated: no CPU-shaped instrument has shown a lexical/AST/static-
embedding approach is competitive with a trained skimmer, so building `--focus` today would mean
either shipping the heavy model (contradicting the CPU-product goal) or shipping something unproven
against it. Revisit only if a CPU-shaped skimming result appears, or if real demand is measured
(see `tensor-grep-demand-gate-measurement`).

### CodeComp: dropped, not portable to a CLI

CodeComp (arXiv:2604.10235) is a KV-cache compression technique -- it reduces the memory/compute
footprint of an LLM's own attention cache across a long-running inference session inside that LLM's
serving stack. `tg` is a CLI that produces context payloads consumed by an external agent/LLM
process it does not control the KV cache of; there is no KV cache inside `tg` for this technique to
compress. It is dropped from consideration entirely, not demand-gated -- there is no `tg`-side
integration point for it to attach to, unlike `--focus` above, which is a plausible (if currently
unproven) future feature.
