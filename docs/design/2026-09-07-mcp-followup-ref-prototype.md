# MCP-SURFACE extension: bounded follow-up reference prototype

**Date:** 2026-09-07
**Owner:** MCP-SURFACE extension, `docs/plans/2026-09-07-agentic-quality-simplification.md` Task 10.
**Scope of this slice:** one checkbox of Task 10 — "Prototype bounded follow-up references
for omitted source. Bind each reference to root, snapshot identity, parameters, expiry and
range. A stale/tampered/cross-root reference must fail or force explicit refresh, not return
silently unrelated source."

## What shipped

`src/tensor_grep/cli/mcp_followup_ref.py`: `mint_followup_ref()` / `resolve_followup_ref()`,
an HMAC-signed, opaque token binding an omitted-content range reference to:

- **root** — the repo root the reference was minted under (cross-root reference rejected)
- **path** — confined under root via canonical-path resolution (traversal/symlink escape/
  absolute-path rejected at mint)
- **params_hash** — the exact request parameters that produced the omission (a follow-up
  under different parameters cannot silently reuse a stale range)
- **snapshot_id** — a SHA-256 content hash of the file at mint time (not mtime+size, which a
  same-size replacement could pass through unnoticed)
- **expiry** — TTL-bounded, exclusive boundary (`now >= expires_at` fails), all numeric fields
  validated as finite and non-bool

`resolve_followup_ref` fails closed with a specific `FollowupRefError.reason` for every
adversarial case tested: `malformed`, `tampered`, `cross_root`, `params_mismatch`, `expired`,
`stale_snapshot`, `unsupported_version`.

## What did NOT ship (explicitly out of scope for this slice)

- **No MCP tool registration.** This is a standalone primitive — it is not called from
  `mcp_server.py`'s default response path, so it makes no default catalog/protocol switch (the
  literal constraint in the Task 10 title).
- **No byte-serving.** `resolve_followup_ref` returns a validated range *descriptor*
  (path/range/snapshot_id), not bytes. See the KNOWN LIMITATION docstring in the module: a real
  byte-serving integration must re-verify the content hash from the SAME read it serves (open
  once, hash what was actually read, compare, then return those bytes) — never trust a hash
  computed in an earlier, separate stat/read pass. This is a documented TOCTOU boundary, not a
  silently-accepted gap.
- **Response profiles, byte caps, structuredContent/outputSchema negotiation, tools/list
  cursor semantics** — the rest of Task 10's checkboxes remain unstarted.

## Audit trail

Codex Luna (`gpt-5.6-luna`, read-only) ran 4 rounds:
- Round 1: 2 HIGH (forgeable snapshot identity via mtime+size; unconfined path traversal),
  2 MEDIUM (incomplete schema validation; unchecked byte range vs file size), 1 LOW (inclusive
  expiry boundary).
- Round 2: 1 HIGH (TOCTOU between validation and consumption — accepted as a documented
  limitation, not fixed, since this primitive is deliberately descriptor-only), 1 MEDIUM
  (incomplete numeric/type schema validation).
- Round 3: 2 defects (NaN/inf TTL bypass; missing `issued_at` validation) — both fixed.
- Round 4: 2 remaining internal-API hardening items (bool coercion on mint-side `ttl_seconds`/
  `byte_range`/`now`) — fixed. Not formally re-audited (round budget; these are internal-API
  inputs from this codebase's own callers, not attacker-reachable payload fields like the
  round-1/2/3 findings were).

Final state: 25/25 tests green, ruff clean, ruff format clean.
