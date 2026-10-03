export const meta = {
  name: 'tg-audit-fix-loop',
  description: 'Run the codex-gated adversarial audit-fix loop on a verified H/M finding: behavioral RED -> minimal fix -> independent adversarial gate (fresh-context Opus seat) -> verify every finding with your own probes -> re-audit until SHIP. Loads the tensor-grep-codex-gated-audit-loop skill. Also expresses a docs-artifact audit round (findings against committed docs with per-round artifact hash chains).',
  whenToUse: 'Fixing a verified audit finding (H/M) that needs a draft PR; writing a gated test that must pass identically on the desktop AND CI pytest env; any security-surface change needing an adversarial gate before merge; or a fix to a finding a prior fix already shipped wrong (the twin law).',
  phases: [
    { title: 'Seam', detail: 're-verify the finding on origin/main with git-show (never the dirty local tree); census the argv-rewrite doors — see skills tensor-grep-argv-normalization-and-shadowing (front-door rewrites, shape-monotonic routing) and tensor-grep-cross-platform-path-confinement (junction/drive-absolute confinement) for the seam-phase census' },
    { title: 'RED', detail: 'behavioral test that fails pre-fix; for env-gated tests, make hermetic by construction; hostile fixtures must BITE — see skill tensor-grep-hermetic-hostile-tests (env-independent seams, fixture-BITES precheck, mutation asserted-applied)' },
    { title: 'GREEN', detail: 'minimal fix; platform-gate path-shape transforms' },
    { title: 'Gate', detail: 'independent adversarial audit (fresh-context Opus seat; try to BREAK it, cite file:line)' },
    { title: 'Verify', detail: 're-probe every finding with YOUR OWN commands; re-audit until SHIP; record rounds in the commit message' },
  ],
}

// ---------------------------------------------------------------------------
// WHY A SEAM+CENSUS PHASE INSTEAD OF TRUSTING THE FINDING.
// The 2026-08-08/09 campaign (H2/M1/M3/M14) proved three things the loop must
// re-derive every run: (1) a finding's file:line drifts release-to-release, so
// re-verify the SYMBOL on origin/main; (2) a front-door argv normalizer
// (SEARCH_OPTION_FIRST_FLAGS -> `tg search ...`) can SHADOW the door a fix
// guards, so census every door the rewritten argv can reach -- see
// tensor-grep-argv-normalization-and-shadowing; (3) a
// Windows-only path transform applied unconditionally flips a confinement
// check on POSIX -- see tensor-grep-cross-platform-path-confinement.
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
    origin_main_sha: { type: 'string', description: 'origin/main HEAD at audit time' },
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
    doors: { type: 'array', description: 'every argv front-door / parse path the fix must reach' },
    artifact_kind: {
      type: 'string',
      enum: ['code', 'docs'],
      description: "'code' (default) or 'docs' for a finding against a committed docs artifact (doc, plan, census count, receipt claim -- not runtime code)",
    },
    artifact_sha256: {
      type: 'string',
      description: 'docs artifacts only: SHA-256 of the canonical artifact bytes bound on origin/main',
    },
    nothing_to_fix: {
      type: 'boolean',
      description: 'true when the finding is false or already fixed at this SHA, for any artifact kind',
    },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['verdict', 'rounds'],
  properties: {
    // Canonical vocabulary is SHIP | FIX-FIRST. SHIP-WITH-NITS stays for gate
    // outputs whose nits are banked; FIX-BEFORE-MERGE is retired wording for FIX-FIRST.
    verdict: { type: 'string', enum: ['SHIP', 'SHIP-WITH-NITS', 'FIX-FIRST'] },
    // The workflow itself may also return NOTHING_TO_FIX (seam found the finding false or already fixed).
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
          artifact_sha256: {
            type: 'string',
            description: 'SHA-256 of the artifact bytes this round reviewed (per-round hash chain; required for docs rounds)',
          },
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
    reason_class: { type: 'string', description: 'the exact expected assertion/reason class' },
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
  required: ['probes', 'all_findings_reproduced', 'findings_reproduced_count', 'fix_confirmed'],
  properties: {
    fix_confirmed: {
      type: 'boolean',
      description: 'true only if YOU re-ran the RED test and it now passes for the right reason',
    },
    findings_reproduced_count: { type: 'integer', description: 'how many of the gate findings your own probes reproduced' },
    probes: {
      type: 'array',
      minItems: 1,
      description: 'must include the re-run of the RED test showing it passes',
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
HOUSE CONSTRAINTS (shared dev box; these apply to every step):
- CPU-SAFE: NEVER run cargo build/test/check/clippy, and NEVER run tests/e2e/test_routing_parity.py
  (it invokes cargo run). Rust compile evidence comes from PR CI only; rustfmt --check is allowed.
- Never \`git add .\` / \`git add -A\`; stage explicit paths only.
- \`git commit --amend\` only while the branch has never been pushed: \`git log --oneline origin/<branch>\` must print nothing first; after a push, make an ordinary second commit.
- Before any baseline swap (\`git checkout origin/main -- <file>\`, Out-File/patch revert), copy the file's current uncommitted bytes aside; prefer re-editing the single mutated line back.
- An UNCITED finding is DISCARDED. Every claim needs a file:line or a command plus its output.
- A FAILED seat / empty payload is a HOLE, not a pass: report it, never paper over it.
`

const FINDING_RAW = (args && (args.finding || (args._text && args._text.join(' ')))) || null
const FINDING = typeof FINDING_RAW === 'string' && FINDING_RAW.trim() ? FINDING_RAW.trim() : null
if (!FINDING) {
  return {
    verdict: 'FIX-FIRST',
    rounds: [],
    error: 'usage: /tg-audit-fix-loop <H/M finding id + one-line truth> -- nothing to loop on',
  }
}

// Phase 1: SEAM -- re-verify the finding on origin/main (never the dirty local
// tree) and census every door the fix must reach.
phase('Seam')
const seam = await agent(
  `${HOUSE}
LOAD the skill tensor-grep-codex-gated-audit-loop, and consult
tensor-grep-argv-normalization-and-shadowing + tensor-grep-cross-platform-path-confinement for
the census discipline.

FINDING UNDER REPAIR: ${FINDING}

TASK (read-only): re-derive this finding against origin/main. For every symbol it names, run
git-show origin/main:<file> and confirm the symbol still exists and still misbehaves as claimed
(bind to the exact SHA you report). Census EVERY argv front-door / parse path a fix must
reach: a front-door rewrite can shadow the door the finding names.

ARTIFACT KIND: if the finding is against a committed DOCS artifact (a doc, plan, census count,
or receipt claim -- not runtime code), set artifact_kind="docs", bind the canonical bytes with
git-show origin/main:<file> and record their SHA-256 in artifact_sha256 (commit the
plan you cite), and re-derive each named claim against those bytes. Doors may be empty for a
docs finding; that does NOT mean nothing to fix. If the finding is FALSE or ALREADY FIXED at
this SHA -- code or docs -- set nothing_to_fix=true and do not invent work.`,
  { label: 'seam', phase: 'Seam', schema: SEAM_SCHEMA, model: 'sonnet' },
)

// An empty seam payload or an empty door census for a CODE finding is a HOLE (HOUSE rule), not
// evidence that nothing needs fixing -- only an explicit nothing_to_fix=true (with a bound SHA) may end the run, as NOTHING_TO_FIX.
if (!seam) {
  return {
    verdict: 'FIX-FIRST',
    rounds: [],
    error: 'seam seat returned nothing -- a hole, not a pass; re-run the seam phase',
  }
}
if (!/^[0-9a-f]{40}$/.test(seam.origin_main_sha || '')) {
  return {
    verdict: 'FIX-FIRST',
    rounds: [],
    error: 'seam.origin_main_sha is not a 40-hex SHA -- the finding is not bound to a real origin/main; re-run the seam phase',
    seam,
  }
}
if (seam.nothing_to_fix === true) {
  return {
    verdict: 'NOTHING_TO_FIX',
    rounds: [],
    note: `seam phase found nothing to fix at ${seam.origin_main_sha} (finding false or already fixed) -- no loop run`,
    seam,
  }
}
if ((seam.doors || []).length === 0 && seam.artifact_kind !== 'docs') {
  return {
    verdict: 'FIX-FIRST',
    rounds: [],
    error: 'empty door census for a code finding -- a hole, not a pass; re-run the seam phase',
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
// class; hermetic by construction; hostile fixtures must BITE.
phase('RED')
const red = await agent(
  `${HOUSE}
${SEAM_TEXT}
LOAD the skill tensor-grep-hermetic-hostile-tests.

TASK: write ONE behavioral test that fails on the current code for the finding above.
- It must fail with the exact expected assertion/reason class: a crash, import failure,
  setup error, or skip is NOT a valid RED -- pin the reason class in the test.
- Env-gated seams are hermetic by construction: never branch on ambient availability.
- Hostile fixtures must BITE: assert the fixture precondition before trusting the arm.
- Run it and paste the verbatim failing output. Do NOT fix the code in this phase.
- Wrap the run in a shell timeout with a per-test --timeout (anti-hang protocol).
- For env-dependent skips and Rust test handshakes, follow the tensor-grep-hermetic-hostile-tests skill.
- If seam.artifact_kind === "docs" the RED is a REPRODUCTION, not a behavioral test: paste the
  exact false passage from the committed artifact bytes with its SHA-256, and name the falsity
  class (wrong count, missing receipt, untracked-plan citation, ...). Put the doc path in
  test_file, the verbatim false passage + hash in failure_output, and the falsity class in
  reason_class. No test file is written for a docs RED.`,
  { label: 'red', phase: 'RED', schema: RED_SCHEMA, model: 'sonnet' },
)

if (!red) {
  return { verdict: 'FIX-FIRST', rounds: [], error: 'RED seat returned nothing -- a hole, not a pass', seam }
}

// Phase 3: GREEN -- minimal fix; platform-gate path-shape transforms.
phase('GREEN')
const green = await agent(
  `${HOUSE}
${SEAM_TEXT}
RED TEST: ${red.test_file} (expected reason class: ${red.reason_class})

TASK: make the MINIMAL fix that turns the RED test green for the right reason. Platform-gate any
path-shape transform. Reach EVERY door in the seam census, not just the one the finding
named. Run the RED test plus the narrow suites around the touched files; paste verbatim output.
Stage nothing; the orchestrator owns git.
- If seam.artifact_kind === "docs": the minimal fix is a docs edit to the committed artifact.
  Record the post-edit SHA-256 in notes, paste the re-derivation that proves the claim now true
  as test_output, and run any repo doc-governance test that consumes the edited file (pytest is
  allowed; cargo is not). Stage nothing; the orchestrator owns git.`,
  { label: 'green', phase: 'GREEN', schema: GREEN_SCHEMA, model: 'sonnet' },
)

if (!green) {
  return { verdict: 'FIX-FIRST', rounds: [], error: 'GREEN seat returned nothing -- a hole, not a pass', seam, red }
}
let latestGreen = green
const fixText = () => `
RED TEST: ${red.test_file} (reason class: ${red.reason_class})
FILES CHANGED BY THE FIX: ${JSON.stringify(latestGreen.files_changed || [])}
`

// Phases 4-5: GATE + VERIFY, looped. The gate is a fresh-context adversarial audit (independent of the fix author); verify re-probes every finding with its own commands. A FIX-FIRST verdict feeds one repair round. A104: the gate is a real-finding convergence loop and ends only on independent SHIP, never on round count -- the RUST-REPLACE-SYMLINK guard took 13 rounds plus a final codex pass to SHIP (tensor-grep-codex-gated-audit-loop, "Campaign-scale round receipts"). Budget 10+ rounds for a security-class finding; MAX_ROUNDS is a parking point, not a conclusion.
const MAX_ROUNDS = 10
let verdict = null
const allRounds = []
let repairContext = ''

for (let round = 1; round <= MAX_ROUNDS; round++) {
  phase('Gate')
  const gate = await agent(
    `${HOUSE}
${SEAM_TEXT}${fixText()}
You are the INDEPENDENT adversarial gate. You did not write the fix. Try to BREAK it.
${repairContext}
Cite file:line for every finding; default FIX-FIRST if uncertain. Verdicts: SHIP | SHIP-WITH-NITS
(nits banked) | FIX-FIRST (+file:line + repro + minimal fix per finding). A finding with
no citation is discarded. Record each as a round row with round=${round}.
- For a docs artifact (seam.artifact_kind === "docs"), re-read the committed bytes, try to
  falsify every claim, and record the artifact_sha256 you reviewed on every round row -- the
  per-round hash chain is what makes an APPROVE-on-a-hash a checkable claim.`,
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
${SEAM_TEXT}${fixText()}
GATE FINDINGS FOR ROUND ${round} (verdict ${gate.verdict}):
${JSON.stringify(gate.rounds || [], null, 1)}

TASK: re-probe EVERY gate finding above with YOUR OWN commands (never trust the fix author's
transcript or the gate's). You MUST re-run the RED test (${red.test_file}) and show it now passes
for the right reason (${red.reason_class}); set fix_confirmed=true only if it does. Report each
probe's command + verbatim result, how many gate findings you reproduced
(findings_reproduced_count), and all_findings_reproduced.
- For a docs artifact, re-probe means re-deriving the doc's counts/claims with your own commands
  against the edited bytes and comparing to the round's recorded artifact_sha256.`,
    { label: `verify:r${round}`, phase: 'Verify', schema: VERIFY_SCHEMA, model: 'sonnet' },
  )

  const verifyOk = !!verify && verify.fix_confirmed === true && (verify.probes || []).length > 0
  let verifyNote = ''
  if (verdict === 'SHIP' || verdict === 'SHIP-WITH-NITS') {
    // The Verify phase gates SHIP: it stands only on an independent verify that re-ran the RED
    // test and confirmed the fix.
    if (verifyOk) break
    verdict = 'FIX-FIRST'
    verifyNote = verify
      ? 'verify seat did not confirm the fix (fix_confirmed false or no probes)'
      : 'verify seat returned nothing'
    allRounds.push({
      round,
      severity: 'VERIFY-FAILURE',
      area: verifyNote,
      file: '-',
      fix: 're-run gate + verify; a SHIP is accepted only when the independent verify confirms it',
    })
  } else if (verifyOk && verify.findings_reproduced_count === 0 && verify.all_findings_reproduced === false) {
    // Unreproduced gate findings must not force FIX-FIRST: bank them as nits.
    verdict = 'SHIP-WITH-NITS'
    allRounds.push({
      round,
      severity: 'NIT',
      area: 'gate FIX-FIRST findings were not reproduced by the independent verify; banked as nits',
      file: '-',
      fix: 'none required',
    })
    break
  }

  if (round < MAX_ROUNDS) {
    repairContext = `
PRIOR GATE FINDINGS TO REPAIR (round ${round}):
${JSON.stringify(gate.rounds || [], null, 1)}
${verifyNote ? `VERIFY FAILURE: ${verifyNote}\n` : ''}VERIFY PROBES:
${verify ? JSON.stringify(verify.probes, null, 1) : '(verify seat returned nothing)'}
`
    phase('GREEN')
    const repaired = await agent(
      `${HOUSE}
${SEAM_TEXT}
TASK: repair ONLY the gate findings listed below, minimally, then re-run the RED test and the
narrow suites around the touched files; paste verbatim output.
${repairContext}`,
      { label: `repair:r${round}`, phase: 'GREEN', schema: GREEN_SCHEMA, model: 'sonnet' },
    )
    if (repaired) latestGreen = repaired
  }
}

return {
  finding: FINDING,
  verdict,
  rounds: allRounds,
  seam,
  red,
  green: latestGreen,
  max_rounds: MAX_ROUNDS,
  note: verdict === 'SHIP' || verdict === 'SHIP-WITH-NITS'
    ? 'gate passed; orchestrator owns commit/PR per the usual gates'
    : 'still FIX-FIRST after the round budget; park honestly with the round receipts (post the verdict as an artifact)',
}
