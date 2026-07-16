# Tensor-Grep Reference

> Argument order is **path-first**: `tg <command> <REPO_PATH> <SYMBOL>`.
> If you reverse them (`<SYMBOL> <REPO_PATH>`) tensor-grep auto-corrects and
> prints a hint, but write path-first to avoid the extra round trip. A bare
> `tg <command> <SYMBOL>` resolves the symbol against the current directory.

## Core Commands

```powershell
tg --version
tg source REPO_PATH SYMBOL
tg defs REPO_PATH SYMBOL
tg refs REPO_PATH SYMBOL
tg callers REPO_PATH SYMBOL
tg blast-radius REPO_PATH SYMBOL
tg blast-radius-plan REPO_PATH SYMBOL
tg blast-radius-render REPO_PATH SYMBOL
tg imports FILE
tg importers FILE [ROOT]
tg evidence emit REPO_PATH --capsule capsule.json --query "task" --json
tg codemap REPO_PATH --out /tmp/code-map --json
tg session open REPO_PATH
tg search PATTERN PATH
tg search PATTERN PATH --rank
tg find "natural language query" PATH
tg orient REPO_PATH
```

## Useful Variants

```powershell
tg source REPO_PATH SYMBOL --json
tg defs REPO_PATH SYMBOL --provider native --json
tg refs REPO_PATH SYMBOL --provider lsp --json
tg blast-radius REPO_PATH SYMBOL --provider hybrid --json
tg blast-radius-plan REPO_PATH SYMBOL --provider native --json
tg callers REPO_PATH SYMBOL --json          # check result_incomplete: true = truncated list
tg blast-radius REPO_PATH SYMBOL --json     # same result_incomplete contract
tg search PATTERN PATH --rank
tg search PATTERN PATH --rank --json
tg orient REPO_PATH
tg orient REPO_PATH --json
tg orient REPO_PATH --max-tokens 6000 --max-central-files 15
```

When `result_incomplete` is `true`, the scan hit a cap and the call-site list is partial — do not treat a truncated zero-caller result as dead code. A clean zero-caller result is also not proof of dead code: the call graph cannot see set/decorator/dispatch-table registrations.

## Practical Sequence

```powershell
tg source C:\repo open_file
tg blast-radius C:\repo open_file
tg blast-radius-plan C:\repo open_file
```

Use the top-ranked file/span first. Only broaden to refs/callers if the primary file is still ambiguous.

## Orient-First Sequence (unfamiliar repo)

```powershell
tg orient C:\repo
tg source C:\repo <symbol-from-orient-output>
tg blast-radius C:\repo <symbol-from-orient-output>
```

Use `tg orient` when you do not yet know which files or symbols matter. The capsule gives you central files (import in-degree), entry points, and a symbol map — pick the right symbol, then proceed with source/blast-radius.

## Search-Then-Source Sequence (unknown symbol name)

```powershell
tg search "pattern" C:\repo --rank
tg source C:\repo <symbol-from-top-hit>
```

Use when the symbol name is unknown but the concept or text is known. `--rank` (alias `--bm25`) re-ranks ripgrep hits by BM25 content relevance — pure Python, no API key, no GPU.

## Whole-Repo Semantic Search (no pattern needed)

```powershell
tg find "verify login tokens" C:\repo
tg find "verify login tokens" C:\repo --json
tg find "verify login tokens" C:\repo --limit 20 --max-tokens 8000
```

`tg find` (experimental) is for when you cannot predict a matching keyword or regex at all — unlike `tg search --rank` (which re-ranks an EXISTING regex match set), it walks and ranks the WHOLE repo via BM25 + local CPU dense-embedding relevance, no pattern pre-filter. No API key, no GPU. `rank_fallback_reason` present in JSON means the dense leg degraded to BM25-only (extra/model absent) — still a legitimate, fully supported result. `result_incomplete: true` + exit 2 means the repo walk or ranking corpus was truncated (`--max-repo-files` cap, `--deadline` cutoff, or an internal chunk cap) — treat the result as partial, not the full answer; widen `--max-repo-files`/`--deadline` and retry. Exit 1 means a complete scan found no ranked matches. Does not offer `--format rg`.

## Session Memory

`tg session` caches the repo-map so repeated context-render, edit-plan, and blast-radius calls do not re-index from scratch. Real subcommands (from `tg session --help`):

```powershell
tg session open REPO_PATH            # creates the session; run with --json to get the session_id, then pass it below
tg session list                      # list cached sessions for the current root (no SESSION_ID)
tg session show SESSION_ID           # show the cached repo-map payload
tg session refresh SESSION_ID        # refresh after file changes
tg session context SESSION_ID [PATH] [QUERY]              # context pack from the cached session
tg session context-render SESSION_ID [PATH] [QUERY]       # prompt-ready render bundle from cache
tg session edit-plan SESSION_ID [PATH] [QUERY]            # cached edit-planning bundle (2nd positional is query text)
tg session blast-radius SESSION_ID [PATH] [SYMBOL]        # cached-session blast radius
tg session blast-radius-render SESSION_ID [PATH] [SYMBOL] # prompt-ready cached blast radius
tg session blast-radius-plan SESSION_ID [PATH] [SYMBOL]   # cached blast-radius planning bundle
tg session serve SESSION_ID [PATH]   # serve repeated requests from a single session
tg session daemon start|status|stop  # manage the warm localhost session daemon (sub-group; needs a subcommand)
```

Use the session-scoped variants (`tg session context-render`, `tg session edit-plan`, `tg session blast-radius-render`) in place of the top-level equivalents when working in a repeated-edit loop across invocations. The `session_id` comes from `tg session open --json` and is the required first argument for every subcommand except `open`/`list`/`daemon`. Refresh with `tg session refresh SESSION_ID` after non-trivial file changes.

## Evidence Receipts (governance / audit trail)

`tg evidence emit` aggregates what tg already computed (repo revision identity, blast-radius, validation outcomes, changed files/rollback, caller-supplied agent/model/cost) into one versioned JSON receipt, for a downstream consumer (e.g. gotcontext) to audit an agent's work. Every receipt gets a keyless `receipt_sha256` integrity digest; `--sign` additionally Ed25519-signs it so a separate trust domain can verify it without holding a forgeable key.

```powershell
tg evidence emit REPO_PATH --capsule capsule.json --manifest manifest.json --out receipt.json   # unsigned; receipt_sha256 always present
tg evidence emit REPO_PATH --sign --out receipt.json            # Ed25519-signed; fails closed (non-zero, no file written) if no key resolves
tg evidence keygen                                               # generate ~/.tensor-grep/keys/evidence_ed25519.key (0600) + .pub; --force to overwrite
tg evidence pubkey                                                # print the public key + key_id for registering with a verifier
tg evidence verify receipt.json --json                           # digest_valid / signature_valid / key_trusted / valid
tg evidence verify receipt.json --trusted-key BASE64_PUBKEY --require-trusted   # fail closed unless the embedded key is pinned
```

An embedded public key alone only proves the receipt is internally self-consistent, never who signed it — pin the signer's key with `--trusted-key` (or `TG_EVIDENCE_TRUSTED_KEYS`) and add `--require-trusted` before trusting `valid=true` for anything security-relevant. Full wire format: `docs/CONTRACTS.md` section 8.

## Known Issues

**Unscoped search on a vendored root refuses instantly, not a 60 s hang.** `tg search PATTERN` with no path against a root whose top level contains `node_modules`/`vendor`/`external_repos`/`third_party` is refused in under 1 s (exit 2) before any walk starts. A large/unscoped root with no such top-level dir still gets a wall-clock-bounded native walk (flagged partial on expiry) or the `TG_RG_TIMEOUT_SECONDS`-bounded rg passthrough (default 60 s, lowered from 600 s in #288) — the 60 s timeout is a backstop, not the primary behavior. WORKAROUND: always supply a path — `tg search PATTERN C:\repo` completes in ~0.4 s.
