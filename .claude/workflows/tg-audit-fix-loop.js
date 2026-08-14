export const meta = {
  name: 'tg-audit-fix-loop',
  description: 'Run the codex-gated adversarial audit-fix loop on a verified H/M finding: behavioral RED -> minimal fix -> independent codex gate -> verify every finding with your own probes -> re-audit until SHIP. Loads the tensor-grep-codex-gated-audit-loop skill.',
  whenToUse: 'Fixing a verified audit finding (H/M) that needs a draft PR; writing a gated test that must pass identically on the desktop AND CI pytest env; any security-surface change needing an adversarial gate before merge (A3); or a fix to a finding a prior fix already shipped wrong (the twin law).',
  phases: [
    { title: 'Seam', detail: 're-verify the finding on origin/main with git-show (never the dirty local tree); census the argv-rewrite doors (A83) — see skills tensor-grep-argv-normalization-and-shadowing (front-door rewrites, shape-monotonic routing) and tensor-grep-cross-platform-path-confinement (junction/drive-absolute confinement) for the seam-phase census' },
    { title: 'RED', detail: 'behavioral test that fails pre-fix; for env-gated tests, make hermetic by construction (A85); hostile fixtures must BITE — see skill tensor-grep-hermetic-hostile-tests (env-independent seams, fixture-BITES precheck, mutation asserted-applied)' },
    { title: 'GREEN', detail: 'minimal fix; platform-gate path-shape transforms (A84)' },
    { title: 'Gate', detail: 'independent codex audit (fresh context, try to BREAK it, cite file:line)' },
    { title: 'Verify', detail: 're-probe every finding with YOUR OWN commands; re-audit until SHIP; record rounds in the commit message' },
  ],
}

// ---------------------------------------------------------------------------
// WHY A SEAM+CENSUS PHASE INSTEAD OF TRUSTING THE FINDING.
// The 2026-08-08/09 campaign (H2/M1/M3/M14) proved three things the loop must
// re-derive every run: (1) a finding's file:line drifts release-to-release, so
// re-verify the SYMBOL on origin/main; (2) a front-door argv normalizer
// (SEARCH_OPTION_FIRST_FLAGS -> `tg search ...`) can SHADOW the door a fix
// guards, so census every door the rewritten argv can reach (A83) -- see
// tensor-grep-argv-normalization-and-shadowing; (3) a
// Windows-only path transform applied unconditionally flips a confinement
// check on POSIX (A84) -- see tensor-grep-cross-platform-path-confinement.
// For RED-phase hostile fixtures, tensor-grep-hermetic-hostile-tests carries
// the env-independent seam + fixture-BITES construction discipline. The seam
// phase is the anti-drift ledger: it derives
// the ground-truth facts by running git-show, then the RED/GREEN/Gate/Verify
// phases work against that ledger -- never against a frozen citation.
// ---------------------------------------------------------------------------

const SEAM_SCHEMA = {
  type: 'object',
  required: ['finding', 'origin_main_sha', 'seams', 'doors'],
  properties: {
    finding: { type: 'string', description: 'the H/M finding id + one-line truth' },
    origin_main_sha: { type: 'string', description: 'origin/main HEAD at audit time (A44)' },
    seams: {
      type: 'array',
      items: {
        type: 'object',
        required: ['symbol', 'file', 'resolved'],
        properties: {
          symbol: { type: 'string' },
          file: { type: 'string' },
          resolved: { type: 'string', description: 'git-show origin/main:<file> symbol lookup result' },
        },
      },
    },
    doors: { type: 'array', description: 'every argv front-door / parse path the fix must reach (A83 census)' },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['verdict', 'rounds'],
  properties: {
    // Canonical A3 vocabulary is SHIP | FIX-FIRST. SHIP-WITH-NITS stays for
    // gate outputs whose nits are banked (A19); FIX-BEFORE-MERGE is retired
    // wording for FIX-FIRST (2026-08-12 retention audit: the two vocabularies
    // coexisting was a self-contradiction).
    verdict: { type: 'string', enum: ['SHIP', 'SHIP-WITH-NITS', 'FIX-FIRST'] },
    rounds: {
      type: 'array',
      items: {
        type: 'object',
        required: ['round', 'severity', 'area', 'file', 'fix'],
        properties: {
          round: { type: 'integer' },
          severity: { type: 'string' },
          area: { type: 'string' },
          file: { type: 'string' },
          fix: { type: 'string' },
        },
      },
    },
  },
}

const RED_SCHEMA = {
  type: 'object',
  required: ['test_file', 'failure_output', 'reason_class'],
  properties: {
    test_file: { type: 'string', description: 'path of the new behavioral test' },
    failure_output: { type: 'string', description: 'verbatim failing output, pre-fix' },
    reason_class: { type: 'string', description: 'the exact expected assertion/reason class (A61)' },
  },
}

const GREEN_SCHEMA = {
  type: 'object',
  required: ['files_changed', 'test_output', 'notes'],
  properties: {
    files_changed: { type: 'array', items: { type: 'string' } },
    test_output: { type: 'string', description: 'verbatim passing output of the RED test' },
    notes: { type: 'string' },
  },
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['probes', 'all_findings_reproduced'],
  properties: {
    probes: {
      type: 'array',
      items: {
        type: 'object',
        required: ['finding', 'command', 'result'],
        properties: {
          finding: { type: 'string' },
          command: { type: 'string' },
          result: { type: 'string' },
        },
      },
    },
    all_findings_reproduced: { type: 'boolean' },
  },
}

const HOUSE = `
HOUSE CONSTRAINTS (verbatim, non-negotiable):
- CPU-SAFE: NEVER run cargo build/test/check/clippy, and NEVER run tests/e2e/test_routing_parity.py
  (it invokes cargo run). Rust compile evidence comes from PR CI only; rustfmt --check is allowed.
- Never \`git add .\` / \`git add -A\`; stage explicit paths only.
- \`git commit --amend\` only while the branch has never been pushed: \`git log --oneline origin/<branch>\` must print nothing first; after a push, make an ordinary second commit (A110).
- Before any baseline swap (\`git checkout origin/main -- <file>\`, Out-File/patch revert), copy the file's current uncommitted bytes aside; prefer re-editing the single mutated line back (A103).
- An UNCITED finding is DISCARDED. Every claim needs a file:line or a command plus its output.
- A FAILED seat / empty payload is a HOLE, not a pass: report it, never paper over it.
`

const FINDING = (args && (args.finding || (args._text && args._text.join(' ')))) || null
if (!FINDING) {
  return {
    verdict: 'FIX-FIRST',
    rounds: [],
    error: 'usage: /tg-audit-fix-loop <H/M finding id + one-line truth> -- nothing to loop on',
  }
}

// Phase 1: SEAM -- re-verify the finding on origin/main (never the dirty local
// tree) and census every door the fix must reach (A83).
phase('Seam')
const seam = await agent(
  `${HOUSE}
LOAD the skill tensor-grep-codex-gated-audit-loop, and consult
tensor-grep-argv-normalization-and-shadowing + tensor-grep-cross-platform-path-confinement for
the census discipline.

FINDING UNDER REPAIR: ${FINDING}

TASK (read-only): re-derive this finding against origin/main. For every symbol it names, run
git-show origin/main:<file> and confirm the symbol still exists and still misbehaves as claimed
(A44: bind to the exact SHA you report). Census EVERY argv front-door / parse path a fix must
reach (A83): a front-door rewrite can shadow the door the finding names. If the finding is FALSE
or ALREADY FIXED at this SHA, say so in "finding" and return an empty doors list -- do not
invent work.`,
  { label: 'seam', phase: 'Seam', schema: SEAM_SCHEMA, model: 'sonnet' },
)

if (!seam || (seam.doors || []).length === 0) {
  return {
    verdict: 'SHIP',
    rounds: [],
    note: 'seam phase found nothing to fix (finding false, already fixed, or empty census) -- no loop run',
    seam,
  }
}

const SEAM_TEXT = `
SEAM LEDGER (derived live from origin/main; work against THIS, never a frozen citation):
  finding = ${seam.finding}
  origin_main_sha = ${seam.origin_main_sha}
  seams = ${JSON.stringify(seam.seams)}
  doors = ${JSON.stringify(seam.doors)}
`

// Phase 2: RED -- behavioral test that fails pre-fix for the EXPECTED reason
// class (A61); hermetic by construction (A85); hostile fixtures must BITE.
phase('RED')
const red = await agent(
  `${HOUSE}
${SEAM_TEXT}
LOAD the skill tensor-grep-hermetic-hostile-tests.

TASK: write ONE behavioral test that fails on the current code for the finding above.
- It must fail with the exact expected assertion/reason class (A61): a crash, import failure,
  setup error, or skip is NOT a valid RED -- pin the reason class in the test.
- Env-gated seams are hermetic by construction (A85): never branch on ambient availability.
- Any environment-dependent SKIP branch that cannot be removed panics under an armed env var in CI (A106: the TG_REQUIRE_SYMLINK_TESTS pattern) -- a green run of silent skips proves nothing.
- Hostile fixtures must BITE: assert the fixture precondition before trusting the arm.
- Run it and paste the verbatim failing output. Do NOT fix the code in this phase.
- Wrap the run in a shell timeout with a per-test --timeout (anti-hang protocol).
- Bounded test handshakes use capacity-1 channels with recv_timeout on every receive; an expiry panics CANNOT_MEASURE:, never a verdict (A109) -- a capacity-0 rendezvous blocks forever.`,
  { label: 'red', phase: 'RED', schema: RED_SCHEMA, model: 'sonnet' },
)

// Phase 3: GREEN -- minimal fix; platform-gate path-shape transforms (A84).
phase('GREEN')
const green = await agent(
  `${HOUSE}
${SEAM_TEXT}
RED TEST: ${red ? `${red.test_file} (expected reason class: ${red.reason_class})` : '(RED phase returned nothing -- STOP and report)'}

TASK: make the MINIMAL fix that turns the RED test green for the right reason. Platform-gate any
path-shape transform (A84). Reach EVERY door in the seam census, not just the one the finding
named. Run the RED test plus the narrow suites around the touched files; paste verbatim output.
Stage nothing; the orchestrator owns git.`,
  { label: 'green', phase: 'GREEN', schema: GREEN_SCHEMA, model: 'sonnet' },
)

// Phases 4-5: GATE + VERIFY, looped. The gate is a fresh-context adversarial audit (independent of the fix author); verify re-probes every finding with its own commands. A FIX-FIRST verdict feeds one repair round. A104: the gate is a real-finding convergence loop and ends only on independent SHIP, never on round count -- the RUST-REPLACE-SYMLINK guard took 13 rounds plus a final codex pass to SHIP (tensor-grep-codex-gated-audit-loop, "Campaign-scale round receipts"). Budget 10+ rounds for a security-class finding; MAX_ROUNDS is a parking point, not a conclusion.
const MAX_ROUNDS = 10
let verdict = null
const allRounds = []
let repairContext = ''

for (let round = 1; round <= MAX_ROUNDS; round++) {
  phase('Gate')
  const gate = await agent(
    `${HOUSE}
${SEAM_TEXT}
You are the INDEPENDENT adversarial gate. You did not write the fix. Try to BREAK it.
${repairContext}
Cite file:line for every finding; default FIX-FIRST if uncertain. Verdicts: SHIP | SHIP-WITH-NITS
(nits banked per A19) | FIX-FIRST (+file:line + repro + minimal fix per finding). A finding with
no citation is discarded. Record each as a round row with round=${round}.`,
    { label: `gate:r${round}`, phase: 'Gate', schema: VERDICT_SCHEMA, model: 'opus' },
  )

  if (!gate) {
    verdict = 'FIX-FIRST'
    allRounds.push({ round, severity: 'GATE-FAILURE', area: 'gate seat returned nothing', file: '-', fix: 're-run the gate; an empty seat is a hole, not a pass' })
    break
  }
  allRounds.push(...(gate.rounds || []).map((r) => ({ ...r, round })))
  verdict = gate.verdict

  phase('Verify')
  const verify = await agent(
    `${HOUSE}
${SEAM_TEXT}
TASK: re-probe EVERY finding recorded for round ${round} with YOUR OWN commands (never trust the
fix author's transcript). Include the RED test's reason class and the door census. Report each
probe's command + verbatim result.`,
    { label: `verify:r${round}`, phase: 'Verify', schema: VERIFY_SCHEMA, model: 'sonnet' },
  )

  if (verdict === 'SHIP' || verdict === 'SHIP-WITH-NITS') break

  if (round < MAX_ROUNDS) {
    repairContext = `
PRIOR GATE FINDINGS TO REPAIR (round ${round}):
${JSON.stringify(gate.rounds || [], null, 1)}
VERIFY PROBES:
${verify ? JSON.stringify(verify.probes, null, 1) : '(verify seat returned nothing)'}
`
    phase('GREEN')
    await agent(
      `${HOUSE}
${SEAM_TEXT}
TASK: repair ONLY the gate findings listed below, minimally, then re-run the RED test and the
narrow suites around the touched files; paste verbatim output.
${repairContext}`,
      { label: `repair:r${round}`, phase: 'GREEN', schema: GREEN_SCHEMA, model: 'sonnet' },
    )
  }
}

return {
  finding: FINDING,
  verdict,
  rounds: allRounds,
  seam,
  red,
  green,
  max_rounds: MAX_ROUNDS,
  note: verdict === 'SHIP' || verdict === 'SHIP-WITH-NITS'
    ? 'gate passed; orchestrator owns commit/PR per the usual gates'
    : 'still FIX-FIRST after the round budget; park honestly with the round receipts (A28: post the verdict as an artifact)',
}
