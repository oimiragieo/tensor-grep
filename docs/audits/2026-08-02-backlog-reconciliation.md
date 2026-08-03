# Backlog reconciliation receipts — 2026-08-02

This is the Task 2 evidence packet for canonical status index `2026-08-02.2`. It records the live
external snapshot separately from the deterministic repository tests: pytest validates the checked-in
facts and grammar, but it never calls GitHub, PyPI, or WSL.

## One-shot external snapshot

Captured at `2026-08-02T23:33:11.2487493-04:00`. Commands were read-only.

```powershell
git fetch origin main
git rev-parse origin/main
gh pr list --state open --json number,title,headRefName,baseRefName
gh issue list --state open --limit 100 --json number,title,labels
gh run list --workflow ci.yml --branch main --limit 3 --json databaseId,status,conclusion,headSha,updatedAt
gh release list --limit 3
```

Raw results:

```text
origin/main=8024125612d5fb42481acde34d94ad39bbaa3c3e
open_prs=[]
open_issues=[{"labels":[{"id":"LA_kwDORYS4K88AAAACiyjF9g","name":"manual-review","description":"Requires maintainer review before action","color":"d93f0b"},{"id":"LA_kwDORYS4K88AAAAClfA1GA","name":"area:performance","description":"Performance or benchmark claim","color":"fbca04"},{"id":"LA_kwDORYS4K88AAAAClfA1LQ","name":"area:cli","description":"CLI or search command behavior","color":"1d76db"},{"id":"LA_kwDORYS4K88AAAAClfA1Rw","name":"area:ast","description":"AST or structural search","color":"5319e7"},{"id":"LA_kwDORYS4K88AAAAClfA1WA","name":"area:install","description":"Install, upgrade, or launcher behavior","color":"d93f0b"},{"id":"LA_kwDORYS4K88AAAAClfA1Xw","name":"benchmark-required","description":"Requires benchmark evidence before claims","color":"fbca04"},{"id":"LA_kwDORYS4K88AAAAClfA1aQ","name":"needs-triage","description":"Awaiting maintainer triage","color":"fbca04"},{"id":"LA_kwDORYS4K88AAAAClfA1gA","name":"priority:medium","description":"Medium-priority report","color":"fbca04"},{"id":"LA_kwDORYS4K88AAAAClfA1kw","name":"type:feature","description":"Feature request","color":"a2eeef"},{"id":"LA_kwDORYS4K88AAAACsMvgPQ","name":"area:security","description":"Security-sensitive triage","color":"d93f0b"},{"id":"LA_kwDORYS4K88AAAACsMvgyA","name":"needs-info","description":"Reporter input is needed","color":"d93f0b"},{"id":"LA_kwDORYS4K88AAAACsMvg4w","name":"needs-private-security-review","description":"Move sensitive details to private security reporting","color":"b60205"},{"id":"LA_kwDORYS4K88AAAACsMvhGQ","name":"priority:high","description":"High-priority report","color":"b60205"},{"id":"LA_kwDORYS4K88AAAACsMvhOw","name":"security-review","description":"Security review required","color":"b60205"}],"number":48,"state":"OPEN","title":"perf: reduce public shim startup overhead against rg and ast-grep baselines"}]
main_ci=[
  {"conclusion":"success","databaseId":30778356638,"headSha":"8024125612d5fb42481acde34d94ad39bbaa3c3e","status":"completed","updatedAt":"2026-08-03T02:34:47Z"},
  {"conclusion":"success","databaseId":30765407062,"headSha":"8fc51f8448cae6261235d30e3164843ee088d460","status":"completed","updatedAt":"2026-08-02T21:13:56Z"},
  {"conclusion":"success","databaseId":30760773925,"headSha":"be6b16fbc728011ebe93b4b87521ef25d17335cc","status":"completed","updatedAt":"2026-08-02T18:45:56Z"}
]
v1.102.1  Latest  v1.102.1  2026-08-02T20:53:03Z
v1.102.0          v1.102.0  2026-08-02T06:03:11Z
v1.101.31         v1.101.31 2026-08-02T05:08:33Z
```

The current-state claim is anchored to the full `origin/main` SHA and exact current run `30778356638`;
re-query before any merge because this snapshot is intentionally not a permanent concurrency gate.

## Merged-PR receipts used by the tracker

The following `gh pr view <number> --json number,title,state,mergedAt,mergeCommit` results were captured
in the same one-shot pass:

```json
{"mergeCommit":{"oid":"fb3291bb3c4ca63181bddc66a41da2d2376470ad"},"mergedAt":"2026-07-13T16:46:47Z","number":571,"state":"MERGED","title":"fix(ast-grep): probe requires exit 0, not just the marker — doctor honesty (#90b)"}
{"mergeCommit":{"oid":"71cd49dd233ef9793de6d1bbdc0a11bcebd4903f"},"mergedAt":"2026-07-15T11:02:23Z","number":605,"state":"MERGED","title":"fix: bound cuda GPU engine implicit-walk to mirror the #105 native ceiling"}
{"mergeCommit":{"oid":"d47938ca41f68c0ee6a5ecb6cddf8fbb22f1ebc8"},"mergedAt":"2026-08-02T14:52:45Z","number":903,"state":"MERGED","title":"docs: audit all 27 skills, fix 4 drifts, and close the CLAUDE.md capture gap"}
{"mergeCommit":{"oid":"d7c4438df8d6a272a590ee0c6d942b069ccb7bda"},"mergedAt":"2026-08-02T17:46:17Z","number":908,"state":"MERGED","title":"test: add a requires_grammar marker and apply it to test_lang_c"}
{"mergeCommit":{"oid":"8024125612d5fb42481acde34d94ad39bbaa3c3e"},"mergedAt":"2026-08-03T02:03:45Z","number":910,"state":"MERGED","title":"docs: restore blocked tracker section and record #904 closure"}
```

| PR | Disposition supported | Merged commit | Merged at / title |
|---:|---|---|---|
| #571 | #90 doctor-honesty half shipped | `fb3291bb3c4ca63181bddc66a41da2d2376470ad` | 2026-07-13 — doctor exit-0 honesty |
| #605 | #109 shipped | `71cd49dd233ef9793de6d1bbdc0a11bcebd4903f` | 2026-07-15 — bound CUDA implicit walk |
| #903 | #36 shipped | `d47938ca41f68c0ee6a5ecb6cddf8fbb22f1ebc8` | 2026-08-02 — audit all 27 topic skills |
| #908 | #37 shipped | `d7c4438df8d6a272a590ee0c6d942b069ccb7bda` | 2026-08-02 — mark the grammar-dependent test |
| #910 | tracker baseline merged | `8024125612d5fb42481acde34d94ad39bbaa3c3e` | 2026-08-03 — restore blocked tracker state |

PR state and merged commit, not release chronology, are the proof used for `SHIPPED`.

## #89 WSL path-domain reproduction

This was a bounded diagnostic only. WSL was not restarted or shut down.

```text
Linux DESKTOP-IFLG1HL 6.6.87.2-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC Thu Jun  5 18:30:46 UTC 2025 x86_64 GNU/Linux
TG_PATH=/home/james/.local/bin/tg
tensor-grep 1.102.0
RUST_BINARY=/mnt/c/Users/oimir/bin/tg
```

Command and exit receipt:

```bash
tg search tensor_grep /mnt/c/dev/projects/tensor-grep/src --json >/tmp/tg-89.json
SEARCH_RC=2
cat /tmp/tg-89.json
```

Raw JSON:

```json
{"detail":"search path does not exist: /mnt/c/dev/projects/tensor-grep/src","error":"path_not_found","ok":false,"version":1}
```

Control arm proving the Linux path exists:

```bash
ls -ld /mnt/c/dev/projects/tensor-grep/src
```

```text
drwxrwxrwx 1 james james 4096 May 24 23:14 /mnt/c/dev/projects/tensor-grep/src
```

`tg doctor --json` reported the WSL Python front door as Linux while selecting the standalone native
binary at `/mnt/c/Users/oimir/bin/tg`, a Windows executable. Therefore the actionable inference is a
path-domain bridge defect: a valid Linux `/mnt/c/...` search root is delegated unchanged to the Windows
native process, which rejects it as missing. The WSL front door and native binary both reported
`1.102.0`; source/PyPI was one patch newer (`1.102.1`), but the paired components agreed with each other
and the failure shape exactly matches #89. The implementation plan must be amended and re-reviewed with
a red control that exercises this cross-domain route before final closeout.

## Reconciled dispositions

| ID | Canonical disposition | Evidence / trigger |
|---|---|---|
| #22 | `RETIRED` | Exit `2` means incomplete; GPU fallback is an in-band routing disclosure and does not independently change the complete-search exit. |
| F2 | `RETIRED` | The anonymous-agent sentinel is deliberate backward compatibility; no replacement identity contract exists. |
| #36 | `SHIPPED` | PR #903. |
| #37 | `SHIPPED` | PR #908. |
| #89 | `READY` | Reproduced above; requires an amended and independently re-reviewed TDD task. |
| #90 | `RETIRED` | Mixed result preserved: doctor honesty shipped in PR #571; the bounded portability half was non-reproducing. |
| #109 | `SHIPPED` | PR #605. |
| #859 | `READY` | The original receipt covered one codemap site, not the secure-writer class population; see the appended correction in the 2026-08-01 audit. |
| RUST-REPLACE-SYMLINK | `DEMAND_GATED` | Public direct-file leaf-symlink behavior needs a concrete threat model or compatibility decision before change. |

The canonical index in `docs/TASK_BOARD.md` is the closed-world machine-readable authority. Historical
rows elsewhere are retained as evidence and do not override it.

## Attribution boundaries

- Main CI success proves the `8024125` merged artifact passed its workflow; it does not prove this
  documentation-only reconciliation before it is merged.
- GitHub release `v1.102.1` and PyPI `1.102.1` are publication facts, not proof for an individual
  backlog disposition.
- The WSL receipt establishes actionability, not a fix. #89 cannot become `SHIPPED` until its amended
  task follows red test, implementation, independent gate, merge, and merged-artifact verification.
