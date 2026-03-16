# GPU benchmark environment notes

- Local `python` / `.venv` is CPU-only (`torch 2.9.1+cpu`); explicit GPU benchmark runs need `.venv_cuda\Scripts\python.exe`.
- `benchmarks/run_gpu_benchmarks.py` now auto-resolves `.venv_cuda` as `TG_SIDECAR_PYTHON` and injects `PYTHONPATH=src` for sidecar-backed `tg.exe` GPU runs.
- On this host, `.venv_cuda` reports:
  - GPU 0: `NVIDIA GeForce RTX 4070` (`sm_89`) — operational.
  - GPU 1: `NVIDIA GeForce RTX 5070` (`sm_120`) — unsupported by current `torch 2.6.0+cu124`; probe and real search fail with `CUDA error: no kernel image is available for execution on the device`.
- If future workers benchmark or validate GPU behavior directly via `tg.exe`, they should expect RTX 5070 failures until the CUDA/PyTorch stack is upgraded.
