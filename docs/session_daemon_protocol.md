# Session Daemon Protocol

The warm-session daemon is the only network-listening surface `tg` has. This page
specifies its wire protocol and its authentication model.

It exists because an audit (2026-08-19, §9) found that the protocol was documented
**only in code comments** — `docs/architecture.md` said "authenticated with HMAC" and
stopped there. A reader rebuilding this component from the docs would have had to
reinvent the security model, and would probably have got it wrong in a way that looked
fine. Every claim below cites `src/tensor_grep/cli/session_daemon.py` so it can be
re-derived rather than trusted.

> **Cite the symbol, not the line.** Constants are named here rather than pinned to line
> numbers, which drift. Locate them with
> `grep -n '_DAEMON_HOST\|_MAX_DAEMON_REQUEST_BYTES' src/tensor_grep/cli/session_daemon.py`.

---

## 1. Transport and binding

| Property | Value | Constant |
|---|---|---|
| Host | `127.0.0.1` — loopback only, never `0.0.0.0` | `_DAEMON_HOST` |
| Port | ephemeral, recorded in the session metadata file | — |
| Framing | one JSON object per line, UTF-8, `\n`-terminated | — |
| Connect timeout | 0.5 s | `_DAEMON_CONNECT_TIMEOUT_SECONDS` |
| Per-request socket read timeout | 30 s | `_DAEMON_HANDLER_TIMEOUT_SECONDS` |
| Startup wait | 5 s | `_DAEMON_START_TIMEOUT_SECONDS` |

The daemon is a local IPC mechanism, not a service. Binding to loopback is a hard
requirement: it is the outer boundary that keeps the token from being the *only* thing
between an attacker and the index.

---

## 2. Authentication

### Token generation

`run_session_daemon_server` generates `secrets.token_urlsafe(32)` per daemon instance.
Tokens are never derived from the path, PID, or anything guessable, and never reused
across daemons.

### Token storage

The token is written into the session metadata file, created with:

```python
os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _DAEMON_METADATA_MODE)  # 0o600
```

Three properties matter, and all three are deliberate:

- **`O_EXCL`** — creation fails if the path already exists, so a pre-planted file cannot
  be written through.
- **Mode passed to `os.open`**, not applied afterwards with `chmod`. The kernel applies
  `0o600` *atomically at creation*, so there is no window in which the file exists and is
  world-readable. A write-then-chmod sequence has exactly that window.
- **Windows ACL lockdown** is applied *before* the token bytes are written, so the
  narrower permission is in force for the entire lifetime of the secret.

### The authorization gate

Every request carries the token in a top-level `"token"` field
(`_DAEMON_TOKEN_FIELD`). `_ThreadedSessionDaemon.is_authorized` decides:

```python
if not self.token:
    return False  # fail closed, see below
provided = request.get(_DAEMON_TOKEN_FIELD)
if not isinstance(provided, str) or not provided:
    return False
return hmac.compare_digest(provided, self.token)  # constant time
```

`hmac.compare_digest` is required, not stylistic: a plain `==` short-circuits on the
first differing byte and leaks the token one character at a time to a local attacker who
can time responses.

### Fail-closed on a tokenless daemon

**A daemon with no token refuses everything.** This reversed an earlier behaviour where
tokenless meant "authorize everything" as a legacy/in-test convenience.

The reasoning is worth preserving, because the earlier behaviour is the tempting one:
production *always* generates a token, so a tokenless daemon is a **misconfiguration**.
A misconfiguration must refuse everything, never accept everything — the failure mode of
a broken gate must be denial.

Tokenless *construction* remains legal (some lifecycle tests need it); only tokenless
*authorization* is forbidden. The invariant lives in `is_authorized` alone — the
constructor deliberately does not also raise, so there is exactly one place to audit.

---

## 3. Ordering: authenticate before anything else

`_SessionDaemonHandler.handle` performs, strictly in this order:

1. **Bounded read** of one request line (§4) — before parsing.
2. **`json.loads`**.
3. **`is_authorized`** — *before dispatching any command or resolving any path*.
4. Only then: activity note, in-flight accounting, session resolution, dispatch.

Steps 3 and 4 must not be reordered. Resolving a path before authenticating would let an
unauthenticated client probe the filesystem through error messages. In-flight accounting
is also deliberately *after* auth, so unauthenticated clients cannot hold the daemon open
against its own idle-shutdown lifecycle.

An unauthorized request receives a fixed, non-informative envelope and the connection
closes:

```json
{"version": …, "session_id": "", "error": {"code": "unauthorized", "message": "invalid or missing daemon token"}}
```

The message is identical for a missing token, a malformed token, and a wrong token.
Distinguishing them would be an oracle.

---

## 4. Pre-authentication DoS bound

`_read_bounded_request_line` reads at most `_MAX_DAEMON_REQUEST_BYTES` (**1 MiB**).

A bare `rfile.readline()` with no size argument buffers an entire line into memory
*before* the caller can authenticate, so one hostile local client sending an endless line
with no newline exhausts memory without ever holding a credential. The bound is therefore
applied **pre-auth**, together with the 30 s socket read timeout — an attacker must not
be able to consume unbounded memory *or* unbounded time before proving who they are.

---

## 5. Rebuilding this component: verification checklist

A rebuild is correct only if all of these hold. Each is a property to test, not to read.

- [ ] The listener binds `127.0.0.1` and **cannot** be configured to `0.0.0.0`.
- [ ] Each daemon generates a fresh cryptographically-random token.
- [ ] The metadata file is created with `O_CREAT|O_EXCL` and mode `0o600` **passed to
      `os.open`**; verify no window exists where the file is readable by others.
- [ ] A request with **no** token is refused.
- [ ] A request with a **wrong** token is refused.
- [ ] A request with the **correct** token succeeds. *(Without this arm the previous three
      pass trivially against a gate that refuses everything.)*
- [ ] Token comparison is constant-time — assert `hmac.compare_digest` is the comparator;
      a timing test is too noisy to be the gate.
- [ ] A daemon constructed **without** a token refuses every request, including one
      carrying an empty token.
- [ ] A request line exceeding 1 MiB is rejected **without** the process buffering it.
- [ ] No filesystem path is resolved before authorization — assert on the code path taken,
      not on wall-clock behaviour.
- [ ] Unauthorized responses are byte-identical across missing / malformed / wrong tokens.

The sixth item is the one people skip, and it is the one that makes the other refusal
arms meaningful: a gate that denies everything passes every negative test.
