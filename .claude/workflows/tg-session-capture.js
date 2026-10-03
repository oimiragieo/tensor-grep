export const meta = {
  name: 'tg-session-capture',
  description:
    'Audit tensor-grep skills for accuracy against current code and the facts this session measured, optionally research new-skill candidates with Exa, and decide fold-vs-new.',
  whenToUse:
    'After a session that changed product behaviour, CI, or release discipline, to capture learnings into skills/docs without hand-auditing the whole skill library. Pass args.ledger (the session facts you measured) and optionally args.research_topics ([{key, topic, context}]).',
  phases: [
    { title: 'Audit', detail: 'per-cluster skill accuracy vs live code (sonnet)' },
    { title: 'Research', detail: 'Exa grounding for new-skill candidates (sonnet)' },
    { title: 'Synthesis', detail: 'fold-vs-new decision + ranked action list (opus)' },
  ],
}

// ---------------------------------------------------------------------------
// HOUSE RULES — pasted into every agent prompt. A subagent inherits
// none of the orchestrator's context; if it is not in the prompt it does not exist.
// ---------------------------------------------------------------------------
const HOUSE = `
HOUSE RULES (shared dev box; these apply to every step):
- Work in the repository checkout you are invoked in (your working directory); report its
  absolute path, and read files relative to it.
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
// The facts are SUPPLIED PER RUN (args.ledger), never frozen into this file: a ledger
// hardcoded at authoring time goes stale one release later and then tells every
// subagent to trust out-of-date facts over the current docs.
// ---------------------------------------------------------------------------
const LEDGER_RAW = (args && (args.ledger || (args._text && args._text.join(' ')))) || null
const LEDGER_INPUT = typeof LEDGER_RAW === 'string' && LEDGER_RAW.trim() ? LEDGER_RAW.trim() : null
if (!LEDGER_INPUT) {
  return {
    error:
      'usage: /tg-session-capture ledger=<the facts this session measured, one per line> [research_topics=[{key, topic, context}]] -- nothing to audit against',
  }
}
const LEDGER = `
VERIFIED SESSION FACTS (measured by the orchestrator this session). These are the surfaces
that changed, so they are where skills are most likely to be stale. Re-check any fact you
rely on against the tree before reporting drift.
${LEDGER_INPUT}
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
      'Do these describe the CURRENT gates, given any CI or test-discipline changes in the session facts? Does anything claim a gate or command that no longer exists?',
  },
  {
    key: 'release',
    skills: [
      'tensor-grep-release-and-positioning',
      'tensor-grep-release-drift-check',
      'tensor-grep-backlog-campaign',
    ],
    focus:
      'Do these match current release reality, given any release changes in the session facts? Does any text imply tagged==published, or a version-presence check instead of a per-artifact filename check?',
  },
  {
    key: 'debug-diagnose',
    skills: [
      'tensor-grep-debugging-playbook',
      'tensor-grep-failure-archaeology',
      'tensor-grep-diagnostics-and-tooling',
    ],
    focus:
      'Given any changed failure surfaces in the session facts, are the documented symptoms/remedies still the ones the product emits?',
  },
  {
    key: 'product-surfaces',
    skills: [
      'tensor-grep-find-and-route',
      'tensor-grep-config-and-flags',
      'tensor-grep-argv-normalization-and-shadowing',
    ],
    focus:
      'Do these skills document the commands, output fields, and flags that actually exist now, including any the session facts say changed?',
  },
  {
    key: 'ops-dogfood',
    skills: [
      'tensor-grep-workspace-dogfood',
      'tensor-grep-run-and-operate',
      'tensor-grep-enterprise-agent',
    ],
    focus:
      'Do these skills tell a reader to verify on a CLEAN install rather than a maintainer machine, and do their operating instructions still match the session facts?',
  },
]

// New-skill research candidates are SUPPLIED PER RUN (args.research_topics, each
// {key, topic, context}); an empty list skips the Research phase. Candidates hardcoded here
// went stale once the skills they proposed were written, and their "no existing skill covers
// this" context then became false.
const RESEARCH_TOPICS = Array.isArray(args && args.research_topics)
  ? args.research_topics.filter((t) => t && t.key && t.topic)
  : []

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
                .map((s) => `  .claude/skills/${s}/SKILL.md`)
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
    if (RESEARCH_TOPICS.length === 0) {
      log('no research_topics supplied -- Research phase skipped')
      return []
    }
    phase('Research')
    return await pipeline(RESEARCH_TOPICS, (t) =>
      agent(
        `${HOUSE}\n\nRESEARCH TOPIC: ${t.topic}\n\nCONTEXT:\n${t.context || '(none supplied)'}\n\n` +
          `Before researching, check .claude/skills/ for an existing skill that already covers ` +
          `this topic; if one does, say so and default to FOLD_INTO_EXISTING or SKIP.\n\n` +
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
