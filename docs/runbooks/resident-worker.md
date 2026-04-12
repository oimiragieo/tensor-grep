# Resident AST Worker Runbook

The Resident AST Worker keeps the AST metadata warm in memory, achieving extremely low latency for repeated searches. It communicates via TCP IPC.

## Experimental Status
This feature is currently experimental and must be explicitly opted into by setting:
`TG_RESIDENT_AST=1`

It is not part of the stable default enterprise surface. Treat it as an operator-managed opt-in path until it graduates from [docs/EXPERIMENTAL.md](../EXPERIMENTAL.md).

## Troubleshooting

### 1. IPC Port Conflicts
- **Symptom:** The worker fails to start or claims the port is in use.
- **Diagnosis:** `tg doctor` will report if the TCP port file exists and if the socket is responding.
- **Resolution:**
  - Check `.tg_cache/ast/worker_port.txt` to find the active port.
  - Kill any orphaned worker processes holding that port.

### 2. Orphaned Worker Processes
- **Symptom:** High background CPU/memory usage when `tg` is not actively running.
- **Diagnosis:** Look for lingering `tg worker` processes.
- **Resolution:**
  - Try the supported stop path first: `tg worker --stop`
  - Send a termination signal to the orphaned process: `kill <pid>` (Linux/macOS) or `Stop-Process -Id <pid>` (Windows).
  - Delete `.tg_cache/ast/worker_port.txt`.
