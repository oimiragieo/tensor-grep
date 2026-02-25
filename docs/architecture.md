# Architecture

`tensor-grep` uses an **Outside-In Double-Loop** TDD methodology with a **Platform-Aware Architecture**.

## Multi-Pass Query Analyzer

The core of `tensor-grep` is the Query Analyzer, which routes patterns to the fastest available path:

1. **CPU Fallback Path:** Simple regexes are routed to standard Python regex processing.
2. **GPU Fast Path (cuDF):** High-speed string operations executed directly on the GPU.
3. **NLP Path (cyBERT):** Complex classifications are routed to the transformer network.

## Multi-Platform I/O

File reading is aggressively optimized for the host OS:

* **Linux:** Uses `KvikIO` for GPUDirect Storage (GDS).
* **Windows:** Uses `dstorage-gpu` for Microsoft DirectStorage.
* **Fallback:** Standard standard Python I/O with chunking.
