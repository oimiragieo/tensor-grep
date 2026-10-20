# tensor_grep_rs

Rust core crate for `tensor-grep`.

This crate powers fast search/count paths and Python bindings via `pyo3`.

## YAML dependency migration

The native AST workflow now uses the maintained `yaml_serde` parser in place of
`serde_yaml`, removing the unmaintained `unsafe-libyaml` dependency flagged by
[RUSTSEC-2024-0449](https://rustsec.org/advisories/RUSTSEC-2024-0449).
The replacement still contains unsafe Rust; this change does not claim otherwise.
CLI arguments, YAML configuration formats, and Python binding signatures are unchanged.

Rust callers of `AstWorkflowOrchestrator::extract_rule_pattern` or
`extract_rule_member_patterns` must construct values with the new parser. Its `Value`
has a different Rust type identity. Use this dependency alias in the caller's manifest:

```toml
serde_yaml = { package = "yaml_serde", version = "0.10.7" }
```

Existing `serde_yaml::from_str` calls then construct the expected argument type.
Native compatibility tests cover merge precedence, composite rules, scalar metadata,
Unicode, and malformed input. To roll back, revert the manifest, lockfile, and tests
together; this also restores the advisory-affected dependency.
