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
    verdict: { type: 'string', enum: ['SHIP', 'SHIP-WITH-NITS', 'FIX-BEFORE-MERGE'] },
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