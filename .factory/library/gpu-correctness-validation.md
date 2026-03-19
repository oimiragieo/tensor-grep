## GPU correctness validation (VAL-GPU-006)

- Date: 2026-03-16
- Live validation now works on this host when the sidecar is pinned to `C:\dev\projects\tensor-grep\.venv_cuda\Scripts\python.exe` and `PYTHONPATH` is set to `C:\dev\projects\tensor-grep\src`.
- Use `C:\dev\projects\tensor-grep\rust_core\target\release\tg.exe` with corpus `C:\dev\projects\tensor-grep\artifacts\gpu_bench_data\10MB`.
- Working GPU: device `0` (`NVIDIA GeForce RTX 4070`). Device `1` (`RTX 5070`) is still unsupported by the current `torch 2.6.0+cu124` stack.

Validated patterns against both CPU `tg search --json` and `rg --json --no-ignore`:

1. `gpu benchmark sentinel` → 2 matches, 1 file, parity PASS
2. `WARN retry budget exhausted` → 16 matches, 8 files, parity PASS
3. `Database connection timeout` → 8 matches, 8 files, parity PASS

Recommended rerun pattern:

```powershell
$env:TG_SIDECAR_PYTHON = 'C:\dev\projects\tensor-grep\.venv_cuda\Scripts\python.exe'
$env:PYTHONPATH = 'C:\dev\projects\tensor-grep\src'
C:\dev\projects\tensor-grep\rust_core\target\release\tg.exe search --gpu-device-ids 0 --json --no-ignore "gpu benchmark sentinel" C:\dev\projects\tensor-grep\artifacts\gpu_bench_data\10MB
```

If the default `.venv` is used without the sidecar override, the historical fallback remains `C:\dev\projects\tensor-grep\artifacts\bench_gpu_scale.json`, whose `correctness_checks` field records the same three patterns as PASS on the RTX 4070.
