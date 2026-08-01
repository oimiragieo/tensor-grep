export const meta = {
  name: 'tg-skill-audit',
  description: 'Re-derive every load-bearing claim in the .claude/skills library against the current tree, and emit a ranked fix queue',
  whenToUse: 'After a high-velocity stretch, before onboarding someone onto the skill library, or whenever a skill has been cited as authority for a decision. Catches the drift class that tests/unit/test_skill_library_drift.py cannot see by design: citations that RESOLVE but point at unrelated code.',
  phases: [
    { title: 'Ledger', detail: 'derive ground-truth facts by RUNNING the commands' },
    { title: 'Audit', detail: 'semantic re-derivation per skill cluster, waves of 3' },
    { title: 'Synthesis', detail: 'dedupe, rank by blast radius, emit fix queue' },
  ],
}

// ---------------------------------------------------------------------------
// WHY A LEDGER PHASE INSTEAD OF CONSTANTS.
// An earlier version of this workflow hardcoded the ground-truth facts into the
// script. That is the very defect it audits for: a number frozen at authoring
// time, trusted by every downstream agent, drifting silently. It also shipped a
// WRONG fact to 11 agents at once (a correct skill count reported as drift),
// which is the map-ledger amplification failure. So the facts are DERIVED at
// run time, by an agent that runs the commands and reports their raw output.
// ---------------------------------------------------------------------------

const LEDGER_SCHEMA = {
  type: 'object',
  required: ['facts', 'raw_output'],
  properties: {
    facts: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'value', 'derivation'],
        properties: {
          name: { type: 'string' },
          value: { type: 'string' },
          derivation: { type: 'string', description: 'the exact command that produced it' },
        },
      },
    },
    raw_output: { type: 'string', description: 'verbatim output of every command run' },
  },
}

const AUDIT_SCHEMA = {
  type: 'object',
  required: ['cluster', 'skills_audited', 'verdict', 'anchors_sampled', 'strongest_verified_claim', 'findings'],
  properties: {
    cluster: { type: 'string' },
    skills_audited: { type: 'array', items: { type: 'string' } },
    verdict: { type: 'string', enum: ['CLEAN', 'DRIFT_FOUND'] },
    anchors_sampled: { type: 'integer' },
    strongest_verified_claim: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['skill', 'location', 'claim', 'actual', 'severity'],
        properties: {
          skill: { type: 'string' },
          location: { type: 'string' },
          claim: { type: 'string' },
          actual: { type: 'string' },
          severity: { type: 'string', enum: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] },
        },
      },
    },
  },
}

const CLUSTERS = [
  { key: 'change-safely', skills: ['tensor-grep-change-control', 'tensor-grep-debugging-playbook', 'tensor-grep-failure-archaeology', 'tensor-grep-validation-and-qa'] },
  { key: 'understand', skills: ['tensor-grep-architecture-contract', 'code-search-and-retrieval-reference', 'tensor-grep-config-and-flags', 'tensor-grep'] },
  { key: 'operate-a', skills: ['tensor-grep-build-and-env', 'tensor-grep-run-and-operate', 'tensor-grep-diagnostics-and-tooling', 'tensor-grep-docs-and-writing', 'tensor-grep-release-and-positioning'] },
  { key: 'operate-b', skills: ['tensor-grep-workspace-dogfood', 'tensor-grep-enterprise-agent', 'tensor-grep-prepare', 'tensor-grep-ledger', 'tensor-grep-find-and-route'] },
  { key: 'operate-c', skills: ['tensor-grep-multi-project-search', 'tensor-grep-enterprise-review-bundle', 'tensor-grep-gpu', 'tensor-grep-add-language', 'tensor-grep-backlog-campaign'] },
  { key: 'advance', skills: ['tensor-grep-semantic-search-campaign', 'tensor-grep-benchmark-and-proof-toolkit', 'tensor-grep-research-frontier', 'tensor-grep-research-methodology', 'tensor-grep-large-repo-scale-campaign'] },
]

const HOUSE = `
HOUSE CONSTRAINTS (verbatim, non-negotiable):
- CPU-SAFE: NEVER run cargo build/test/check/clippy, and NEVER run tests/e2e/test_routing_parity.py
  (it invokes cargo run). This is a shared desktop and CPU-heavy work is forbidden on it.
- READ-ONLY. Report findings; do not edit any file.
- An UNCITED finding is DISCARDED. Every claim needs a file:line or a command plus its output.
- If a skill is accurate, say CLEAN and name the strongest claim you actually verified. A bare
  "no findings" is indistinguishable from not having looked.
- DO NOT propose replacing an old line number with a new line number. AGENTS.md's
  "Cite the SYMBOL, not the line — and never re-stamp" section records FIVE maintenance passes that
  re-stamped by hand and shipped already-wrong anchors every time. Report the drift; the remedy is
  a grep-the-symbol instruction, decided by the orchestrator.
`

// Waves of 3. The cap is in the SCRIPT, not just in guidance, because the author
// (me) is exactly who skips it under time pressure -- and a 21-agent single-shot
// fan-out of this workflow died wholesale on a session token limit, returning
// zeros that read like a clean audit.
async function inWaves(items, size, fn) {
  const out = []
  for (let i = 0; i < items.length; i += size) {
    const chunk = items.slice(i, i + size)
    const res = await parallel(chunk.map((it, j) => () => fn(it, i + j, false)))
    for (let k = 0; k < res.length; k++) {
      if (res[k] == null) {
        log(`HOLE: ${chunk[k].key} returned null -- retrying once, narrower`)
        res[k] = await fn(chunk[k], i + k, true)
        if (res[k] == null) log(`NOT COVERED after retry: ${chunk[k].key}`)
      }
    }
    out.push(...res)
  }
  return out
}

phase('Ledger')
const ledger = await agent(
  `${HOUSE}

TASK: derive ground-truth facts for a skill-library audit by RUNNING these commands in
C:/dev/projects/tensor-grep. Report each command's RAW output. Do not summarise from memory, and do
not answer from any doc -- the docs are the thing being audited.

  1. python -c "import sys;sys.path.insert(0,'src');from tensor_grep.cli import repo_map as r;print(r._symbol_navigation_descriptor())"
  2. grep -c "lang_registry.register_language(" src/tensor_grep/cli/repo_map.py
  3. python -c "import json,urllib.request;print(json.load(urllib.request.urlopen('https://pypi.org/pypi/tensor-grep/json'))['info']['version'])"
  4. tg --version          (report it AND note it may lag PyPI -- say which answered)
  5. ls -1d .claude/skills/*/ | wc -l
  6. grep -oE "^\\*\\*Form [0-9]+" AGENTS.md | sort -u | wc -l
  7. wc -l .github/workflows/ci.yml
  8. python -c "import sys;sys.path.insert(0,'src');from tensor_grep.cli import mcp_server as m;print(m._TG_MCP_SERVER_CONTRACT_VERSION)"

For each, return name, value, and the exact command as its derivation. A fact without its
derivation is not a fact -- downstream agents must be able to re-run it.`,
  { label: 'ledger', phase: 'Ledger', schema: LEDGER_SCHEMA, model: 'haiku' },
)

const LEDGER_TEXT = `
VERIFIED FACTS -- derived live at the start of THIS run by running the commands. Trust these over
any text you read in a skill or doc; the docs are the artifact under audit.
${(ledger?.facts || []).map((f) => `- ${f.name} = ${f.value}\n    derivation: ${f.derivation}`).join('\n')}

RAW OUTPUT:
${ledger?.raw_output || '(ledger phase returned nothing -- treat every fact below as UNVERIFIED and say so)'}

NOTE: a mechanical gate (tests/unit/test_skill_library_drift.py) already proves every citation
RESOLVES -- the file exists and the line is in range. Do NOT re-check existence. Your job is the
half that gate cannot see: whether the cited line still CONTAINS what the skill claims. Drift of
14-500 lines with the citation still resolving is the dominant defect in this library.
`

phase('Audit')
const audits = await inWaves(CLUSTERS, 3, (c, _i, isRetry) =>
  agent(
    `${LEDGER_TEXT}
${HOUSE}

TASK: audit these skills for DRIFT against the current tree. Cluster "${c.key}":
${c.skills.map((s) => `  - .claude/skills/${s}/SKILL.md (+ any REFERENCE.md beside it)`).join('\n')}

For every LOAD-BEARING claim, RE-DERIVE it:
  * line ANCHOR      -> grep the claimed SYMBOL and compare to the cited line. Highest-yield check.
  * number / count   -> run the command that produces it
  * flag / subcommand-> confirm it exists in the CLI definition. A documented flag that does not
                        exist is CRITICAL -- someone will paste the command.
  * env var          -> confirm something actually READS it
  * contract claim   -> find the implementing code and read it
  * narrative        -> a stale "this is broken" is worse than a stale line number; flag anything
                        the current code contradicts
${isRetry ? '\nRETRY: your prior attempt returned nothing. Narrow to the 2 highest-risk skills and RETURN A RESULT.\n' : ''}
Report only drift you MEASURED, with the skill file:line of the wrong text and the repo evidence.`,
    { label: `audit:${c.key}`, phase: 'Audit', schema: AUDIT_SCHEMA, model: 'sonnet' },
  ),
)

const covered = audits.filter(Boolean)
const missing = CLUSTERS.length - covered.length
if (missing > 0) log(`NOT COVERED: ${missing} of ${CLUSTERS.length} clusters returned nothing after retry`)

phase('Synthesis')
const findings = covered.flatMap((a) => (a.findings || []).map((f) => ({ ...f, cluster: a.cluster })))
log(`${findings.length} findings across ${covered.length}/${CLUSTERS.length} clusters`)

const plan = await agent(
  `${LEDGER_TEXT}

You are the chairman. Fold these into ONE ranked fix queue.

AUDITS (${covered.length}/${CLUSTERS.length} clusters returned):
${JSON.stringify(covered, null, 1)}

RULES:
- ${missing} cluster(s) returned NOTHING. Do not fabricate results for them; open with an explicit
  PAYLOAD SHORTFALL line naming each. A zero from a lane that never ran is not a clean lane.
- DISCARD any finding whose evidence is not a file:line or a command+output. Report how many you cut.
- DEDUPE across clusters: the same drift found twice is ONE item.
- RANK BY BLAST RADIUS: a wrong FACT a future session would act on (a flag that does not exist, a
  language listed as unsupported that ships, a contract stated backwards) outranks a stale line
  number by a wide margin. Line drift is noise until it changes a decision.
- The remedy for a drifted anchor is a grep-the-symbol instruction plus a "was N -> now M" receipt,
  NEVER a new line number. Say so per item.
- Close with: (a) which findings a deterministic gate could catch, (b) which needed a reading agent,
  and (c) the single highest-leverage change to make the library more self-defending.`,
  { label: 'chairman', phase: 'Synthesis', model: 'opus' },
)

return {
  clusters_dispatched: CLUSTERS.length,
  clusters_covered: covered.length,
  not_covered: missing,
  total_findings: findings.length,
  ledger,
  audits: covered,
  plan,
}
