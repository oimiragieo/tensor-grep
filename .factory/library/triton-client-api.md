# Triton Inference Server Client API

## HTTP Client Constructor

`tritonclient.http.InferenceServerClient` accepts timeout kwargs:

- `connection_timeout` (float, seconds): Timeout for establishing connection to server
- `network_timeout` (float, seconds): Timeout for network operations after connection

Both are passed directly to the underlying HTTP client (urllib3/requests).

## Usage in tensor-grep

In `src/tensor_grep/backends/cybert_backend.py`, the `_create_triton_http_client()` helper centralizes client construction with 5-second defaults for both timeouts (module-level constants `TRITON_CONNECTION_TIMEOUT_SECONDS` and `TRITON_NETWORK_TIMEOUT_SECONDS`).
