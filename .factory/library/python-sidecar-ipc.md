# Python Sidecar IPC

- Rust sidecar entrypoint lives in `rust_core/src/python_sidecar.rs`.
- Python receiver lives in `src/tensor_grep/sidecar.py` and package entrypoint for `python -m tensor_grep` lives in `src/tensor_grep/__main__.py`.
- Request JSON schema: `{"command": str, "args": [str, ...], "payload": object|null}`.
- Response JSON schema: `{"stdout": str, "stderr": str, "exit_code": int, "pid": int}`.
- Rust uses three threads per invocation: one writer for stdin and one reader each for stdout/stderr before `wait()`. This avoids deadlock on multi-megabyte stdin/stdout payloads.
- Supported env overrides for tests/diagnostics:
  - `TG_SIDECAR_PYTHON`: override Python executable path.
  - `TG_SIDECAR_MODULE`: override `python -m ...` module (default `tensor_grep.sidecar`).
  - `TG_SIDECAR_SCRIPT`: run `python <script>` instead of module for crash/sleep mocks.
  - `TG_SIDECAR_TIMEOUT_MS`: override the Rust sidecar wait timeout (default 30000 ms) for timeout/kill tests.
- GPU sidecar searches now reject invalid requested device IDs before backend selection and return a user-facing CUDA-unavailable error when `CUDA_VISIBLE_DEVICES` is explicitly empty.
- Windows note: when launching `.venv\Scripts\python.exe`, the live interpreter PID observed from Python may be a child of a venv shim process rather than directly of `tg.exe`. Verification should at least confirm distinct live `tg` and `python` PIDs.
