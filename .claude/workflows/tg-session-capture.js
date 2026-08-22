export const meta = {
  name: 'tg-session-capture',
  description:
    'Audit tensor-grep skills for accuracy against current code, research two new-skill candidates with Exa, and decide fold-vs-new.',
  whenToUse:
    'After a session that changed product behaviour, CI, or release discipline, to capture learnings into skills/docs without hand-auditing 36 skills.',
  phases: [
    { title: 'Audit', detail: 'per-cluster skill accuracy vs live code (sonnet)' },
    { title: 'Research', detail: 'Exa grounding for new-skill candidates (sonnet)' },
    { title: 'Synthesis', detail: 'fold-vs-new decision + ranked action list (opus)' },
  ],
}

// ---------------------------------------------------------------------------
// HOUSE RULES — pasted VERBATIM into every agent prompt. A subagent inherits
// none of the orchestrator's context; if it is not in the prompt it does not exist.
// ---------------------------------------------------------------------------
const HOUSE = `
HOUSE RULES (verbatim, non-negotiable):
- Repo root: C:\\dev\\projects\\tensor-grep . Read with absolute paths.
- You are READ-ONLY unless your task says otherwise. Do NOT run git. Do NOT commit.
  Do NOT create branches. The orchestrator does 100% of git.
- Do NOT run \`cargo\` anything, and do NOT run the full pytest suite. This is a SHARED
  dev box and those saturate it. Reading files and targeted greps are fine.
- An uncited finding is DISCARDED. Cite file:line for every claim about the codebase.
- If a slice is CLEAN, say CLEAN and give the strongest claim you actually verified.
  A bare "no findings" is indistinguishable from not having looked.
- If you cannot read a required file, say CANNOT_READ and name it. Do NOT infer contents.
- Do not dispatch other agents. Answer inline.
`

// ---------------------------------------------------------------------------
// SESSION FACTS LEDGER — verified live by the orchestrator before dispatch.
// These are the surfaces that CHANGED, so they are where skills go stale.
// (map-ledger-must-be-verified: every line below was measured this session,
//  not transcribed from prior project text.)
// ---------------------------------------------------------------------------
const LEDGER = `
VERIFIED SESSION FACTS (2026-08-21/22). Treat as ground truth; they OVERRIDE older doc text.
1. Released v1.111.3, v1.111.4, v1.111.5, v1.111.6 — each verified PER-ARTIFACT (4 files:
   3 wheels incl win_amd64 + sdist). Prior state: v1.111.2 was TAGGED with ZERO PyPI files,
   v1.111.1 had 2 of 4. Last complete before this session was v1.111.0.
2. PYPI-SIZE-CAP cleared: 713 -> 287 releases, 10.734 -> 4.747 GB.
3. Product fixes shipped: scan fail-closed on a missing root (cli/scan_guardrails.py
   missing_scan_paths); ast-grep remediation text in every unavailable refusal
   (cli/ast_workflows.py); inline-rules 'engine:' key preserved (cli/ast_scan.py);
   tg find --json now populates routing_backend/routing_reason (cli/main.py);
   cli_modes globals shim matches the module LEAF name; rulesets availability disclosure
   (rulesets_runnable + rulesets_unavailable_reason in cli/ast_scan.py).
4. rust_core/src/index_lock.rs: the wall-clock assertion in
   heartbeat_keeps_a_slow_holder_alive_past_the_stale_threshold was REMOVED. One such
   failure had been skipping the ENTIRE release chain (Semantic Release + build-pypi +
   publish-pypi all 'skipped') while the correctness assertion passed.
5. .github/workflows/ci.yml: the 'code' path filter now also watches docs/audits, because
   the handler-disposition ledger there is TEST INPUT — a ledger-only PR was skipping every
   test-python lane INCLUDING the test that reads that ledger.
6. NEW: scripts/ci-local/ — a Docker CI-parity harness (Dockerfile, entrypoint.sh, run.sh)
   running the cargo + pytest lanes locally in a CPU-capped container, plus
   tests/unit/test_ci_local_harness_parity.py as its drift tripwire. Currently in PR #1093.
7. Clean-room measured: on a stock 'pip install tensor-grep==1.111.6',
   'tg scan --ruleset' EXITS NONZERO — ast-grep CLI ships in no extra. The disclosure
   correctly reports rulesets_runnable=false. A dev box with ast-grep on PATH hides this.
`

const AUDIT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['cluster', 'verdict', 'findings', 'read_failures'],
  properties: {
    cluster: { type: 'string' },
    verdict: { type: 'string', enum: ['CLEAN', 'STALE', 'CANNOT_READ'] },
    strongest_verified_claim: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['skill', 'file_line', 'problem', 'proposed_fix', 'severity'],
        properties: {
          skill: { type: 'string' },
          file_line: { type: 'string' },
          problem: { type: 'string' },
          proposed_fix: { type: 'string' },
          severity: { type: 'string', enum: ['HIGH', 'MEDIUM', 'LOW'] },
        },
      },
    },
    read_failures: { type: 'array', items: { type: 'string' } },
  },
}

const RESEARCH_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['topic', 'recommendation', 'external_sources', 'proposed_outline'],
  properties: {
    topic: { type: 'string' },
    recommendation: { type: 'string', enum: ['NEW_SKILL', 'FOLD_INTO_EXISTING', 'SKIP'] },
    fold_target: { type: 'string' },
    rationale: { type: 'string' },
    external_sources: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['url', 'what_it_adds'],
        properties: { url: { type: 'string' }, what_it_adds: { type: 'string' } },
      },
    },
    proposed_outline: { type: 'array', items: { type: 'string' } },
  },
}

// Skill clusters. Each agent gets 3-4 NAMED skills — never "audit the skills".
const CLUSTERS = [
  {
    key: 'change-safety',
    skills: [
      'tensor-grep-change-control',
      'tensor-grep-validation-and-qa',
      'tensor-grep-hermetic-hostile-tests',
    ],
    focus:
      'Do these describe the CURRENT gates? Ledger items 4,5,6 changed CI and test discipline. Does anything claim a gate or command that no longer exists?',
  },
  {
    key: 'release',
    skills: [
      'tensor-grep-release-and-positioning',
      'tensor-grep-release-drift-check',
      'tensor-grep-backlog-campaign',
    ],
    focus:
      'Ledger items 1,2 changed release reality. Does any text still imply tagged==published, or a version-presence check instead of a per-artifact filename check?',
  },
  {
    key: 'debug-diagnose',
    skills: [
      'tensor-grep-debugging-playbook',
      'tensor-grep-failure-archaeology',
      'tensor-grep-diagnostics-and-tooling',
    ],
    focus:
      'Ledger items 3,4,7 changed failure surfaces. Are the documented symptoms/remedies still the ones the product emits?',
  },
  {
    key: 'product-surfaces',
    skills: [
      'tensor-grep-find-and-route',
      'tensor-grep-config-and-flags',
      'tensor-grep-argv-normalization-and-shadowing',
    ],
    focus:
      'Ledger item 3 changed tg find --json (routing fields) and scan guardrails. Do these skills document the fields/flags that actually exist now?',
  },
  {
    key: 'ops-dogfood',
    skills: [
      'tensor-grep-workspace-dogfood',
      'tensor-grep-run-and-operate',
      'tensor-grep-enterprise-agent',
    ],
    focus:
      'Ledger item 7: a stock pip install cannot run tg scan --ruleset. Do these skills tell a reader to verify on a CLEAN install rather than a maintainer machine?',
  },
]

const RESEARCH_TOPICS = [
  {
    key: 'benchmark-claim',
    topic: 'publishing a defensible performance/benchmark claim for a developer tool',
    context: `tensor-grep has TWO CONFLICTING internal speedup numbers (7.5x and a later 6.4x)
and NO committed harness. A 5-seat council unanimously said WITHDRAW the public 7.5x. The existing
skill .claude/skills/tensor-grep-benchmark-and-proof-toolkit/SKILL.md was grepped and contains only
ONE incidental mention of a noise floor and NO coverage of: minimum-vs-mean, geomean for ratios,
interleaved paired sampling, confidence intervals, never-mix-regimes, or tombstoning a superseded
claim. Decide NEW_SKILL vs FOLD_INTO_EXISTING (fold target would be that file, 534 lines).`,
  },
  {
    key: 'local-ci-parity',
    topic:
      'running CI lanes locally in a container when the dev machine is shared, and keeping the local harness from drifting from the real CI definition',
    context: `This session built scripts/ci-local/ (Dockerfile + entrypoint.sh + run.sh) because
AGENTS.md bans local cargo on a SHARED box. Getting it green surfaced TWELVE CI-vs-local
divergences, including: root having CAP_DAC_OVERRIDE so a chmod-000 hostile fixture did not bite;
a FAILED docker build leaving the previous image tagged so a run silently tested stale bytes;
tmpfs defaulting to noexec so native extensions could not be imported; git refusing a bind mount
as dubious ownership which aborted pytest COLLECTION (zero tests ran); and an out-of-tree
CARGO_TARGET_DIR breaking the product's own PYTHONPATH injection. No existing skill covers this.
Decide NEW_SKILL vs FOLD_INTO_EXISTING.`,
  },
]

// ---------------------------------------------------------------------------
// Audits and research are INDEPENDENT — the research prompts never interpolate an
// audit result. Barrier only at synthesis. (This skill's own 2026-07-29/30 receipt:
// a barrier here was pure wall-clock waste, twice.)
// ---------------------------------------------------------------------------
const [audits, research] = await Promise.all([
  (async () => {
    phase('Audit')
    const out = []
    // waves of <=5 — HARD CAP, written into the script not just the doc
    for (let i = 0; i < CLUSTERS.length; i += 5) {
      const chunk = CLUSTERS.slice(i, i + 5)
      log(`audit wave ${i / 5 + 1}: ${chunk.map((c) => c.key).join(', ')}`)
      const got = await parallel(
        chunk.map((c) => () =>
          agent(
            `${HOUSE}\n${LEDGER}\n\nAUDIT CLUSTER "${c.key}".\n\nRead ONLY these skill files:\n` +
              c.skills
                .map((s) => `  C:\\dev\\projects\\tensor-grep\\.claude\\skills\\${s}\\SKILL.md`)
                .join('\n') +
              `\n\nFOCUS: ${c.focus}\n\nFor each skill, verify its CLAIMS against the live code in ` +
              `src/tensor_grep/ , rust_core/src/ , .github/workflows/ci.yml . A citation that RESOLVES ` +
              `is not enough — check the cited line still contains what the skill says it does. ` +
              `Report only findings you can cite. Propose a concrete minimal edit for each.`,
            { label: `audit:${c.key}`, phase: 'Audit', schema: AUDIT_SCHEMA, model: 'sonnet' },
          ),
        ),
      )
      got.forEach((g, j) => {
        if (g) out.push(g)
        else log(`DROPPED (null): audit:${chunk[j].key} — counted as not-covered`)
      })
    }
    return out
  })(),
  (async () => {
    phase('Research')
    return await pipeline(RESEARCH_TOPICS, (t) =>
      agent(
        `${HOUSE}\n\nRESEARCH TOPIC: ${t.topic}\n\nCONTEXT:\n${t.context}\n\n` +
          `Use Exa (mcp__Exa__web_search_exa / web_fetch_exa) to find CURRENT external practice. ` +
          `Prefer primary sources: standards bodies, published methodology docs, tool documentation, ` +
          `papers. For each source say concretely WHAT IT ADDS that we do not already have.\n\n` +
          `Then decide: NEW_SKILL, FOLD_INTO_EXISTING (name the target file), or SKIP.\n` +
          `FOLD_INTO_EXISTING is a legitimate and often CORRECT answer — a thin skill dilutes the ` +
          `library. Only say NEW_SKILL if the material is substantial AND does not belong in an ` +
          `existing file. Propose a section-by-section outline.`,
        { label: `research:${t.key}`, phase: 'Research', schema: RESEARCH_SCHEMA, model: 'sonnet' },
      ),
    )
  })(),
])

phase('Synthesis')
const researched = research.filter(Boolean)
if (researched.length !== RESEARCH_TOPICS.length) {
  log(`RESEARCH SHORTFALL: ${researched.length}/${RESEARCH_TOPICS.length} returned`)
}

const decision = await agent(
  `${HOUSE}\n\nYou are the synthesis seat. Below are skill-accuracy audits and external research.\n\n` +
    `AUDITS:\n${JSON.stringify(audits, null, 2)}\n\n` +
    `RESEARCH:\n${JSON.stringify(researched, null, 2)}\n\n` +
    `Produce a RANKED action list for the orchestrator. Rules:\n` +
    `- Every action names an exact file path and the exact edit.\n` +
    `- Rank by BLAST RADIUS: a skill that would mislead someone into shipping a wrong claim ` +
    `outranks a stale line number.\n` +
    `- If the audits found nothing for a cluster, say so plainly rather than inventing work.\n` +
    `- Do NOT fabricate results for any cluster missing from the AUDITS payload; flag it as ` +
    `PAYLOAD SHORTFALL instead.\n` +
    `- State explicitly which research candidates are NEW_SKILL vs FOLD, and why.`,
  { label: 'synthesis', phase: 'Synthesis', model: 'opus' },
)

return {
  clusters_dispatched: CLUSTERS.length,
  clusters_returned: audits.length,
  research_dispatched: RESEARCH_TOPICS.length,
  research_returned: researched.length,
  audits,
  research: researched,
  decision,
}
