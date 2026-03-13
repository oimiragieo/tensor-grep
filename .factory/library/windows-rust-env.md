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
