# A90 — Fail closed on unknown top-level commands (world-class #1 trust killer) — REV 4 (council-amended)

> **For agentic workers:** REQUIRED SUB-SKILL: `test-driven-development` + `executing-plans` +
> `tensor-grep-change-control` + `tensor-grep-config-and-flags`. One PR; never `git add .`.

## Goal

`tg edit-ready --help` and `tg verify-edit --help` (commands that do not exist yet) must exit 2
with `error.code=unknown_command` + `nearest[]` on BOTH front doors — never show fake search/root
help with exit 0. Bare `tg PATTERN` / `tg PATTERN PATH` / `tg PATTERN --flag` stays search (the
core front door, empirically legal today) and is pin-protected.

## Baseline (premise-checked on published 1.110.12 + managed native 1.110.12 + origin/main `4fd9b9e`)

| invocation | native today | python (wheel) today | verdict |
|---|---|---|---|
| `tg hello` (bare pattern) | search exit 0 | search exit 0 | MUST STAY |
| `tg hello --json` (pattern+flag) | search exit 0 | search exit 0 | MUST STAY (legal) |
| `tg edit-ready --help` | ROOT help exit 0 | SEARCH help exit 0 | BROKEN (A90) |
| `tg edit-ready --json` | SEARCH (query=edit-ready) exit 1 (0 matches) | SEARCH JSON exit 0 | BROKEN (A90) |
| `tg edit-ready` (bare) | search pattern | search pattern | AMBIGUOUS — stays search |

Key empirical fact: **pattern+flag searches are legal today (`tg hello --json` works)** — so a
flag-only discriminator CANNOT distinguish `tg edit-ready --json` from `tg hello --json`. Both are
equally valid searches. This forces the design choice below.

## Spec (council MUST-FIX items 1-7 + agy additions — all folded)

**ARM A (recommended): RESERVED-COMMAND REGISTRY + `--help` refusal.**

- New module-level constant `RESERVED_TOP_LEVEL_COMMANDS` (Python `commands.py`) + native twin
  (parse the same literal, mirroring `is_known_python_command`'s existing include_str pattern):
  `{"edit-ready", "verify-edit", "workspace"}` — documented as "roadmap commands, not yet
  registered; must never be treated as search when command-shaped, nor claimed to exist".
- Refusal predicate (BOTH doors, identical): `argv[0] ∉ KNOWN_COMMANDS` AND `argv[0] ∈
  RESERVED_TOP_LEVEL_COMMANDS` AND `any(token.startswith("-") for token in argv[1:])` → exit 2,
  `error.code=unknown_command`, `nearest[]` = thresholded suggestions from KNOWN_COMMANDS
  (max edit-distance 3, filter `__`-internal + reserved names, cap 5, deterministic tie-break,
  `[]` when nothing within threshold).
- `--help`/`-h` anywhere after an unknown (reserved OR not-reserved) first arg → same refusal
  (a nonexistent command has no help; this is the world-class repro shape exactly).
- **Explicit compatibility decision (codex MUST-FIX 3):** `tg <reserved> <positional>` (no flags)
  stays search — indistinguishable from a pattern+path, and the reserved names are legitimate
  search terms (e.g. searching docs for "edit-ready" must keep working). Flag-bearing reserved
  invocations are the refusal surface. Documented in the PR body + `docs/routing_policy.md`.
  `tg edit-ready --json` therefore refuses (it is flag-bearing) while `tg edit-ready docs/` searches.
- Single semantic result per door (codex MUST-FIX 7): the normalizer returns an enum result
  (`Search` | `KnownCommand` | `UnknownCommandRefusal { nearest }`); the front-door CALLER renders.
  **Output split (codex REV-2 MUST-FIX 1):**
  - `--help`/`-h` refusal: stdout EMPTY, stderr = stable human diagnostic
    `error: unknown command 'X' (did you mean ...?)`, exit 2. NO structured fields asserted from
    the help shape.
  - `--json` (or other flag) refusal: stdout EMPTY, stderr = exactly ONE JSON object
    `{"error": {"code": "unknown_command", "nearest": [...]}}`, exit 2.
  - Precedence when BOTH `--help` and `--json` present: `--help` wins (help is a human surface;
    document + pin).
- `nearest[]` (codex MUST-FIX 5 + agy): normalized (lowercase), max distance 3, excludes
  `__`-prefixed internal commands, cap 5, stable order, `[]` when nothing close. Pins: `searhc ->
  ['search']`; a distant token -> `[]`; deterministic across runs.
- **Registry lifecycle (codex REV-2 MUST-FIX 3 + REV-3 MUST-FIX):**
  `RESERVED_TOP_LEVEL_COMMANDS` (authoritative in `commands.py`, owner = the roadmap/change-control
  line) is mechanically bounded:
  - INVARIANT (gate test): `RESERVED ∩ KNOWN_COMMANDS == ∅` — a name cannot be both reserved and
    registered.
  - TRANSITION RULE (gate test): when a command is registered (added to KNOWN_COMMANDS + the 4
    sites), its reserved entry is REMOVED IN THE SAME PR — the gate fails if a KNOWN_COMMANDS
    addition leaves a matching reserved entry behind.
  - ROADMAP RULE: adding a roadmap command name to the reserved set is a docs/commands.py change
    with a comment citing the roadmap row; the census test asserts the set is non-empty and every
    member is either roadmap-documented or commented.
  - **SCOPED NATIVE PARSER (codex REV-3 MUST-FIX — VERIFIED):** the existing native
    `is_known_python_command` (`main.rs:7826-7834`) matches ANY quoted literal line in
    `commands.py` — it is NOT scoped to the `KNOWN_COMMANDS` set block, so simply adding reserved
    names to that file would make them appear KNOWN natively and the `not-known AND reserved`
    predicate could never fire. The native side MUST extract the two sets INDEPENDENTLY-SCOPED:
    a parser that reads the literal block bounded by `KNOWN_COMMANDS = {` ... `}` and separately
    `RESERVED_TOP_LEVEL_COMMANDS = {` ... `}` (brace-depth aware, comment-insensitive), producing
    `is_known_python_command()` AND `is_reserved_python_command()` from the correct block. Add a
    native unit pin proving every reserved name is `reserved == true` and `known == false`, AND
    that a known name (e.g. `orient`) is `known == true` and `reserved == false` (both directions —
    the unscoped parser's exact failure is a reserved name leaking into known).

**ARM B (fallback if council prefers minimal):** only `--help`/`-h` after unknown first arg
refuses; `--json`/other flags stay search for unknown+unreserved. Smaller surface, but leaves
`tg edit-ready --json` fake-searching (the roadmap's own example) — weaker.

Council recommendation sought: ARM A vs ARM B. Plan defaults to ARM A.

## TDD steps

1. RED Python unit (`tests/unit/test_cli_bootstrap.py`): `unknown_help_and_flag_bearing_top_level_command_is_refused_not_searched` — `["edit-ready", "--help"]` and `["edit-ready", "--json"]` → refusal enum/exit-2; FAILS today (returns search args).
2. RED native unit (`rust_core/src/main.rs`): `unknown_top_level_command_with_help_or_flag_refuses` — same two shapes → refusal branch; FAILS pre-fix.
3. RED e2e parity (`tests/e2e/test_routing_parity.py`) — FULL two-door matrix (codex REV-2
   MUST-FIX 2), each row asserted IDENTICALLY on Python + native:
   - `edit-ready --help` → exit 2, empty stdout, stderr human diagnostic (no structured assert);
   - `edit-ready --json` → exit 2, empty stdout, ONE stderr JSON `unknown_command` + `nearest`;
   - `edit-ready --help --json` → same as `--help` (precedence pin);
   - `hello --json` (unreserved pattern+flag) → SEARCH (exit 0), both doors;
   - `hello path --json` (unreserved pattern+path+flag) → SEARCH, both doors;
   - `edit-ready docs/ --json` (reserved + positional + flag) → REFUSE (unknown_command), both
     doors;
   - `qqqzzz --json` (unreserved unknown + flag) → SEARCH (it is NOT reserved — flag-bearing
     unreserved unknowns are legitimate pattern+flag searches; only RESERVED names refuse);
   - `edit-ready docs/` (reserved + positional, no flag) → SEARCH (compat decision),
     both doors.
4. GREEN: implement ARM A predicate + enum result in both doors (bootstrap.py + main.rs).
5. PIN bare-search regression (BOTH doors): `tg hello`, `tg hello --json`, `tg hello path`, and
   `tg edit-ready docs/` all still SEARCH (exit 0/1 by match, never exit 2 with unknown_command).
6. PIN nearest: `searhc --help` → `["search"]`; `qqqqzzzz --help` → `[]`; deterministic repeat.
7. PIN registry lifecycle (codex REV-2 MUST-FIX 3): `RESERVED ∩ KNOWN_COMMANDS == ∅` always; the
   test asserts every reserved name is roadmap-documented/commented and the set is non-empty;
   registration-census guard unchanged (4-site + 2-door + parity tests still pass — no new command
   registered). PLUS the escape-hatch pin: `tg search workspace --json` (explicit-command form)
   still searches, both doors (codex REV-2 MUST-FIX 3 escape).
8. Gate: ruff check / format --preview / mypy / targeted suites; rustfmt local; cargo matrix in CI.
9. Codex adversarial audit (dispatch surface = registration class; cite file:line).
10. Draft PR `fix(cli): fail closed on unknown flag-bearing top-level commands (A90)` → drain one-per-publish → dogfood `uvx --from tensor-grep==<new>` (`tg edit-ready --help` → exit 2 unknown_command) + native twin.

## Files

- Modify: `src/tensor_grep/cli/commands.py` (RESERVED set), `src/tensor_grep/cli/bootstrap.py`
  (Python door enum + refusal), `rust_core/src/main.rs` (native door enum + refusal + tests),
  `docs/routing_policy.md` (compatibility decision documented)
- Modify: `tests/unit/test_cli_bootstrap.py`, `tests/e2e/test_routing_parity.py`
- No KNOWN_COMMANDS / _TG_ONLY_SEARCH_FLAGS / PUBLIC_TOP_LEVEL_COMMANDS registration change (the fix
  READS the sets; the reserved names are explicitly NOT registered as commands — that's the point).

