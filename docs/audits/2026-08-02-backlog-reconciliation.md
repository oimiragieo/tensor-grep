# Backlog reconciliation receipts — 2026-08-02

This is the Task 2 evidence packet for canonical status index `2026-08-02.3`. It records the live
external snapshot separately from the deterministic repository tests: pytest validates the checked-in
facts and grammar, but it never calls GitHub, PyPI, or WSL.

## One-shot external snapshot

Captured at `2026-08-02T23:44:28.0949170-04:00`. Commands were read-only and match the approved
Task-2 command population exactly.

```powershell
git fetch origin main
git rev-parse origin/main
gh pr list --state open --limit 100 --json number,title,isDraft,headRefOid,statusCheckRollup
gh issue list --state open --limit 100 --json number,title,labels,state
gh run list --branch main --workflow ci.yml --limit 3 --json databaseId,status,conclusion,headSha,updatedAt
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

## WSL environment summary (privacy-redacted)

These were bounded diagnostics only. WSL was not restarted or shut down. Hostname, account names,
home paths, and account-scoped Windows paths are replaced with explicit angle-bracket placeholders;
the command results and non-account repository paths are otherwise preserved.

```text
Linux <redacted-host> 6.6.87.2-microsoft-standard-WSL2 x86_64 GNU/Linux
TG_PATH=<wsl-user-home>/.local/bin/tg
tensor-grep 1.102.0
RUST_BINARY=<windows-user>/bin/tg
doctor.platform=linux
doctor.native_tg_binary=<windows-user>/bin/tg
doctor.native_tg_binary_kind=standalone-executable
doctor.search_acceleration_backend=standalone-native-tg
doctor.rust_binary_version="tg 1.102.0"
doctor.rust_binary_version_matches=true
doctor.ast_grep.available=true
doctor.ast_grep.binary=<windows-user>/AppData/Roaming/npm/ast-grep
```

The summary came from `timeout 30 tg --version` and `timeout 30 tg doctor --json`. Both exited 0;
the selected doctor fields above are a privacy-redacted projection of the raw JSON, not inferred
from filenames.

## #89 search path-domain reproduction

Treatment command and exit receipt:

```bash
timeout 30 tg doctor --json >/tmp/tg-doctor.json
DOCTOR_RC=$?
printf 'DOCTOR_RC=%s\n' "$DOCTOR_RC"
RUST_BINARY="$(python3 -c \
  'import json; print(json.load(open("/tmp/tg-doctor.json"))["native_tg_binary"])')"
timeout 60 tg search -F --json -- _run_native_tg_search \
  /mnt/c/dev/projects/tensor-grep/src/tensor_grep/cli/bootstrap.py >/tmp/tg-89.json
WSL_FRONTDOOR_RC=$?
printf 'WSL_FRONTDOOR_RC=%s\n' "$WSL_FRONTDOOR_RC"
cat /tmp/tg-89.json
timeout 60 "$RUST_BINARY" search -F --force-cpu --json -- _run_native_tg_search \
  /mnt/c/dev/projects/tensor-grep/src/tensor_grep/cli/bootstrap.py >/tmp/tg-89-native-raw.json
NATIVE_RAW_RC=$?
printf 'NATIVE_RAW_RC=%s\n' "$NATIVE_RAW_RC"
cat /tmp/tg-89-native-raw.json
```

Recorded exits were `DOCTOR_RC=0`, `WSL_FRONTDOOR_RC=2`, and `NATIVE_RAW_RC=2`; both raw-path search
arms emitted the JSON below. The direct-native arm makes the translated control a one-variable pair.

Raw JSON:

```json
{"detail":"search path does not exist: /mnt/c/dev/projects/tensor-grep/src/tensor_grep/cli/bootstrap.py","error":"path_not_found","ok":false,"version":1}
```

Control arm proving the Linux path exists:

```bash
ls -ld /mnt/c/dev/projects/tensor-grep/src/tensor_grep/cli/bootstrap.py
```

```text
-rwxrwxrwx 1 <redacted-user> <redacted-user> 75128 Jul 31 19:04 /mnt/c/dev/projects/tensor-grep/src/tensor_grep/cli/bootstrap.py
```

Translated-path control:

```bash
WIN_PATH="$(wslpath -w -- /mnt/c/dev/projects/tensor-grep/src/tensor_grep/cli/bootstrap.py)"
timeout 60 "$RUST_BINARY" search -F --force-cpu --json -- _run_native_tg_search "$WIN_PATH" \
  >/tmp/tg-89-native-win.json
TRANSLATED_RC=$?
printf 'TRANSLATED_RC=%s\n' "$TRANSLATED_RC"
cat /tmp/tg-89-native-win.json
```

Recorded exit: `TRANSLATED_RC=0`.

```json
{"version":1,"routing_backend":"NativeCpuBackend","routing_reason":"force_cpu","path":"C:\\dev\\projects\\tensor-grep\\src\\tensor_grep\\cli\\bootstrap.py","total_files":1,"total_matches":2}
```

`tg doctor --json` reported the WSL front door as Linux while selecting a standalone Windows native
binary. Therefore the actionable inference is a
path-domain bridge defect: a valid Linux `/mnt/c/...` search root is delegated unchanged to the Windows
native process, which rejects it as missing. The WSL front door and native binary both reported
`1.102.0`; source/PyPI was one patch newer (`1.102.1`), but the paired components agreed with each other
and the failure shape exactly matches #89. The implementation plan must be amended and re-reviewed with
a red control that exercises this cross-domain route before final closeout.

## #90 scan false-clear reproduction

The Task-2 implementation initially repeated an old “non-reproducing” conclusion without a durable
receipt. The independent audit rejected it. A fresh bounded treatment/control pair then disproved the
premise and moved #90 to `READY`.

The inline rule was exactly:

```yaml
id: import-rule
language: python
rule:
  pattern: import $A
```

Treatment:

```bash
RULE="$(printf '%s\n' 'id: import-rule' 'language: python' 'rule:' '  pattern: import $A')"
timeout 60 "$RUST_BINARY" scan --inline-rules "$RULE" \
  --path /mnt/c/dev/projects/tensor-grep/src/tensor_grep/cli/bootstrap.py --json \
  >/tmp/tg90-inline-raw.json 2>/tmp/tg90-inline-raw.err
RAW_SCAN_RC=$?
printf 'RAW_SCAN_RC=%s\n' "$RAW_SCAN_RC"
```

Recorded exit: `RAW_SCAN_RC=0`.

Raw treatment JSON (whitespace collapsed only):

```json
{"version":1,"schema_version":1,"routing_backend":"AstBackend","routing_reason":"ast-inline-rules-scan","sidecar_used":false,"config_path":"inline-rules","path":"C:\\mnt\\c\\dev\\projects\\tensor-grep\\src\\tensor_grep\\cli\\bootstrap.py","ruleset":null,"language":"python","rule_count":1,"matched_rules":0,"total_matches":0,"backends":["AstGrepWrapperBackend"],"findings":[{"rule_id":"import-rule","language":"python","severity":null,"message":null,"fingerprint":"fc173fa6e962ee930e6fa6f6c3678a8b61ac5b7cbf8331a95addd88e91f3d53b","matches":0,"files":[],"evidence":[],"status":"clear"}]}
```

Stderr contradicted that confidence:

```text
tg: warning: skipped unreadable paths during ast scan: ERROR: C:\mnt\c\dev\projects\tensor-grep\src\tensor_grep\cli\bootstrap.py: The system cannot find the path specified. (os error 3)
```

Control, using the same installed Windows native binary and only translating the filesystem operand:

```bash
WIN_PATH="$(wslpath -w -- /mnt/c/dev/projects/tensor-grep/src/tensor_grep/cli/bootstrap.py)"
timeout 60 "$RUST_BINARY" scan --inline-rules "$RULE" --path "$WIN_PATH" --json \
  >/tmp/tg90-inline-win.json 2>/tmp/tg90-inline-win.err
TRANSLATED_SCAN_RC=$?
printf 'TRANSLATED_SCAN_RC=%s\n' "$TRANSLATED_SCAN_RC"
```

Recorded exit: `TRANSLATED_SCAN_RC=0`.

The control emitted no stderr. Raw control JSON (whitespace collapsed only):

```json
{"version":1,"schema_version":1,"routing_backend":"AstBackend","routing_reason":"ast-inline-rules-scan","sidecar_used":false,"config_path":"inline-rules","path":"C:\\dev\\projects\\tensor-grep\\src\\tensor_grep\\cli\\bootstrap.py","ruleset":null,"language":"python","rule_count":1,"matched_rules":1,"total_matches":6,"backends":["AstGrepWrapperBackend"],"findings":[{"rule_id":"import-rule","language":"python","severity":null,"message":null,"fingerprint":"35218bcc5ae7a8e39c19eec5b20ed511600aee291edfcdb347647d166c865daa","matches":6,"files":["C:\\dev\\projects\\tensor-grep\\src\\tensor_grep\\cli\\bootstrap.py"],"evidence":[{"file":"C:\\dev\\projects\\tensor-grep\\src\\tensor_grep\\cli\\bootstrap.py","match_count":6}],"status":"new"}]}
```

This is both a path-domain routing defect and an honesty defect:
the treatment claimed a clear result over a file its selected backend did not read. PR #571 remains
valid proof for the separate doctor exit-127 half; it does not close this scan half.

## Semantic RED-phase matrix

The canonical skeleton and 19 parser/negative controls passed before any semantic reconciliation.
Each approved semantic node was then run independently against that valid stale skeleton:

| Node | Stale fact | Observed RED |
|---|---|---|
| `test_exit_contract_retirement` | #22 `BLOCKED`, required `RETIRED` | `assert 'BLOCKED' == 'RETIRED'` |
| `test_legacy_agent_id_retirement` | F2 `BLOCKED`, required `RETIRED` | `assert 'BLOCKED' == 'RETIRED'` |
| `test_shipped_receipts` | #109 `BLOCKED`, required PR #605 `SHIPPED` | `assert ('BLOCKED' == 'SHIPPED')` |
| `test_mixed_90_retirement` | #90 `BLOCKED`, plan then expected mixed retirement | `assert 'BLOCKED' == 'RETIRED'` |
| `test_859_is_ready_with_audit_correction` | #859 `BLOCKED`, required `READY` | `assert 'BLOCKED' == 'READY'` |
| `test_program_ownership_and_ready_statuses` | first owner `MCP-SURFACE` `BLOCKED` | `assert 'BLOCKED' == 'READY'` |
| `test_ceo_and_demand_ownership` | #255 absent from exact demand set | set diff reported missing `'#255'` |
| `test_handoff_version_and_current_prose` | handoff `2026-08-01.1`, board `2026-08-02.2` | version equality failed |
| `test_89_reproduced_path_domain_defect_is_ready` | #89 remained `BLOCKED` after the live failure | `assert 'BLOCKED' == 'READY'` |
| `test_mixed_90_reproduction_is_ready` | first reconciliation marked #90 `RETIRED`; the current treatment/control requires `READY` | `assert 'RETIRED' == 'READY'` |

The later #89 and #90 WSL treatment/control receipts supersede only the initial dispositions, not the
first eight REDs. The last two rows are the independent amendment REDs observed before this
correction went green.

## Reconciled dispositions

| ID | Canonical disposition | Evidence / trigger |
|---|---|---|
| #22 | `RETIRED` | Exit `2` means incomplete; GPU fallback is an in-band routing disclosure and does not independently change the complete-search exit. |
| F2 | `RETIRED` | The anonymous-agent sentinel is deliberate backward compatibility; no replacement identity contract exists. |
| #36 | `SHIPPED` | PR #903. |
| #37 | `SHIPPED` | PR #908. |
| #89 | `READY` | Reproduced above; requires an amended and independently re-reviewed TDD task. |
| #90 | `READY` | Doctor honesty shipped in PR #571; current raw-vs-translated scan control proves the portability/false-clear half is a live defect owned by the amended cross-domain task. |
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
- The WSL receipts establish actionability, not fixes. #89/#90 cannot become `SHIPPED` until their
  amended task follows red test, implementation, independent gate, merge, and merged-artifact
  verification.
