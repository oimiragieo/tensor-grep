## Harness API docs notes

- `bench_data/*.log` is ignored by default, so native search and index example generation must pass `--no-ignore`.
- On this Windows host, `tg.exe search --gpu-device-ids 0 ...` currently fails with `Explicit GPU device selection [0] could not initialize a GPU backend: CuDF and Torch GPU backends were unavailable`.
- The native routing code checks for a warm `.tg_index` before honoring explicit `--gpu-device-ids`, so delete any temp `.tg_index` or use a fresh corpus when generating GPU-sidecar examples.
- `TG_SIDECAR_SCRIPT` is a practical way to exercise the Rust GPU-sidecar envelope normalization path when GPU Python backends are unavailable.
