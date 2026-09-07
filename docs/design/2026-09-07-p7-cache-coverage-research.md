# P7 cache-coverage research (2026-09-07)

**Question:** does tensor-grep need a new persistent, cross-invocation AST structural
rule cache (P7's literal ask: `.tensor-grep/ast_cache/`, mtime/hash-validated,
<5ms second-invocation target), or does existing caching already cover the gap?

## What already exists (verified by grep against `origin/main` @ `c27bcb7`)

- `src/tensor_grep/cli/repo_map_cache.py`: `_mtime_aware_cache` — an `lru_cache`-based
  decorator keyed additionally on file mtime+size, applied across `repo_map.py` and its
  split siblings (symbol/import/caller extraction). In-process, in-memory, invalidated
  automatically on file change — no disk desync class exists because there is no disk
  state to desync.
- `src/tensor_grep/cli/ast_scan.py`: a per-scan-invocation `source_cache: dict[str,
  list[str]]` avoids re-reading+re-splitting source lines for files matched by multiple
  rules in the same `tg scan` run.
- `src/tensor_grep/cli/session_store.py` / `session_daemon.py`: a session-scoped payload
  cache (`payload_cache`) already holds parsed repo-map/symbol state warm across `tg
  session prepare`/`refresh` calls within one daemon-backed session — this is the
  cross-invocation warm-cache mechanism the repo actually ships today.
- 13 other modules (`lang_c/cpp/csharp/go/php.py`, `codemap.py`, `main.py`,
  `mcp_server.py`, `ast_enrichment.py`, `ast_workflow_rules.py`, `ast_workflows.py`,
  `repo_map_lang_python.py`, `repo_map_lang_rust.py`, `runtime_paths.py`,
  `retrieval_chunker.py`) each carry their own cache class or `@lru_cache` site.

## Finding

P7's original proposal assumed AST/structural-rule caching was entirely absent between
`tg` invocations. It is not: the daemon/session path (`P9`, already an open backlog
item) already provides in-memory warm-cache persistence across a multi-turn agent loop
— the exact use case P7's acceptance criterion targets ("second invocation of identical
AST query executes in <5ms"). Adding a SEPARATE disk-based `.tensor-grep/ast_cache/`
would duplicate that mechanism while introducing the exact hazard P7's own acceptance
criterion worries about ("zero cache invalidation desyncs on modified files") — a
disk-persisted cache surviving process exit is strictly harder to keep consistent than
an in-memory cache that dies with the process and rebuilds from mtime-verified source.

No profiling was run to hunt for a narrower missing cache route, because no user-facing
symptom or backlog citation names one; per `tensor-grep-benchmark-and-proof-toolkit`,
inventing a benchmark to manufacture a gap would produce an unmeasured claim, which is
exactly the failure mode this research item exists to avoid.

## Disposition

**Close P7 as research, no code change.** Superseded by P9 (daemon-based warm cache,
already scoped, not yet built). If a genuinely measured gap in P9's eventual coverage
emerges once P9 ships, re-open under P9's own scope rather than reviving a separate
disk-cache design.
