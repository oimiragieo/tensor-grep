# Windows Rust Environment

## Cargo PATH

On the Windows CI/dev environment, `cargo` is **not** on the default PATH.
Workers must use the full path to invoke Cargo commands:

```
C:\Users\oimir\.cargo\bin\cargo.exe
```

For example:
```powershell
C:\Users\oimir\.cargo\bin\cargo.exe test --manifest-path rust_core/Cargo.toml
```

The `services.yaml` `rust_test` command assumes `cargo` is on PATH, so workers
should substitute the full path if `cargo` is not found.

## rg (ripgrep) PATH

`rg.exe` is **not** on the system PATH. Use either:
- `TG_RG_PATH=<path>` env var to point tg.exe at the rg binary
- `benchmarks/rg.zip` auto-extract (benchmark scripts handle this automatically)
- The bundled rg in the cargo install dir: `C:\Users\oimir\.cargo\bin\rg.exe`

## bench_data .gitignore issue

`bench_data/*.log` files are matched by a `.log` gitignore pattern. When running
rg or tg.exe against bench_data, you MUST pass `--no-ignore` (or for rg, `-u`/`--unrestricted`)
to see the `.log` files. Benchmark scripts must account for this.

## hyperfine

`hyperfine` is **not** installed on this machine. Use PowerShell `Measure-Command` as an
alternative for cold-start timing:
```powershell
(1..5 | ForEach-Object { Measure-Command { & tg.exe search ERROR bench_data } }).TotalMilliseconds | Measure-Object -Average
```
Or install hyperfine: `cargo install hyperfine`
