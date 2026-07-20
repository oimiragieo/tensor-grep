# Enterprise Review Bundle CI Gate

This runbook wires tensor-grep's existing agent-evidence primitives -- `tg agent`, `tg evidence
emit --sign`, `tg review-bundle create --receipt`, and `tg review-bundle verify --against` -- into
a single local-first chain that a CI job can gate a pull request on. It closes the loop described
in `docs/CONTRACTS.md` sections 8 (EvidenceReceipt signing) and 11 (Review bundles): an agent's
signed claim about what it did is carried in a review bundle, and CI fails the check (exit `1`)
if that claim is unsigned, untrusted, tampered, or stale relative to the PR's real head commit.

Everything below runs entirely on the CI runner (no network calls, no external service) -- the
only thing you provide is a pinned Ed25519 public key as a repo/org secret.

## The chain

1. **An agent (or a human) does work and captures evidence.** `tg agent REPO/src "task"
   --json > capsule.json` (or any of the other evidence-producing commands: `tg scan --json`,
   `tg checkpoint create --json`, an applied rewrite's audit manifest) produces the raw artifacts
   the receipt will summarize.
2. **Emit a signed EvidenceReceipt.** `tg evidence emit . --capsule capsule.json --sign --out
   receipt.json` binds the evidence to the CURRENT repo revision (`revision.commit_sha`,
   `revision.dirty`) and signs it with an Ed25519 private key (`tg evidence keygen` once, then
   `--signing-key` / `TG_EVIDENCE_SIGNING_KEY`). This step normally runs wherever the agent did the
   work -- a developer machine, an agent sandbox -- NOT inside the CI job itself, since CI is the
   verifier, not the claimant.
3. **Package it into a review bundle.** `tg review-bundle create --manifest audit-manifest.json
   --receipt receipt.json --output review-bundle.json --json`. `--receipt` is repeatable if more
   than one agent/step contributed evidence to the same change.
4. **Commit or upload `review-bundle.json`** alongside the PR (as a build artifact, a comment
   attachment, or a committed file under review) so the CI job in the PR can read it back.
5. **CI verifies it against the PR's real head commit and gates on the result:**

   ```bash
   tg review-bundle verify review-bundle.json \
     --against "$PR_HEAD_SHA" \
     --require-trusted \
     --trusted-key "$TG_EVIDENCE_TRUSTED_KEY" \
     --json
   ```

   Exit `1` (the command's pre-existing fail-closed contract, unchanged by this feature) means:
   an embedded receipt is unsigned, its key isn't in `--trusted-key`, its content was tampered
   with, its captured commit doesn't match `$PR_HEAD_SHA`, it was captured against a dirty working
   tree, or `$PR_HEAD_SHA` itself didn't resolve to a real commit. Read `.against` and `.receipts[]`
   in the JSON output to see exactly which check failed and why -- see `docs/CONTRACTS.md` section
   11 for the full field shapes.

## GitHub Actions recipe (the trap-safe version)

```yaml
name: Evidence Gate

on:
  pull_request:

jobs:
  verify-evidence:
    runs-on: ubuntu-latest
    steps:
      # CRITICAL: check out the PR HEAD commit explicitly. The default checkout ref for a
      # pull_request event, and $GITHUB_SHA, both resolve to an EPHEMERAL MERGE COMMIT of the PR
      # branch into the base branch -- NOT the PR's actual head commit. If you skip `ref:` here
      # and pass --against "$GITHUB_SHA" below, every receipt's captured commit_sha (which was
      # bound to the real PR head, never a merge commit tg never sees) will NEVER match, and this
      # gate fails RED on every single PR regardless of whether the evidence is genuinely fresh.
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}

      - name: Install tg
        run: pip install tensor-grep

      - name: Verify review bundle against the real PR head
        env:
          TG_EVIDENCE_TRUSTED_KEY: ${{ secrets.TG_EVIDENCE_TRUSTED_KEY }}
        run: |
          tg review-bundle verify review-bundle.json \
            --against "${{ github.event.pull_request.head.sha }}" \
            --require-trusted \
            --trusted-key "$TG_EVIDENCE_TRUSTED_KEY" \
            --json
```

Two details worth calling out explicitly, both already covered above but easy to miss under time
pressure when adapting this snippet:

- `--against` takes `github.event.pull_request.head.sha` (the PR event payload's own head field),
  **not** `github.sha` / `$GITHUB_SHA` (the workflow's ambient commit, which is the merge commit on
  a `pull_request` trigger). Get this backwards and the gate is permanently red.
- The `actions/checkout` `ref:` and the `--against` value must be the SAME commit. If you checkout
  the merge commit but verify against the head SHA (or vice versa), `review-bundle.json` itself
  (read from the checked-out tree) may not even be the version the receipt describes, on top of the
  ref-mismatch problem above.

## Registering a trusted key

`tg evidence keygen --out ~/.tensor-grep/keys/evidence_ed25519.key` generates a keypair once (for
a person or a service identity that emits receipts). `tg evidence pubkey` prints the base64 public
key and its `key_id`; store the public key as a CI secret (`TG_EVIDENCE_TRUSTED_KEY` in the
recipe above) or in `TG_EVIDENCE_TRUSTED_KEYS` (comma-separated, for more than one trusted
signer). The private key never leaves the machine/agent that signs; CI only ever needs the public
key, matching the asymmetric-trust design in `docs/CONTRACTS.md` section 8.

## What this gate does and does not prove

- It proves: a specific, pinned identity's key signed a specific EvidenceReceipt, the receipt's
  content has not been altered since signing, and the receipt was captured at exactly the PR's
  head commit with no uncommitted changes in the working tree at capture time.
- It does NOT prove: that the receipt's *content* (the blast-radius evidence, validation outcomes,
  confidence score, etc.) is itself correct -- `tg` never independently re-derives or fact-checks
  what an agent claims to have done inside the receipt; it only proves the claim is authentic and
  fresh. Treat a passing gate as "this evidence trail is genuine and current," not "this change is
  correct."
- It is entirely local-first: no external service, no network call, no dependency on a specific CI
  vendor. The same three commands (`tg review-bundle create --receipt`, `tg review-bundle verify
  --against`) work identically outside GitHub Actions -- GitLab CI, a pre-merge hook, or a plain
  shell script -- as long as the caller supplies the correct target-ref SHA for that platform's
  equivalent of the "PR head, not the merge commit" distinction above.
