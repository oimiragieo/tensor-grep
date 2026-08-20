# Decision record: MCP 2.0 exposure (PIN_AND_DEFER)

**Date:** 2026-08-20. **Item:** `W2-a` in
`docs/plans/2026-08-20-worldclass-closeout-plan.md` §W2. **Base:** `origin/main` at `7dfff2f`
(`refactor: split mcp_server.py into mcp_rewrite_tools/mcp_audit_tools/mcp_symbol_tools (#1051)`).

## What this decides

Whether tensor-grep should migrate `src/tensor_grep/cli/mcp_server.py` (built on
`mcp.server.fastmcp.FastMCP`, `mcp>=1.27.2,<2`) to the MCP 2.0 wire protocol and the `mcp` 2.x
Python SDK now. It does not decide anything about `MCP-SURFACE` / Task 2C, which is a separate,
already-fenced item.

## Re-derived evidence (in-tree, at `7dfff2f`)

- `grep -n 'mcp>=' pyproject.toml` -> `586:    "mcp>=1.27.2,<2",`
- `grep -n 'from mcp' src/tensor_grep/cli/mcp_server.py` ->
  `24:from mcp.server.fastmcp import FastMCP` and
  `26:from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS` — tg delegates wire-protocol
  version negotiation to the SDK entirely; it does not hand-roll a handshake.
- `grep -n '_TG_MCP_SERVER_CONTRACT_VERSION *=' src/tensor_grep/cli/mcp_server.py` -> **`188`**
  (value `"1.7.0"`). This is tg's own tool-surface contract number and is unrelated to the MCP
  wire protocol version — do not conflate the two when reading trigger T3 below.
- Guard already in place on the upper bound: `tests/unit/test_mcp_dependency_is_upper_bounded.py`
  (it exists precisely because `mcp` 2.0.0 deleted `mcp.server.fastmcp`, which would break every
  fresh install of tg's rewrite path — see that file's own docstring for the 2026 incident this
  guard was written to prevent).

## Research re-verified at authoring time (2026-08-20)

Both lookups below were re-derived directly for this record, not carried over from the plan's
research receipt (its anchors are cited only as history in that receipt; the plan explicitly
requires this record to re-derive, not trust, the underlying facts).

1. **The spec revision is real and matches the plan's description.** Fetched
   `https://modelcontextprotocol.io/specification/2026-07-28/changelog` on 2026-08-20. Confirmed
   verbatim, itemized 1–8 in the "Major changes" section:
   - protocol-level sessions and the `Mcp-Session-Id` header removed from Streamable HTTP (SEP-2567);
   - the `initialize` / `notifications/initialized` handshake removed — protocol version and
     capabilities now travel in `_meta` on every request (SEP-2575);
   - **`server/discover` MUST be implemented** by every server (SEP-2575);
   - the HTTP GET endpoint and `resources/subscribe`/`resources/unsubscribe` are replaced by a
     single `subscriptions/listen` long-lived stream (SEP-2575);
   - `ping`, `logging/setLevel`, and `notifications/roots/list_changed` removed; log level now
     travels per-request in `_meta` (SEP-2575);
   - experimental tasks moved out of core into an `io.modelcontextprotocol/tasks` extension,
     replacing blocking `tasks/result` with polling `tasks/get`/`tasks/update` (SEP-2663);
   - Multi Round-Trip Requests (MRTR) replace server-initiated requests (`roots/list`,
     `sampling/createMessage`, `elicitation/create`) with an `InputRequiredResult` /
     `inputResponses` retry pattern (SEP-2322);
   - every result now carries a required `resultType` field (`"complete"` or `"input_required"`)
     (SEP-2322).

   This is not additive — it removes core RPCs (`initialize`, `ping`) and adds a mandatory new one
   (`server/discover`). A 2.0 port of `mcp_server.py` is a rewrite of the transport layer, not a
   dependency bump.

2. **The upstream maintainers themselves recommend the pin tg already carries.** Fetched
   `https://pypi.org/pypi/mcp/json` on 2026-08-20. `info.version` (PyPI "latest") is **`2.0.0`**.
   The package README states, verbatim: *"This is v2 of the MCP Python SDK, the current stable
   release line... Not ready to migrate? v1.x lives on the `v1.x` branch, continues to receive
   critical bug fixes and security patches... Since `pip install mcp` now installs 2.x, keep a
   `<2` upper bound on your requirement (for example `mcp>=1.28,<2`) until you've migrated."*
   Also confirmed `mcp.server.fastmcp` — the module tg imports — no longer exists in 2.x (matches
   `test_mcp_dependency_is_upper_bounded.py`'s own incident history).

   Latest maintained `1.x` release, re-derived directly from the PyPI releases index (not the
   README prose, which only states the policy):

   ```
   1.27.1  2026-05-08T16:50:10Z
   1.27.2  2026-05-29T17:16:02Z
   1.28.0  2026-06-16T21:37:16Z
   1.28.1  2026-06-26T12:57:27Z
   1.29.0  2026-07-28T13:41:40Z   <- latest v1.x
   ```

   `1.29.0`, uploaded **2026-07-28**, is the current maintained-branch head. tg's floor at the
   time of this record is `1.27.2` (two patch releases behind the maintained head); the floor
   bump to `1.29.0` is item `W2-b`, a separate slice per the collision map — this record does not
   touch `pyproject.toml`.

## Decision

```yaml
decision: PIN_AND_DEFER
revalidate_by: 2027-02-20            # 6 months out from this record; time-bounded trigger T6
monitoring_owner: tensor-grep-release-drift-check post-release sweep
triggers:
  - id: T1  type: upstream_maintenance_end   source: https://pypi.org/pypi/mcp/json  checked: 2026-08-20
  - id: T2  type: client_incompatibility     bar: "a NAMED client with a reproduction case that
            cannot be resolved by a client-side pin; a single speculative issue does NOT qualify"
  - id: T3  type: internal_unblock           detail: "Task 2C clears, unblocking MCP-SURFACE"
  - id: T4  type: python_platform_support_loss  detail: "maintained 1.x drops a Python version tg supports"
  - id: T5  type: transitive_dep_unpatchable    detail: "a transitive dependency of 1.x gains an
            advisory with no fix reachable under the <2 bound"
  - id: T6  type: time_bounded_revalidation     detail: "revalidate_by elapses with no other trigger"
```

## Why PIN_AND_DEFER, not migrate

- **No user demand.** No open issue or dogfood report asks tg to speak MCP 2.0.
- **No security pressure.** No advisory was found against `mcp==1.27.2` or any 1.x release in the
  supported range; the PyPI maintainers themselves say 1.x "continues to receive critical bug
  fixes and security patches." (This record does not manufacture a CVE-based rationale — see
  `W2-b`'s own framing correction for the same point.)
- **The 2.0 API deleted `FastMCP`.** `mcp_server.py` is built on `mcp.server.fastmcp.FastMCP`
  end-to-end (server construction, tool registration, request context). Migrating is a rewrite of
  the transport and handler-registration layer, not a version bump — and per the changelog itself,
  a NEW mandatory RPC (`server/discover`) and the removal of `initialize`/`ping` mean the rewrite
  is unavoidable even for a mechanical port.
- **It would collide with `MCP-SURFACE`.** The MCP tool surface is currently fenced behind Task 2C
  at contract version `1.7.0` (`mcp_server.py:188`). Migrating the transport now, before that gate
  clears, means re-deriving the same surface twice.
- **The upstream-recommended posture is exactly tg's current one.** Pinning `<2` and tracking the
  maintained `1.x` head (item `W2-b`) is precisely what the SDK's own README tells integrators to
  do until they choose to migrate — this decision does not deviate from upstream guidance, it
  matches it and makes the match explicit and reviewable.

## What would reopen this decision

Any of T1–T6 above. T1 and T6 are the two triggers with a mechanical re-derivation (see "Wired
monitoring" below); T2–T5 are human-discovered and are named here specifically so a future
engineer does not have to re-derive from scratch what would justify reopening the question.

## Wired monitoring (T1)

Trigger `T1` (`upstream_maintenance_end`) is wired into the
`tensor-grep-release-drift-check` post-release sweep
(`.claude/skills/tensor-grep-release-drift-check/SKILL.md`), which now re-derives the maintained
`mcp` 1.x head against `https://pypi.org/pypi/mcp/json` on every post-release run and compares it
to both the `pyproject.toml` floor and this record's `revalidate_by` date. See that skill's
"Part 1, step 4" for the exact command and its `MAINTAINED` / `STALE` / `EXPIRED` /
`CANNOT_MEASURE` verdict labels.

**Honest limitation, stated once and not re-argued elsewhere:** this sweep is a maintenance
command (Part 2 of that skill explicitly forbids turning it into a hard pytest — the numbers
drift by design). It observes T1 (PyPI maintenance status) and, by the calendar, T6
(`revalidate_by`). It does **not** observe T2 (client incompatibility) or T3 (Task 2C clearing);
those remain human-discovered, and this record exists so the human who discovers them has the
trigger already named rather than having to reconstruct the reasoning. A mechanism over two of six
triggers is a real improvement over a decision that depends entirely on someone remembering to
look — it is not described here as full coverage, and no future edit to this file should upgrade
that claim without adding an actual T2/T3 mechanism to match it.

## Non-goals

- This record does not change `pyproject.toml` or `uv.lock` (`W2-b`, a separate slice, owns that
  file for its merge window per the collision map in the plan).
- This record does not decide `MCP-SURFACE` / Task 2C.
- This record does not add a hard CI gate keyed to the MCP spec revision string; the SDK-constant
  tripwire approach was rejected in plan revision r1→r2 because the constant is read from an
  *installed* 1.x SDK and would essentially never observe the ecosystem moving past it — see
  `W2.5` in the plan for the full rejection rationale, which this record does not restate.
