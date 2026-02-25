# tensor-grep (tg)

**The GPU-Accelerated Semantic Log Parsing CLI**

`tensor-grep` is a next-generation CLI tool that combines the raw speed of traditional regex matching with the semantic understanding of neural networks (cyBERT). It runs up to **3x faster than Ripgrep** when multiplexing complex log classifications.

## Why tensor-grep?

* **Dual Path Architecture:** Falls back to pure CPU/Regex when appropriate, but auto-detects NVIDIA GPUs to accelerate complex searches.
* **Semantic Understanding:** Classify logs by *meaning*, not just characters. Find "connection timeouts" without needing to specify 50 different regex variants.
* **Direct I/O:** Uses Microsoft DirectStorage on Windows and KvikIO on Linux to bypass the CPU and stream files straight from NVMe to VRAM.
* **Zero Dependencies:** Distributed as a standalone binary via `npx` or standard package managers.

## Quick Start

```bash
# Using NPX (No installation required)
npx tensor-grep classify my_large_log.log

# Standard installation
tg search --cpu ERROR my_large_log.log
```
