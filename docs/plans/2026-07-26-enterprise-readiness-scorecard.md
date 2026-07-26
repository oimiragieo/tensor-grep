# tg enterprise-readiness scorecard

Status: DRAFT (awaiting thinktank review)
Author: backlog-steward session, 2026-07-26 (verified against v1.98.25)
Goal: #292 / #249 — convert "is tg enterprise ready?" from an opinion into a list we pass or fail

## Why this document exists

The repo records `world_class_readiness = not_claimed`. That is honest but unfalsifiable — there was
no list to pass. This is the list. Every row is **objectively checkable**, and every status below was
verified by reading the tree on 2026-07-26, not recalled.

Legend: **PASS** (verified) · **GAP** (verified absent) · **PARTIAL** · **UNVERIFIED** (not checked
this pass — do not report as either).

## A. Security / supply chain — mostly PASS

This leg is stronger than the CEO answer implied, and the answer under-sold it.

| # | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| A1 | SBOM per release, machine-readable | **PASS** | CycloneDX for **both** toolchains — `cargo cyclonedx` (`release.yml:240-248`) and `cyclonedx-py environment` (`release.yml:250-253`) |
| A2 | Build provenance (SLSA-style) | **PASS** | `actions/attest-build-provenance@v4` (`release.yml:264`), `attestations: write` (`:197`) |
| A3 | Artifacts cryptographically signed | **PASS** | `sigstore/gh-action-sigstore-python@v3.0.0` (`release.yml:256`), covering `sbom-*.json` |
| A4 | Published vulnerability-disclosure policy | **PASS** | `SECURITY.md` at repo root |
| A5 | CI actions pinned to SHA | **PASS** | Verified across `release.yml` and `trust-benchmark.yml` (the latter pinned during the 2026-07-26 drain) |
| A6 | No shell-string subprocess construction; `--` sentinel before user positionals | **PASS** | The CWE-88 / MCP-276 class is a standing sweep target in `AGENTS.md`; `--` sentinel work landed via #140/#143 |
| A7 | Reproducible / byte-identical builds | **UNVERIFIED** | Not checked this pass. Do not claim. |
| A8 | Per-dependency license obligation review | **UNVERIFIED** | SBOM exists, so the input is there; whether obligations are *reviewed* is unchecked |

## B. Output contract & honesty — the leg that decides it

This is where an agent-consumed tool lives or dies, and where tg's remaining work is.

| # | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| B1 | No silent partial results — Python routes | **PASS** | `result_incomplete` + `incomplete_reason_class` (`json_fmt.py:126-140`, `ripgrep_backend.py:144-150`) |
| B2 | No silent partial results — **native `--json`/`--ndjson`** | **GAP** | The envelope reports success while walk errors go only to stderr. The code documents its own gap at `native_search.rs:1642-1649`. **This is the single blocking item.** (#276) |
| B3 | Closed-vocabulary reason enum, not free text | **PASS** | `unreadable_path` \| `timeout` \| `deadline` \| `scan_limit`; documented + ratcheted (#293) |
| B4 | Stable, documented exit-code contract | **PASS** | exit 2 = incomplete, pinned by governance tests |
| B5 | Silent-loss regression ratchet | **PASS** | `tests/unit/test_silent_loss_census_ratchet.py` — per-file counts may fall, never rise, plus a no-new-files arm |
| B6 | Deterministic output as a **tested** invariant | **PARTIAL** | Golden/parity tests exist; a first-class "same query + same tree ⇒ byte-identical" contract test is not established as such |
| B7 | **SARIF v2.1.0 output mode** for scan/audit commands | **GAP** | Verified absent — no SARIF anywhere in `src/`. This is what lets results compose with GitHub code scanning, CodeQL and Semgrep instead of needing bespoke ingestion |
| B8 | Evidence receipt aligned to a standard | **PARTIAL** | `EvidenceReceipt` exists (#124) but is a bespoke schema; the standard to map onto is in-toto Statement/Predicate + DSSE |

## C. Operational

| # | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| C1 | SemVer + automated release | **PASS** | semantic-release in `ci.yml` |
| C2 | Published deprecation window | **UNVERIFIED** | Industry range is 4–12 months; no policy doc located this pass |
| C3 | Published support/EOL window | **UNVERIFIED** | Not located |
| C4 | Air-gapped operation, no mandatory phone-home | **PASS (with note)** | OpenTelemetry is import-guarded and only calls `get_tracer` (`main.py:7933-7940`) — no exporter configured, so it is a no-op unless an operator sets OTLP env vars. Dense models require a one-time `tg install-dense`, which is explicit and never automatic |
| C5 | Telemetry default-OFF | **PASS** | Same evidence as C4 — nothing is emitted by default |
| C6 | SOC 2 / ISO 27001 | **N/A — do not claim** | There is no hosted component. Claiming it would be over-scoping; honestly stating N/A is stronger than a checkbox |

## D. Agent-specific — the bar that actually matters

Ranked by what breaks worst when the consumer is autonomous rather than human.

| # | Requirement | Status | Notes |
| --- | --- | --- | --- |
| D1 | **Honest failure signalling / fail-closed on uncertainty** | **PARTIAL — B2 is the hole** | The empirical case is stark: in one benchmarked domain **78% of agent failures were silent wrong-state with no tool error at all**. A human skims and catches a wrong-looking number; an agent propagates it as ground truth |
| D2 | Determinism | **PARTIAL** | See B6. Agents re-issue the same query mid-plan; non-determinism makes them unable to tell "the code changed" from "the tool is noisy" |
| D3 | Non-poisonable tool interface (MCP) | **PARTIAL** | tg has MCP path confinement and a contract version. Whether the tool catalog is pinned/hashed against a post-approval "rug pull" is **UNVERIFIED** |
| D4 | Argv-injection safety | **PASS** | Standing sweep target; the agent-specific twist is that a prompt-injected model can influence argv without any shell access |
| D5 | Idempotent / safe-retry on mutating calls | **PARTIAL** | Small mutating surface (index writes, ledger claims, checkpoints). Checkpoint undo was hardened this cycle (#297, #298) |

## The verdict, as a sentence we can defend

> tg passes the supply-chain leg outright, passes the operational leg apart from two unwritten
> policies, and has **one blocking hole in the leg that matters most**: the native `--json` envelope
> can still report success on an incomplete answer (#276).

**Minimum set to drop `not_claimed` honestly:** close **B2** (#276), close **B7** (SARIF), establish
**B6** (determinism as a named, gated invariant), and write **C2/C3** (two short policy docs). Nothing
on that list is research-hard; B2 is the only one that is subtle.

## The differentiator, stated honestly

Most of the above is **table stakes** — a well-built tool has it and nobody buys because of it.

The genuine differentiator is B2 + D1, and the reason is specific: **ripgrep has this exact defect
and its maintainer has explicitly declined to fix it**, calling per-error classification in `--json`
scope creep ([ripgrep#2861](https://github.com/BurntSushi/ripgrep/issues/2861)). Its JSON schema has
no error field at all; completeness is signalled *solely* by exit code — invisible to any consumer
piping into `jq`.

So this is not "catch up to the competition." It is the one place tg can lead rather than tie, and it
is directly the answer to #307. **A partial-result signal that has been proven to fire is table
stakes in principle and rare in practice, because almost nobody tests the negative case.** tg's
bidirectional-oracle discipline is exactly the machinery that makes it provable.

What is **not** a real differentiator, said plainly: SOC 2 for a local CLI (no service to attest);
an agent-certification badge aimed at conversational agents; and raw speed or GPU claims — this repo
already closed both as net-negative or at parity, and re-litigating them would be re-chasing settled
physics.
