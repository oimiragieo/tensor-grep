# Rebuild verification checklist

## Read the implementation and existing tests

Locate the feature's public entry points, storage readers and writers, and cleanup
paths. Read tests for failure behavior as well as the successful round trip. Use
symbol names when citing implementation details; line numbers can drift.

```bash
rg --files tests | rg checkpoint
uv run --no-sync pytest tests/unit/test_checkpoint_cli.py -q
```

Bound every test or subprocess run with the platform's timeout mechanism. Test
meaningful behavior: a refusal must name the expected reason, rather than pass
because setup, an import, or command discovery failed.

## Exercise the public entry points

An internal function call does not exercise command parsing, native/Python
routing, launchers, or MCP registration. Use the actual installed or built `tg`
entry point and inspect its output and exit code. Record the executable version
and whether the request used native code, Python, a sidecar, or a daemon.

## Inspect a complete state round trip

Use a disposable scratch directory. Create the state, inspect its real on-disk
files, change the fixture, and restore or invalidate it through the public command.
Check the file contents independently of the command's success message. Include
failure cases for stale state, unreadable paths, malformed records, and interrupted
cleanup when relevant. Keep checkpoints separate from rebuildable caches.

## Verify the documented guards

For each containment, concurrency, budget, or schema guard, locate the actual
producer and consumer. A negative control should distinguish the expected refusal
from unrelated errors; include a valid positive control. Use event handshakes for
concurrency rather than asserting scheduler-dependent wall-clock overlap.

## Run checks appropriate to the change

Follow [contribution guidance](https://github.com/oimiragieo/tensor-grep/blob/main/CONTRIBUTING.md) for the relevant tests,
formatting, lint, type checks, release validation, and CI requirements. Run heavy
benchmarks on dedicated resources and retain the raw evidence for any performance
claim. Check links and examples when changing documentation.

## Report evidence accurately

Distinguish commands actually run and outputs inspected, implementation read but
not exercised, and checks still pending. A passing unit test does not establish
release availability or behavior on an untested platform. Name the exact revision,
environment, commands, and limitations of each result.
