## NDJSON streaming search contract

- `tg search --ndjson` is implemented in `rust_core/src/main.rs` for both `search` and positional search mode.
- `--json` and `--ndjson` conflict at clap parsing time for search commands.
- `tg run` does not accept `--ndjson`; `tg run --rewrite --ndjson` fails during CLI parsing without entering Python-backed paths.
- Each NDJSON line uses the unified Rust envelope plus direct match fields:
  - `version`, `routing_backend`, `routing_reason`, `sidecar_used`
  - `query`, `path`, `file`, `line`, `text`
- GPU sidecar NDJSON requests JSON from the sidecar internally, then normalizes each sidecar match row to the native `line` field (not `line_number`).
