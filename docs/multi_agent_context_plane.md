# Shared repository context for concurrent clients

Multiple local clients can reuse repository snapshots through sessions and an
optional loopback daemon. Sessions share computed code facts; they do not assign
tasks, grant edit permission, or coordinate an entire agent workflow.

## Repository-scoped session store

Sessions live under `<repo>/.tensor-grep/sessions/`. The session store uses atomic
writes and cross-process locks for creation and refresh, and bounds retention with
`TG_SESSION_MAX` (default 64). Session identifiers and recorded roots are validated
before loading. Files in a snapshot are checked for size/mtime changes; stale
sessions must be refreshed before reuse.

See `src/tensor_grep/cli/session_store.py` for the storage implementation and
[the harness API](harness_api.md#session-open-json) for response shapes.

## Optional warm daemon

The daemon caches parsed session payloads and selected responses in memory.
Clients authenticate with a per-daemon token over a loopback socket; request
framing, read bounds, and root confinement are described in the
[daemon protocol](session_daemon_protocol.md). Token-file protection uses filesystem
permissions, with best-effort Windows ACL restriction; it is not remote access control.
Idle and maximum-uptime bounds limit lifetime.

```bash
tg session daemon start .
tg context-render . "invoice payment"
tg edit-plan . "add a refund path"
tg session daemon status . --json
tg session daemon stop .
```

Top-level eligible native-provider `context-render` and `edit-plan` requests can
reuse a running daemon. Explicit `tg session ... --daemon` requests start or reuse
one. Implicit sessions are keyed by root and `max_repo_files`; response cache keys
also include output-affecting request options. A repeat request is not guaranteed
to hit the cache when those options or the snapshot change.

## Scope and limits

- MCP `tg_session_*` tools use the on-disk session store in process; they do not
  use the daemon socket's response cache.
- Context-render/edit-plan response-cache hits use `snapshot_content_sha256` checks
  and do not detect added files. Run `tg session refresh` or opt into
  `refresh_on_stale` to include new files. Symbol commands have their own
  added-file-sensitive cache policy; consult [Contracts](CONTRACTS.md).
- Warm responses retain scan, output-budget, and resolution-gap disclosures.
  Reusing a snapshot does not turn bounded code analysis into proof of absence.
- The experimental ledger provides explicit advisory claims and reusable findings;
  it does not lock edits, automatically supply daemon responses, or provide
  cross-repository coordination. See [ledger contracts](CONTRACTS.md).

## Local demand metrics

The daemon can persist diagnostic counts in
`<repo>/.tensor-grep/sessions/daemon_metrics.json`. It records daily distinct-client
and overlapping-request counts, plus hashes of repeated targets rather than raw
query/symbol text. These hashes are pseudonymous diagnostic data, not a guarantee
that a target cannot be guessed. Recording is bounded to 30 day buckets and 32
duplicate-target hashes per day. Set `TG_DAEMON_METRICS=0` to opt out.

`tg session daemon status . --json` and `tg doctor . --json` expose the metrics even
when the daemon is stopped. The trailing-window `pre_gate` states distinguish
`NO-COVERAGE` (no observations), `NOT-MET` (observations below thresholds), and `MET`
(observed concurrency and repeats). They are diagnostics, not evidence of speed,
permission, or correctness. Metrics failures must not prevent request serving.
