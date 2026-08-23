# cursor-agent task: write a junior-analyst onboarding guide for scripts/ci-local/

You are running as `agent --yolo -p` on Windows against the `feat/ci-local-docker-harness` branch
of `C:\dev\projects\tensor-grep`.

## Objective

Create ONE new file, `docs/ONBOARDING_CI_LOCAL.md`, that lets a junior analyst who has NEVER seen
this repo run the local CI harness, understand why each piece exists, and know what it does NOT
prove. Assume they know git and Docker basics and nothing else about this project.

## Inputs (READ-ONLY — read these, do not edit them)

- `C:\dev\projects\tensor-grep\scripts\ci-local\Dockerfile`
- `C:\dev\projects\tensor-grep\scripts\ci-local\entrypoint.sh`
- `C:\dev\projects\tensor-grep\scripts\ci-local\run.sh`
- `C:\dev\projects\tensor-grep\tests\unit\test_ci_local_harness_parity.py`
- `C:\dev\projects\tensor-grep\.claude\skills\tensor-grep-local-ci-parity-harness\SKILL.md`

Every factual claim in your output MUST come from those five files. If you cannot find something
there, write `TODO(verify):` and the question — do NOT invent it.

## Edits required — create exactly ONE file

`docs/ONBOARDING_CI_LOCAL.md` with these H2 sections IN THIS ORDER:

1. `## What this is, in one paragraph`
   Why a local harness exists at all: this dev box is a SHARED SERVER and AGENTS.md forbids local
   `cargo test` on it. State the cost motive (GitHub Actions minutes) plainly.

2. `## Run it`
   The three commands from `run.sh` usage, plus the `TG_CI_CPUS` override. Say what the default
   CPU cap is (read it from run.sh, do not guess).

3. `## What a green run does NOT mean`
   Read the `NOT_COVERED` heredoc in `entrypoint.sh` and reproduce that list faithfully. Lead the
   section with the sentence: "The GitHub Actions run remains the merge arbiter."

4. `## The twelve divergences, and why each one matters`
   Reproduce the trap/tell/fix table from the SKILL.md. For EACH row add one sentence a junior
   would need: what the WRONG VERDICT looked like (green-when-should-be-red, or red-when-CI-green).

5. `## The anti-drift gate`
   Explain what `test_ci_local_harness_parity.py` pins and why a second CI definition needs one.
   State explicitly that passing does NOT mean the harness matches CI — only that the mirrored
   strings agree. Explain the positive and negative controls in that file and why a test without
   them proves nothing.

6. `## When to use act instead`
   Reproduce the decision table from the SKILL.md, including act's OWN documented limits
   (intentionally-incomplete default images; ~60GB faithful images; the maintenance concern).
   Do not editorialise beyond what the SKILL.md says.

7. `## If it breaks: first five things to check`
   A numbered checklist derived ONLY from the divergences: verify the image actually rebuilt
   (`docker inspect --format '{{.Config.User}}'`), confirm you are non-root, check volume
   ownership, check tmpfs `exec`, check `git config --global --add safe.directory`.

## Constraints

- **EDIT FILES ONLY — do NO git at all.** No add, commit, branch, checkout, stash, reset, push,
  no `gh`. The orchestrator does all git.
- Create ONLY `docs/ONBOARDING_CI_LOCAL.md`. Do not modify any other file.
- ASCII only. No emoji. Match the plain, factual tone of the SKILL.md.
- Do NOT invent file paths, flags, version numbers, or commands. Every command must appear in one
  of the five input files.
- If a required fact is missing from the inputs, write `TODO(verify): <question>` inline.

## Done criteria

- `git status --porcelain` shows exactly one new untracked file: `docs/ONBOARDING_CI_LOCAL.md`
- The file has all 7 H2 sections in the order above
- The file contains the literal sentence "The GitHub Actions run remains the merge arbiter."
- No other file is modified

## Final report

Print: `[cursor-agent] DONE file=docs/ONBOARDING_CI_LOCAL.md sections=7 ready_for_audit`
Then exit. Do NOT git add, commit, or push.
