---
name: tensor-grep-enterprise-review-bundle
description: Use when packaging tensor-grep outputs for enterprise change review — review-bundle create/verify requires a rewrite audit --manifest (not a capsule); optionally attach scan JSON and checkpoint IDs; pair with evidence emit and audit-history.
---

# tensor-grep enterprise review bundle

Verified against **tg 1.95.0** (2026-07-23).

`tg review-bundle` is the enterprise packaging surface for change review. It is **not** a substitute for `tg agent` / `tg evidence emit`.

## Create

```bash
# Required: rewrite audit manifest from an applied rewrite / audit trail
tg review-bundle create \
  --manifest /path/to/audit-manifest.json \
  --scan /path/to/ruleset-scan.json \    # optional
  --checkpoint-id ckpt-… \              # optional
  --previous-manifest /path/to/prev.json \  # optional diff base
  --receipt /path/to/receipt.json \     # repeatable; embeds a signed EvidenceReceipt in the bundle
  --output /tmp/review-bundle.json \
  --json

tg review-bundle verify --help   # integrity / checksum verification
tg audit-history --json          # discover known manifests
tg audit-verify MANIFEST --json  # MANIFEST is positional, no --manifest flag
```

## CI gate chain (shipped #681)

`--receipt` (repeatable, `create`) embeds one or more signed `EvidenceReceipt`s directly in the bundle;
`verify` then re-checks them against the real PR state, not just internal consistency:

```bash
tg review-bundle verify /tmp/review-bundle.json \
  --against <PR-head-sha> \          # re-verify signature/trust/revision-freshness against the REAL PR head, never $GITHUB_SHA (resolves to a merge commit, not the head)
  --min-receipts 1 \                 # policy floor: reject an empty-to-[] receipts list (closes the all([])==True bypass)
  --expect-key KEY_ID \              # require a specific signer key id, distinct from --trusted-key
  --require-trusted \                # fail closed unless the embedded key matches --trusted-key
  --trusted-key BASE64_PUBKEY \
  --json
```

Both `--min-receipts` and `--expect-key` are default-OFF policy levers — a bundle with a stripped-empty
`receipts: []` list previously still verified `valid:true` (`all([]) == True`); `--min-receipts N` closes
that gap. Full CI-gate wiring and the PR-head-sha rationale: `docs/enterprise_review_bundle_ci.md`.

## Common mistakes

```bash
# WRONG — --capsule is not a create flag
tg review-bundle create tensor-grep --capsule capsule.json

# WRONG — treating review-bundle as the primary edit planner
tg review-bundle create   # missing --manifest → typer error

# WRONG — using $GITHUB_SHA for --against in a CI gate (resolves to a merge commit, not the PR head)
tg review-bundle verify bundle.json --against "$GITHUB_SHA"
```

## Companion loop

1. `tg prepare REPO/src "task" --out capsule.json --json` → edit readiness in one call, capsule persisted directly (replaces the old `tg agent` + manual-redirect step)
2. `tg scan --ruleset … --json` → save scan artifact
3. `tg checkpoint create REPO/src --json`
4. Persist rewrite audit manifest (from rewrite/apply tooling)
5. `tg evidence emit … --capsule capsule.json … --sign --out receipt.json`
6. `tg review-bundle create --manifest … [--scan …] [--checkpoint-id …] --receipt receipt.json --json`

## Related

- `tensor-grep-enterprise-agent`, `tensor-grep-code-audit`, `tensor-grep-run-and-operate`, `tensor-grep-ledger` (advisory claim/finding-reuse — a sibling coordination primitive; `review-bundle`'s receipts are the audit trail, `ledger` is the live-coordination layer, and neither substitutes for the other)
