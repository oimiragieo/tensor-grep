# Triton Inference Server Client API

## HTTP Client Constructor

`tritonclient.http.InferenceServerClient` accepts timeout kwargs:

- `connection_timeout` (float, seconds): Timeout for establishing connection to server
- `network_timeout` (float, seconds): Timeout for network operations after connection

Both are passed directly to the underlying HTTP client (urllib3/requests).

## Usage in tensor-grep

In `src/tensor_grep/backends/cybert_backend.py`, the `_create_triton_http_client()` helper centralizes client construction. Timeout is controlled by `_get_triton_timeout_seconds()` which reads the `TENSOR_GREP_TRITON_TIMEOUT_SECONDS` env var (default: 5.0 seconds, stored in `_DEFAULT_TRITON_TIMEOUT_SECONDS`). Both `connection_timeout` and `network_timeout` are set to the same value.

### Edge cases for timeout parsing

- **Negative values** are clamped to `0.0` (immediate timeout).
- **Empty string** or **non-numeric string** falls back to the default (5.0 seconds).
- **`float("inf")`** (set via `TENSOR_GREP_TRITON_TIMEOUT_SECONDS=inf`) is intentionally accepted as a valid "no timeout" value.
