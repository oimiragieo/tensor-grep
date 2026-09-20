# Agent outcome join (AGT-01, partial)

`benchmarks/agent_outcome_join.py` joins benchmark predictions to observed patch execution.

## Identity

A join is authorized ONLY by the full tuple:
`system_id`, `instance_id`, `repo_commit`, `tool_version`, `model_id`, `budget_id`.

A record missing any field is never completed by guessing. It appears in
`unidentified_predictions` / `unidentified_outcomes` with the exact `missing_fields` list.
Duplicate full identities fail closed on both sides: neither row joins, and the pair is
reported in `duplicate_identities`.

## Command fit is not success

`command_fit` means a validation command was PLANNED. `verified_task_success` requires an
observed passing execution bound to the same identity:

| `execution_observed` | `validation_passed` | `outcome_state` | counted in denominator? |
|---|---|---|---|
| true | true | `passed` | yes |
| true | false | `failed` | yes |
| false | anything | `unavailable` | **no** |

`verified_task_success_rate` divides by `complete_cases` (observed executions only) and is
`null` when that count is zero. Zero evidence is never reported as success.

## Cost

`tokens_in`/`tokens_out`/`elapsed_s` include failed attempts. If any joined row is missing a
cost value, the corresponding total is `null`, not zero.

## Legacy records

`adapt_legacy_bakeoff_row` maps a `run_patch_bakeoff.py` result row (`instance_id` + `system`)
onto the outcome shape. It supplies `system_id` from `system` and nothing else; the record
remains unidentified by design. Legacy readers and existing recall metrics are unchanged --
this module adds a report, it does not modify any existing one.

## Where the report will be emitted

A later AGT-01 slice will embed the report under the top-level `outcome_join` key of the
scorecard artifact written by `build_external_agent_patch_driver_scorecard.py`. No consumer
exists yet: this slice defines and verifies `build_outcome_join_report`, but currently emits its
output nowhere.

## Not covered

This module does NOT close AGT-01. Holdout curation is not implemented here; it remains a
separate follow-up that requires real repository fixtures and known-good patches.
