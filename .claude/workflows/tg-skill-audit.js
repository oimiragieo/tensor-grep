export const meta = {
  name: 'tg-skill-audit',
  description: 'Re-derive every load-bearing claim in the .claude/skills library against the current tree, and emit a ranked fix queue',
  whenToUse: 'After a high-velocity stretch, before onboarding someone onto the skill library, or whenever a skill has been cited as authority for a decision. Catches the drift class that tests/unit/test_skill_library_drift.py cannot see by design: citations that RESOLVE but point at unrelated code.',
  phases: [
    { title: 'Ledger', detail: 'derive ground-truth facts AND artifact identity by RUNNING the commands' },
    { title: 'Audit', detail: 'semantic re-derivation per skill cluster, waves of 3' },
    { title: 'Synthesis', detail: 'exact coverage equality, dedupe, rank by blast radius, emit fix queue' },
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
//
// WHY ARTIFACT IDENTITY (2026-08-12 retention audit, findings H1-H3).
// The previous revision hardcoded a repo root, recorded no SHA or blob
// population, and credited ANY truthy cluster response as full coverage -- so
// it could audit the wrong checkout (split oracle) and report 6/6 clusters
// covered when members were silently omitted. The ledger now captures the
// resolved root, HEAD SHA, cleanliness, and a path+blob-OID manifest of every
// tracked skill file; synthesis requires EXACT set equality between the
// expected population and the union of reported `skills_audited`, retries
// omitted skills individually, and treats null/evidence-free lanes as
// CANNOT_VERIFY -- never as clean.
// ---------------------------------------------------------------------------

const LEDGER_SCHEMA = {
  type: 'object',
  required: ['repo_root', 'head_sha', 'git_status', 'skill_manifest', 'facts', 'raw_output'],
  properties: {
    repo_root: { type: 'string', description: 'output of: git rev-parse --show-toplevel' },
    head_sha: { type: 'string', description: 'output of: git rev-parse HEAD' },
    git_status: { type: 'string', description: 'output of: git status --porcelain (empty = clean)' },
    skill_manifest: {
      type: 'array',
      description: 'one row per tracked file under .claude/skills/',
      items: {
        type: 'object',
        required: ['path', 'blob_oid'],
        properties: {
          path: { type: 'string' },
          blob_oid: { type: 'string' },
        },
      },
    },
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
    skills_audited: {
      type: 'array',
      description: 'folder names actually audited; exact equality with the assigned list is checked downstream',
      items: { type: 'string' },
    },
    verdict: { type: 'string', enum: ['CLEAN', 'DRIFT_FOUND', 'CANNOT_VERIFY'] },
    anchors_sampled: { type: 'integer', description: 'claims re-derived against the tree; CLEAN with 0 is invalid' },
    strongest_verified_claim: { type: 'string', description: 'strongest claim actually re-derived + its evidence; never empty' },
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
  { key: 'change-safely', skills: ['tensor-grep-change-control', 'tensor-grep-debugging-playbook', 'tensor-grep-failure-archaeology', 'tensor-grep-validation-and-qa', 'tensor-grep-hermetic-hostile-tests', 'tensor-grep-cross-platform-path-confinement', 'tensor-grep-release-drift-check'] },
  { key: 'understand', skills: ['tensor-grep-architecture-contract', 'code-search-and-retrieval-reference', 'tensor-grep-config-and-flags', 'tensor-grep-argv-normalization-and-shadowing', 'tensor-grep-index-fingerprint-freshness', 'tensor-grep'] },
  { key: 'operate-a', skills: ['tensor-grep-build-and-env', 'tensor-grep-run-and-operate', 'tensor-grep-diagnostics-and-tooling', 'tensor-grep-docs-and-writing', 'tensor-grep-release-and-positioning'] },
  { key: 'operate-b', skills: ['tensor-grep-workspace-dogfood', 'tensor-grep-enterprise-agent', 'tensor-grep-prepare', 'tensor-grep-ledger', 'tensor-grep-find-and-route'] },
  { key: 'operate-c', skills: ['tensor-grep-multi-project-search', 'tensor-grep-enterprise-review-bundle', 'tensor-grep-gpu', 'tensor-grep-add-language', 'tensor-grep-backlog-campaign', 'tensor-grep-codex-gated-audit-loop'] },
  { key: 'advance', skills: ['tensor-grep-semantic-search-campaign', 'tensor-grep-benchmark-and-proof-toolkit', 'tensor-grep-research-frontier', 'tensor-grep-research-methodology', 'tensor-grep-large-repo-scale-campaign', 'tensor-grep-worldclass-roadmap'] },
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

TASK: derive ARTIFACT IDENTITY and ground-truth facts for a skill-library audit by RUNNING these
commands in the repository checkout you are invoked in. Report each command's RAW output. Do not
summarise from memory, and do not answer from any doc -- the docs are the thing being audited.

  0a. git rev-parse --show-toplevel      (repo_root)
  0b. git rev-parse HEAD                 (head_sha)
  0c. git status --porcelain             (git_status; empty string = clean)
  0d. git ls-files -s -- .claude/skills/ (skill_manifest: path + blob OID for EVERY tracked file)
  1. python -c "import sys;sys.path.insert(0,'src');from tensor_grep.cli import repo_map as r;print(r._symbol_navigation_descriptor())"
  2. grep -c "lang_registry.register_language(" src/tensor_grep/cli/repo_map.py
  3. python -c "import json,urllib.request;print(json.load(urllib.request.urlopen('https://pypi.org/pypi/tensor-grep/json'))['info']['version'])"
  4. tg --version          (report it AND note it may lag PyPI -- say which answered)
  5. ls -1d .claude/skills/*/ | wc -l
  6. grep -oE "^\\*\\*Form [0-9]+" AGENTS.md | sort -u | wc -l
  7. wc -l .github/workflows/ci.yml
  8. python -c "import sys;sys.path.insert(0,'src');from tensor_grep.cli import mcp_server as m;print(m._TG_MCP_SERVER_CONTRACT_VERSION)"

For each, return name, value, and the exact command as its derivation. A fact without its
derivation is not a fact -- downstream agents must be able to re-run it. If the tree is dirty,
list every dirty path in raw_output; a dirty audit target must be declared in the final receipt.`,
  { label: 'ledger', phase: 'Ledger', schema: LEDGER_SCHEMA, model: 'haiku' },
)

// ---------------------------------------------------------------------------
// DYNAMIC SKILL ENUMERATION (2026-08-14, W6 retention wave).
// CLUSTERS above is frozen at authoring time; a skill folder added after it
// (for example tensor-grep-demand-gate-measurement, created by a sibling
// agent) would be silently unaudited -- the stale-list defect class A99 exists
// to prevent. The ledger already records EVERY tracked file under
// .claude/skills/ with a blob OID, so the closed-world folder set is derived
// from that manifest, and any folder the clusters do not name is dispatched as
// an automatic catch-all cluster. A new skill is therefore covered without
// editing this file.
// ---------------------------------------------------------------------------
const SKILLS_PREFIX = '.claude/skills/'
const manifestFolders = [
  ...new Set(
    (ledger?.skill_manifest || [])
      .map((row) => (row && row.path) || '')
      .filter((p) => p.startsWith(SKILLS_PREFIX))
      .map((p) => p.slice(SKILLS_PREFIX.length).split('/')[0])
      .filter((name) => name && !name.includes('.')),
  ),
]
const clusteredFolders = new Set(CLUSTERS.flatMap((c) => c.skills))
const unassigned = manifestFolders.filter((f) => !clusteredFolders.has(f)).sort()
const clusters =
  unassigned.length > 0
    ? [...CLUSTERS, { key: 'unassigned-auto', skills: unassigned }]
    : CLUSTERS
if (unassigned.length > 0) {
  log(
    `DYNAMIC COVERAGE: ${unassigned.length} skill folder(s) absent from the static cluster map; auto-dispatched as cluster "unassigned-auto": ${unassigned.join(', ')}`,
  )
}

const LEDGER_TEXT = `
AUDITED ARTIFACT (every claim below is bound to THIS tree; if you find yourself reading any
other checkout, STOP and return verdict CANNOT_VERIFY):
  repo_root = ${ledger?.repo_root || '(missing -- treat identity as UNVERIFIED)'}
  head_sha  = ${ledger?.head_sha || '(missing)'}
  git_status = ${ledger?.git_status || '(missing)'}
  skill_manifest = ${(ledger?.skill_manifest || []).length} tracked files under .claude/skills/

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
const audits = await inWaves(clusters, 3, (c, _i, isRetry) =>
  agent(
    `${LEDGER_TEXT}
${HOUSE}

TASK: audit these skills for DRIFT against the audited tree named above. Cluster "${c.key}":
${c.skills.map((s) => `  - .claude/skills/${s}/SKILL.md (+ any REFERENCE.md beside it)`).join('\n')}

skills_audited is checked for EXACT equality with the list above: report a skill ONLY if you
actually re-derived its claims in the audited tree; omitting one you could not finish is honest,
padding the list is a coverage fraud. CLEAN requires anchors_sampled >= 1 and a nonempty
strongest_verified_claim; otherwise return CANNOT_VERIFY.

For every LOAD-BEARING claim, RE-DERIVE it:
  * line ANCHOR      -> grep the claimed SYMBOL and compare to the cited line. Highest-yield check.
  * number / count   -> run the command that produces it
  * flag / subcommand-> confirm it exists in the CLI definition. A documented flag that does not
                        exist is CRITICAL -- someone will paste the command.
  * env var          -> confirm something actually READS it
  * contract claim   -> find the implementing code and read it
  * narrative        -> a stale "this is broken" is worse than a stale line number; flag anything
                        the current code contradicts

ALSO CHECK THE FILE AGAINST ITSELF -- no gate we own does this, and it is how the 8th
self-contradiction in this repo shipped (2026-08-02):
  * SELF-CONTRADICTION -> does this file assert a claim AND its refutation? The tell is a
                        correction that landed at ONE site while a duplicate 100+ lines away kept
                        the refuted version. Grep the file for every anchor/number it states MORE
                        THAN ONCE and compare the copies to each other, not only to the tree.
  * RE-STAMP SMELL     -> text of the form "was :X, now :Y" is the re-stamping anti-pattern, not a
                        fix. Verify Y; if it is wrong, report it AND recommend deleting the number
                        rather than stamping Z.
${isRetry ? '\nRETRY: your prior attempt returned nothing. Narrow to the UNCOVERED skills only, list exactly those in skills_audited, and RETURN A RESULT.\n' : ''}
Report only drift you MEASURED, with the skill file:line of the wrong text and the repo evidence.`,
    { label: `audit:${c.key}`, phase: 'Audit', schema: AUDIT_SCHEMA, model: 'sonnet' },
  ),
)

// EXACT COVERAGE EQUALITY (2026-08-12 retention audit, finding H2): a truthy cluster response is
// NOT coverage. The union of skills_audited must equal the expected population -- no omissions,
// duplicates, or extras. Omitted skills are retried individually once; anything still missing is
// CANNOT_VERIFY and stays visible in the receipt.
const expected = clusters.flatMap((c) => c.skills)
const auditedSet = new Set()
for (const a of audits) {
  if (a == null) continue
  for (const s of a.skills_audited || []) auditedSet.add(s)
}
const missing = expected.filter((s) => !auditedSet.has(s))
const extras = [...auditedSet].filter((s) => !expected.includes(s))

if (missing.length > 0) {
  log(`COVERAGE HOLE: retrying ${missing.length} omitted skills individually: ${missing.join(', ')}`)
  const retries = await inWaves(
    missing.map((s) => ({ key: `retry:${s}`, skills: [s] })),
    3,
    (u) =>
      agent(
        `${LEDGER_TEXT}
${HOUSE}

TASK: single-skill salvage audit of .claude/skills/${u.skills[0]}/SKILL.md (+ any REFERENCE.md
beside it) against the audited tree. The cluster pass did not finish this skill; report ONLY this
one in skills_audited. Same re-derivation rules, same self-contradiction check, same no-re-stamp
rule. CLEAN requires anchors_sampled >= 1 and a nonempty strongest_verified_claim.`,
        { label: `salvage:${u.skills[0]}`, phase: 'Audit', schema: AUDIT_SCHEMA, model: 'sonnet' },
      ),
  )
  for (const r of retries) {
    if (r != null) {
      audits.push(r)
      for (const s of r.skills_audited || []) auditedSet.add(s)
    }
  }
}

const finalMissing = expected.filter((s) => !auditedSet.has(s))
const covered = audits.filter(Boolean)
const evidenceFree = covered.filter(
  (a) => a.verdict === 'CLEAN' && ((a.anchors_sampled || 0) < 1 || !(a.strongest_verified_claim || '').trim()),
)
for (const a of evidenceFree) log(`EVIDENCE GAP: cluster ${a.cluster} claimed CLEAN with no sampled evidence -- treated as CANNOT_VERIFY`)
if (finalMissing.length > 0) log(`NOT COVERED: ${finalMissing.join(', ')} -- reported as CANNOT_VERIFY, never clean`)
if (extras.length > 0) log(`UNEXPECTED skills reported outside the manifest: ${extras.join(', ')}`)

phase('Synthesis')
const findings = covered.flatMap((a) => (a.findings || []).map((f) => ({ ...f, cluster: a.cluster })))
log(`${findings.length} findings across ${covered.length} payloads; coverage ${expected.length - finalMissing.length}/${expected.length} skills exact`)

const plan = await agent(
  `${LEDGER_TEXT}

You are the chairman. Fold these into ONE ranked fix queue.

AUDITS (${covered.length} payloads returned):
${JSON.stringify(covered, null, 1)}

COVERAGE LEDGER (authoritative -- do not contradict it):
  expected population: ${expected.length} skills
  audited: ${expected.length - finalMissing.length}
  NOT COVERED (CANNOT_VERIFY): ${finalMissing.length ? finalMissing.join(', ') : 'none'}
  unexpected extras: ${extras.length ? extras.join(', ') : 'none'}
  evidence-free CLEAN payloads: ${evidenceFree.length ? evidenceFree.map((a) => a.cluster).join(', ') : 'none'}

RULES:
- If anything is NOT COVERED or evidence-free, open with an explicit PAYLOAD SHORTFALL line naming
  each item. Do not fabricate results for them; a zero from a lane that never ran is not clean.
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
  repo_root: ledger?.repo_root || null,
  head_sha: ledger?.head_sha || null,
  git_status: ledger?.git_status || null,
  skill_manifest_entries: (ledger?.skill_manifest || []).length,
  clusters_dispatched: clusters.length,
  skills_expected: expected.length,
  skills_audited: expected.length - finalMissing.length,
  not_covered: finalMissing,
  unexpected_skills: extras,
  dynamic_skills: unassigned,
  coverage_exact: finalMissing.length === 0 && extras.length === 0 && evidenceFree.length === 0,
  total_findings: findings.length,
  ledger,
  audits: covered,
  plan,
}
