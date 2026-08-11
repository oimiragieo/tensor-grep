# Doctor "scream" — installation health + pypi_latest (world-class trust, PATH honesty)

> **For agentic workers:** `test-driven-development` + `tensor-grep-change-control` +
> `tensor-grep-diagnostics-and-tooling`. One PR, surgical commits, never `git add .`.

## Goal

`tg doctor --json` must make the resolved-binary-vs-expected-wheel mismatch **unmissable** (the
0.32.0 PATH-shadow the v1.110.13 dogfood still bites on) AND expose whether the installed wheel is
behind the current PyPI release. Today doctor has per-route `*_is_foreign` /
`*_version_matches` / `*_foreign_warning` booleans scattered across ~15 fields, but:
- no `pypi_latest` (whether the WHEEL itself is stale),
- no consolidated `shadow_launchers[]`,
- no aggregate top-level health signal that screams.

## Spec (council MUST adjudicate)

Add to the `tg doctor --json` envelope (schema bump `_DOCTOR_SCHEMA_VERSION` 2 → 3):

1. **`pypi_latest`** (str | null) — best-effort, bounded: reuse `_latest_pypi_tensor_grep_version()`
   (already exists, used by upgrade; 15s timeout, network-fault → None, never crashes doctor).
2. **`installed_behind_pypi`** (bool | null) — `True` when `pypi_latest is not None` AND
   `version < pypi_latest`; `False` when equal/newer; `None` when pypi_latest is None (probe failed —
   do NOT claim "not behind" on a failed probe; fail open with null, never a confident false).
3. **`shadow_launchers`** (list[obj]) — consolidate the routes where the resolved `tg` is foreign
   OR its version does not match the installed wheel OR its version is invalid (unverifiable).
   Each entry `{route, path, version (raw), kind, foreign: bool, version_matches: bool | null}`
   where `version_matches` is `None` iff that PRESENT route's version is unparseable (codex REV-4
   must-fix 1 — the null contract and inclusion rule are the SAME predicate: a route is listed iff
   `foreign OR version_matches is False OR version_matches is None`; a route that resolves to the
   expected version is NOT listed). Absent route ≠ unparseable (absent simply not listed).
   MECHANICALLY derived from the already-computed per-route booleans/versions — no new probing.
   Deterministic route order: path, fresh_shell_path, python_subprocess_path.
4. **`installation_health`** (str) — aggregate `"ok"` | `"foreign_launcher"` |
   `"unverifiable_version"` | `"launcher_version_mismatch"` | `"stale_install"` | `"unknown_pypi"`.
   Precedence (codex must-fix 1/2/3 + REV-2 must-fix; no unreachable states, version-mismatch
   AFFECTS health, and an UNVERIFIABLE version can NEVER fall through to ok):
   1. any route foreign → `foreign_launcher` (most dangerous: could dogfood a different product's
      `tg`);
   2. else ANY of {installed, pypi_latest (non-null but invalid), a present-route} version is
      invalid/unparseable → `unverifiable_version` (we cannot certify health without a comparable
      version — NEVER `ok`; this explicitly covers an invalid non-null `pypi_latest`, codex REV-4
      must-fix 2);
   3. else any route version_mismatch → `launcher_version_mismatch` (a shadowed OLD `tg` must
      never read as ok — it appears in shadow_launchers, so it MUST move health off ok);
   4. else installed_behind_pypi == True → `stale_install`;
   5. else pypi_latest is None → `unknown_pypi` (probe failed, NOT claimed clean);
   6. else `ok`.
   (The previously-proposed `foreign_launcher_and_stale` was unreachable under "foreign wins";
   dropped — the foreign value already implies the mismatch is dangerous. Codex must-fix 1.)
5. **Semantic version comparison (codex must-fix 3 + REV-3/4/5):** the version comparison is
   SEMANTIC (never naive string compare) via a strict dotted-numeric PADDED tuple parser
   (`1.0 == 1.0.0`; `1.110.9 < 1.110.10` numerically):
   - **PEP-440-PREFIX / PLAN REV 6 AMENDMENT (codex re-audit MEDIUM):** any prerelease / local
     / epoch suffix (`1.110.13rc1`, `1.110.13.dev0`, `1.110.13+local`, `1!2.0.0`) is REJECTED
     and treated as **UNVERIFIABLE (None)** — never silently truncated into a stable-looking
     tuple, never PEP-440-compared. RATIONALE: tensor-grep's own release line (semantic-release)
     emits only clean `X.Y.Z`; a prerelease/local result is DISCLOSED as `unverifiable_version`,
     not claimed clean — fail-closed by design. This is a deliberate, council-noted deviation
     from full PEP 440 (which would require declaring `packaging` as a dependency; it is only a
     transitive today and declaring it churns ~260 uv.lock lines). Reopen if a consumer
     genuinely needs prerelease-aware comparisons.
   - `installed_behind_pypi` is `None` when PyPI is unavailable OR installed/pypi version is
     invalid; `True` when semantic `installed < pypi_latest`; `False` when equal/newer. It is
     computed INDEPENDENTLY of the route versions: a junk ROUTE version (some shadow's unparseable
     output) must not nullify the installed-vs-pypi comparison — only invalid/unavailable
     installed or PyPI versions make it null (codex REV-6).
   - `shadow_launchers[].version_matches` is `None` when that PRESENT route reports an invalid
     version; `bool` otherwise (semantic padded compare, never the substring matcher).
   - An ABSENT route is NOT "unparseable" — filtered before the inclusion predicate.
   - Precedence preserved: foreign wins; otherwise any invalid INSTALLED, pypi_latest, or
     present-route version yields `unverifiable_version`; never falls to `ok`.

Open questions (ruled by REV-2 council):
- (a) YES — the human (non-JSON) `tg doctor` prints ONE prominent, stable warning/error line when
  health != "ok" (health code + concise remediation); JSON stdout stays pure (human line never
  affects `--json`).
- (b) NO explicit cap — there are exactly 3 enumerated routes, natural max 3; a defensive `[:5]`
  keeps determinism without truncation semantics.
- (c) extra tests: invalid installed version + valid PyPI; invalid route version proving health
  cannot be `ok`; prerelease/epoch/local-version behavior (packaging semantics); all-3-routes
  deterministic order; human output quiet when health == ok.

## Files

- Modify: `src/tensor_grep/cli/main.py` (`_DOCTOR_SCHEMA_VERSION` 2→3, doctor JSON builder, new
  helper `_doctor_shadow_launchers(...)` + `_doctor_installation_health(...)` + human-line emit),
- Modify: `tests/unit/test_cli_modes.py` (RED tests for the new fields), plus a doctor-test for the
  human scream line.
- No command/flag registration change (doctor is existing; fields are additive, schema bump).

## TDD

1. RED unit: monkeypatch `_doctor_installed_version`=9.9.9, `_latest_pypi_tensor_grep_version`
   →10.0.0, `path_tg_first_version_matches`=False, `path_tg_first_is_foreign`=False → assert
   `installation_health=="launcher_version_mismatch"` (version mismatch affects health),
   `shadow_launchers` non-empty.
2. RED unit: `path_tg_first_is_foreign`=True → `"foreign_launcher"` (priority over mismatch/stale).
3. RED unit: clean routes, pypi_latest=10.0.0, installed=9.9.9 → `"stale_install"`,
   `installed_behind_pypi is True`.
4. RED unit: probe → None, clean routes → `"unknown_pypi"`, `installed_behind_pypi is None`.
5. RED unit: semantic — a route reporting a junk version (unparseable) → `unverifiable_version`
   health (never `ok`), AND JSON asserts `installed_behind_pypi is None` / `version_matches is
   None` on the affected signal (nulls, not just the aggregate). And `1.110.10 < 1.110.12` is True
   via packaging (not lexicographic). Also: invalid INSTALLED version + valid PyPI → `None` +
   `unverifiable_version`.
6. RED unit: all-3-routes present → shadow_launchers deterministic order; human output quiet when
   health == ok; human line present (health code + remediation) when != ok without touching JSON;
   absent route does not appear as "unparseable" (it is simply not in shadow_launchers).
6. GREEN: implement helpers (semantic compare + aggregate + shadow list + human line) + builder +
   schema bump 3.
7. Gate: ruff/format/mypy + targeted `tests/unit/test_cli_modes.py::test_doctor*`.
8. Codex adversarial audit (JSON-schema / fail-open honesty / probe-cost / precedence / semantic).
9. Draft PR `fix(doctor): surface pypi_latest + foreign shadow launchers (PATH honesty)` → drain.