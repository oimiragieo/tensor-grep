# Dogfood receipt: subprocess output contracts

Implementation `88f11df70e49442a02476b3c13ed1dc4def784a4` is based on diagnostics
`7c657aabbb5579a93f564388d8fc4096212cc33e`. PR #1217 owns DOGFOOD-DECODING.
The [policy](../subprocess-output-policy.md) and generated inventory classify 72 sinks:
16 explicit text-mode calls and 12 calls in generated Python helpers. The scanner has a
bounded syntax contract and exact callsite exceptions; it does not prove arbitrary Python.

Human diagnostics use replacement decoding. Machine protocols use strict decoding after
byte capture, preventing Windows text-reader exceptions from silently dropping streams.
Path consumers frame bytes before filesystem decoding. Timeout counts retain complete
records only, and explicit NDJSON paths survive queried-directory defaults. Pip-index
parses both output streams, so invalid UTF-8 in either makes its version probe unknown.

Canonical Windows verification used `uv run --no-sync`, explicit worktree source imports,
and external deadlines. Batches are reported separately because they overlap:

| Check | Result |
|---|---|
| Backend, timeout, AST, agent, MCP rewrite and path contracts | 180 passed, 4 skipped |
| Upgrade command decoding and mocks | 55 passed |
| Doctor GPU/version, repair-env and pip-index | 76 passed, 35 deselected |
| Final count parser and evidence receipts | 118 passed, 4 skipped |
| Git receipts, provider setup, runtime paths and native wrappers | 247 passed, 3 skipped |
| Final scanner and mutation controls | 65 passed |
| Whole-repository Ruff / preview format / mypy | Pass; 820 formatted files, 173 typed source files |
| File-size / bare-call ratchets | Pass; no limits raised |
| Final source replay | 23 PASS rows: 6 diagnostics, 17 decoding |

The real five handler-ledger assertions pass over 340 records and 173 modules with pure
AST reads memoized once per module. A line-1 mutation fails the exact locatability check.
This accelerated local invocation is not unmodified pytest clearance. Earlier full-doctor
and ledger attempts exceeded their external deadlines and prove nothing about unrun cases;
the unchanged full CI suite remains required. Native evaluation stays in CI.

[Raw results](evidence/2026-10-05-dogfood/subprocess.json) retain commands, return codes,
outputs, source SHA and copied-extension identity. The source run's installed metadata
version is 1.123.19 and does not identify source behavior. Published 1.123.21 reproduced the
reported failures; fixed-source controls are not a published-release verdict. Independent
Sol review, required Opus clearance, exact-head CI and published replay remain pending.

## Specialist review corrections and integration

Opus found three consumer-boundary regressions and one latent guard gap on `b286e51`.
Implementation `64329758a222cfc3b8e6bbe2e5f2829f738cf7d4` preserves valid strict JSON on
exit 2, so incomplete index-search results remain available; diff previews choose tolerant
decoding and index protocol failures receive structured errors. Windows Git path decoding
or re-encoding failures degrade the codemap census or revision identity to unavailable.
`acb3dfd4f265dc0e6a2f7d0caaefdba58903e126` rejects strict subprocess text capture before
Windows reader threads can lose a stream. The exact prior guard accepted both synthetic
known-protocol and generic sinks; the amended guard rejects both with the intended reason.

Final integrated source is `1f3a442026d450a5b19c7ddcc6c0fc0dff633d99`. It includes reviewed
diagnostics `d96bb9e`; the merge unions both changes and shortens only the sentinel rationale
to keep doctor_report at 1496 lines under its unchanged 1500 limit. Census identity and
consumer-specific policy descriptions were regenerated from this source.

Root verification on the integrated bytes: 102 MCP/diagnostic tests; 28 path/evidence tests
(3 POSIX controls skipped on Windows); 69 scanner/mutation checks; full Ruff, preview format,
mypy (173 source files), file-size and bare-call gates. The actual five memoized ledger
assertions and negative control pass over 340 records/173 modules. A 30-row source replay
passes, including real subprocess transport with nonempty disclosed partial results, tolerant
diff output, malformed JSON errors, Windows Git path refusal and non-object GPU JSON.
The [raw bundle](evidence/2026-10-05-dogfood/subprocess.json) keeps earlier and corrective
runs separately. These source checks are not full CI, specialist clearance or published replay.

The next independent and Opus reviews both identified an implicit-activation twin in the guard:
`encoding="utf-8"` enabled strict text capture without `text=True`. Guard amendment
`4fbb2c425562b482fe9903ba5b70dc16f11f05cb` centralizes all four activation flags in one
predicate used by both validation and classification. Production source stays identical to
`1f3a442`; 82 guard tests pass, including implicit strict refusals and replacement-decoding
positive controls. A separate exact-`6492fb4` control shows four implicit forms accepted before
and refused after for the intended reason. The 72/16/12 census and committed inventory still
match exactly. Earlier 69-test clearance did not cover this gap; final review is required.

The following Opus review cleared that amendment but identified positional options as a
remaining guard gap. Guard `1049b0a863276736043e0199759ed7cafecf833f` rejects extra positional
arguments and top-level `*args` before output policy or exact forwarding exceptions. Ordinary
command arguments and `args=` stay supported. The summary uses the shared decoding predicate.
All 94 guard tests pass; aliases, wrappers, generated helpers and exact-exception mutations
are covered. An exact-`87f7e68` control demonstrates two old acceptances and their intended
new refusals. The census remains 72 sinks / 16 decoding calls / 12 generated calls; the full
inventory adds an explicit positional-options field without changing call identities.
Changed-file Ruff and preview formatting pass. Production `src/` is unchanged from `1f3a442`.
These results supersede the 82-test guard result only; exact-head reviews and CI remain required.

## Replay onto merged diagnostics

PR #1214 merged as `d0d9f7e960622f868a4a41c14c8d21a6e81ac1c8`. Rebased subprocess source
`a347e47eeb60e5bdafc8dc4fe1a2aca832d9fba6` replays only the reviewed dependency-to-candidate
delta onto that merge. Its complete Git tree equals the Sol/Opus-cleared `06e7fd4` tree.
All repeated canonical-venv gates record this exact head and a clean starting worktree: 94
guard tests; 102 MCP/diagnostic tests; 28 path tests (3 POSIX skips); full Ruff, preview format,
mypy (173 files), size/bare ratchets; 30 diagnostic/decoding source replay rows. The first
guard attempt reported passing assertions but exceeded its 120-second process deadline, so
it supplied no clearance. The completed retry used an external 240-second deadline. Final
metadata review and CI must name the rebased head; publication/replay remain separate.

## CI entrypoint and consumer test corrections

Run `37423447650` on `7fab9f21615d7aace70786bff99a297a35c36544` exposed a collection
error: the console `pytest` entrypoint does not add the repository root needed by a
`scripts` namespace import. The guard test now loads its exact script path using the
existing file-size-test import pattern. All 94 guard assertions pass through the actual
console entrypoint. The same run found an older GPU test asserting text capture; a sibling
PCRE2 test had the same stale expectation. Both now supply bytes and assert byte capture.
Unicode JSON survives the consumer boundary; malformed successful PCRE2 output produces
the exact UTF-8 refusal and exit 1. Existing malformed GPU JSON controls remain intact.

Test-only commit `14f85effc9cb2660141902512d16e4838e64bc68` leaves production, scripts,
policy inventory and exceptions unchanged. Bounded canonical checks pass: 94 console guard
tests, 159 capsule/navigation/session/probe tests, three PCRE2 tests, focused Ruff/preview
formatting and the unchanged file-size gate. The raw bundle records dirty starting trees
and identifies this final test commit; these checks do not clear the new head's full CI.

## Interpreter-independent fingerprint correction

The next Opus review returned FIX-FIRST on `80cc210`: `ast.dump` formatted the same AST
differently across interpreters. Exact-source stdlib controls returned 22 guard violations
on Python 3.11 and 3.13, while canonical 3.12 was clear. Pending CI `37425139246` was
cancelled because this newly exposed defect required another amendment; it gives no clearance.

The independently approved [portability plan](plans/2026-10-06-subprocess-fingerprint-portability.md)
has SHA-256 `59fdee74edb15a38a1bde3d08ddf65d1c9532a31ccce044687d79b223f02283e`
over canonical worktree bytes. Implementation `cfbda04a373583492e62d659f05a6b1e9d72e1d0`
uses sorted present AST fields, omitting only empty type parameters introduced in 3.12.
Nonempty type parameters, ordinary empty lists, `None`, list order and values remain significant.
All 72 calls were paired one-to-one, including the two parent launches of 12 generated calls.
The same seven exact forwarding exceptions and all operations/options/policies/rationales
were preserved. Only fingerprint representations and their derived identities changed.

Python 3.11.14, canonical 3.12.12 and 3.13.5 now produce identical full inventories and exact
exception records, with zero violations and shared normalized JSON SHA-256
`9c32f9c63e22c83866b51f12f000fd323c71d4088850112275336e3595481d6c`.
The raw bundle states the hash method and retains each interpreter's complete output.
All 97 canonical console guard tests, focused Ruff/preview and unchanged size gate pass.
Production `src/` remains unchanged. Fresh final-head independent/Opus review and CI are
required; no test skip, new exception or size-pin increase was introduced.
