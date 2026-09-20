"""Pin the complete, token-free contract of ``ci-cost-os-gate``.

The job only computes the OS JSON consumed by downstream matrix jobs. Its least
privilege contract is therefore structural: an empty permission mapping, no
extra job inputs or steps, and one exact script. A denylist of token spellings
cannot establish that contract because Actions expressions and shell syntax have
an open-ended number of equivalent spellings. The exact deep-equality assertion
below fails closed for any unreviewed addition, including a dynamic token
expression or a new network command.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_MISSING = object()

_WEEKLY_CRON = "17 6 * * 1"
_MONTHLY_CRON = "0 6 1 * *"
_EXPECTED_SCHEDULE_CRONS = [_WEEKLY_CRON, _MONTHLY_CRON]
_EXPECTED_TRIGGER = {
    "push": {"branches": ["main"]},
    "pull_request": {"branches": ["main"]},
    "schedule": [{"cron": _WEEKLY_CRON}, {"cron": _MONTHLY_CRON}],
}
_SCHEDULED_OS_JSON = '["ubuntu-latest","windows-latest","macos-latest"]'
_DEFAULT_OS_JSON = '["ubuntu-latest","windows-latest"]'
_EXPECTED_WORKFLOW_ENV = {
    "CARGO_TERM_COLOR": "always",
    "PYO3_USE_ABI3_FORWARD_COMPATIBILITY": "1",
}
_EXPECTED_WORKFLOW_GLOBALS = {
    "name": "CI",
    "concurrency": {
        "group": "${{ github.workflow }}-${{ github.event_name == 'schedule' && 'schedule' || github.ref }}",
        "cancel-in-progress": "${{ github.ref != 'refs/heads/main' }}",
    },
    "env": _EXPECTED_WORKFLOW_ENV,
}
_YAML_BOOL_TAG = "tag:yaml.org,2002:bool"
_YAML_STR_TAG = "tag:yaml.org,2002:str"

_EXPECTED_STEP_SCRIPT = (
    'if [ "${{ github.event.schedule }}" = "0 6 1 * *" ]; then\n'
    '  echo \'test_os=["ubuntu-latest","windows-latest","macos-latest"]\' >> '
    '"$GITHUB_OUTPUT"\n'
    "else\n"
    '  echo \'test_os=["ubuntu-latest","windows-latest"]\' >> "$GITHUB_OUTPUT"\n'
    "fi\n"
)

_EXPECTED_JOB: dict[str, Any] = {
    "name": "ci-cost-os-gate",
    "runs-on": "ubuntu-latest",
    "permissions": {},
    "outputs": {"test_os": "${{ steps.set.outputs.test_os }}"},
    "steps": [{"id": "set", "run": _EXPECTED_STEP_SCRIPT}],
}


def _assert_raw_yaml_mapping_keys(node: Any, *, root: bool = True) -> None:
    """Reject duplicate/equality-colliding keys before YAML construction."""

    if isinstance(node, MappingNode):
        seen: set[tuple[str, str]] = set()
        for key_node, value_node in node.value:
            assert isinstance(key_node, ScalarNode), (
                "workflow mapping keys must be scalar YAML nodes"
            )
            if root:
                assert key_node.tag in {_YAML_BOOL_TAG, _YAML_STR_TAG}, (
                    f"unexpected root mapping key tag {key_node.tag!r} for {key_node.value!r}"
                )
                if key_node.tag == _YAML_BOOL_TAG:
                    assert key_node.value == "on", (
                        f"unexpected boolean root key {key_node.value!r}; only `on:` is valid"
                    )
                    canonical_key = ("trigger", "on")
                elif key_node.value == "on":
                    canonical_key = ("trigger", "on")
                else:
                    canonical_key = (_YAML_STR_TAG, key_node.value)
            else:
                assert key_node.tag == _YAML_STR_TAG, (
                    f"unexpected mapping key tag {key_node.tag!r} for {key_node.value!r}"
                )
                canonical_key = (_YAML_STR_TAG, key_node.value)
            assert canonical_key not in seen, (
                f"duplicate canonical YAML mapping key {key_node.value!r}"
            )
            seen.add(canonical_key)
            _assert_raw_yaml_mapping_keys(value_node, root=False)
    elif isinstance(node, SequenceNode):
        for item in node.value:
            _assert_raw_yaml_mapping_keys(item, root=False)


def _load_workflow(source: str) -> dict[Any, Any]:
    document = yaml.compose(source)
    assert document is not None, "ci.yml is empty"
    _assert_raw_yaml_mapping_keys(document)
    workflow = yaml.safe_load(source)
    assert isinstance(workflow, dict), "ci.yml top level is not a mapping"
    return workflow


def _workflow() -> dict[Any, Any]:
    return _load_workflow(CI_WORKFLOW.read_text(encoding="utf-8"))


def _cost_gate_job() -> dict[str, Any]:
    workflow = _workflow()
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "ci.yml has no mapping-valued `jobs:` block"
    assert "ci-cost-os-gate" in jobs, "ci-cost-os-gate job disappeared from ci.yml"
    job = jobs["ci-cost-os-gate"]
    assert isinstance(job, dict), "ci-cost-os-gate is not a mapping"
    return job


def _assert_exact_workflow_inheritance(workflow: dict[Any, Any]) -> None:
    """Pin all reviewed workflow globals inherited by every job."""

    globals_only = {
        key: value
        for key, value in workflow.items()
        if not ((type(key) is bool and key is True) or (type(key) is str and key in {"on", "jobs"}))
    }
    assert globals_only == _EXPECTED_WORKFLOW_GLOBALS, (
        "ci.yml workflow-level globals changed; ci-cost-os-gate must inherit only "
        f"the reviewed mapping {_EXPECTED_WORKFLOW_GLOBALS!r}, found {globals_only!r}"
    )


def _assert_exact_cost_gate_job(job: dict[str, Any]) -> None:
    """Reject every unreviewed job change, not just known token spellings."""

    assert job == _EXPECTED_JOB, (
        "ci-cost-os-gate must remain exactly the reviewed token-free job; "
        f"expected {_EXPECTED_JOB!r}, found {job!r}"
    )


def _workflow_trigger_from(workflow: dict[Any, Any]) -> dict[Any, Any]:
    """Return the sole trigger key, rejecting ambiguous YAML representations."""

    present_keys = [
        key
        for key in workflow
        if (type(key) is bool and key is True) or (type(key) is str and key == "on")
    ]
    assert len(present_keys) == 1, (
        "ci.yml must declare exactly one top-level trigger representation, "
        f"`on:` or YAML 1.1's `True`; found {present_keys!r}"
    )
    trigger = workflow[present_keys[0]]
    trigger_error = (
        f"ci.yml top-level trigger under key {present_keys[0]!r} is not a mapping: {trigger!r}"
    )
    assert isinstance(trigger, dict), trigger_error
    return trigger


def _workflow_trigger() -> dict[Any, Any]:
    """Return the unambiguous workflow trigger from the checked-in workflow."""

    return _workflow_trigger_from(_workflow())


def _assert_exact_workflow_trigger(trigger: dict[Any, Any]) -> None:
    assert trigger == _EXPECTED_TRIGGER, (
        "ci.yml top-level trigger mapping drifted; "
        f"expected {_EXPECTED_TRIGGER!r}, found {trigger!r}"
    )


def _schedule_crons(trigger: dict[Any, Any]) -> list[str]:
    """Parse every schedule entry and fail closed on malformed trigger data."""

    entries = trigger.get("schedule", _MISSING)
    assert entries is not _MISSING, "ci.yml declares no top-level `schedule:` block"
    assert isinstance(entries, list), f"ci.yml `schedule:` is not a list: {entries!r}"
    assert entries, "ci.yml `schedule:` list is empty"
    crons: list[str] = []
    for entry in entries:
        assert isinstance(entry, dict), f"ci.yml schedule entry is not a mapping: {entry!r}"
        assert set(entry) == {"cron"}, f"ci.yml schedule entry must contain only `cron:`: {entry!r}"
        cron = entry["cron"]
        assert isinstance(cron, str) and cron.strip(), (
            f"ci.yml `cron:` value is not a non-empty string: {cron!r}"
        )
        crons.append(cron)
    return crons


def _assert_exact_monthly_schedule(trigger: dict[Any, Any]) -> None:
    crons = _schedule_crons(trigger)
    assert crons == _EXPECTED_SCHEDULE_CRONS, (
        "ci.yml must contain exactly the reviewed weekly and monthly schedules "
        f"and no extra cron entries; found {crons!r}"
    )


def test_ci_cost_os_gate_declares_exact_token_free_job() -> None:
    _assert_exact_cost_gate_job(_cost_gate_job())


def test_ci_cost_os_gate_rejects_inherited_env_and_shell_mutations() -> None:
    baseline = _workflow()
    _assert_exact_workflow_inheritance(baseline)

    inherited_token = copy.deepcopy(baseline)
    inherited_token["env"]["GH_TOKEN"] = "${{ github.token }}"
    with pytest.raises(AssertionError):
        _assert_exact_workflow_inheritance(inherited_token)

    custom_shell = copy.deepcopy(baseline)
    custom_shell["defaults"] = {"run": {"shell": "bash -c '{0}'"}}
    with pytest.raises(AssertionError):
        _assert_exact_workflow_inheritance(custom_shell)

    global_permissions = copy.deepcopy(baseline)
    global_permissions["permissions"] = {"write-all": True}
    with pytest.raises(AssertionError):
        _assert_exact_workflow_inheritance(global_permissions)


def test_workflow_trigger_rejects_duplicate_yaml_key_spellings() -> None:
    baseline = _workflow()
    trigger = _workflow_trigger_from(baseline)
    ambiguous = copy.deepcopy(baseline)
    ambiguous[True] = copy.deepcopy(trigger)
    ambiguous["on"] = copy.deepcopy(trigger)

    with pytest.raises(AssertionError):
        _workflow_trigger_from(ambiguous)

    for numeric_key in (1, 1.0):
        numeric = copy.deepcopy(baseline)
        for key in list(numeric):
            if type(key) is bool and key is True:
                del numeric[key]
        numeric[numeric_key] = copy.deepcopy(trigger)
        with pytest.raises(AssertionError):
            _workflow_trigger_from(numeric)


def test_workflow_trigger_rejects_extra_events_and_widened_filters() -> None:
    baseline = _workflow_trigger()
    _assert_exact_workflow_trigger(baseline)

    extra_event = copy.deepcopy(baseline)
    extra_event["workflow_dispatch"] = {}
    with pytest.raises(AssertionError):
        _assert_exact_workflow_trigger(extra_event)

    widened_push = copy.deepcopy(baseline)
    widened_push["push"]["branches"].append("develop")
    with pytest.raises(AssertionError):
        _assert_exact_workflow_trigger(widened_push)


def test_raw_yaml_rejects_duplicate_and_equality_colliding_keys() -> None:
    source = CI_WORKFLOW.read_text(encoding="utf-8")

    duplicate_on = source + '\n"on": {}\n'
    with pytest.raises(AssertionError):
        _load_workflow(duplicate_on)

    uppercase_on = source.replace("\non:\n", "\nON:\n", 1)
    assert uppercase_on != source
    with pytest.raises(AssertionError):
        _load_workflow(uppercase_on)

    numeric_on = source + "\n1: {}\n"
    with pytest.raises(AssertionError):
        _load_workflow(numeric_on)

    duplicate_permissions = source.replace(
        "    permissions: {}\n",
        "    permissions: {}\n    permissions: {}\n",
        1,
    )
    assert duplicate_permissions != source
    with pytest.raises(AssertionError):
        _load_workflow(duplicate_permissions)


def test_schedule_rejects_extra_costly_cron() -> None:
    baseline = _workflow()
    trigger = _workflow_trigger_from(baseline)
    extra_trigger = copy.deepcopy(trigger)
    extra_trigger["schedule"].append({"cron": "*/5 * * * *"})
    with pytest.raises(AssertionError):
        _assert_exact_monthly_schedule(extra_trigger)

    source = CI_WORKFLOW.read_text(encoding="utf-8")
    extra_source = source.replace(
        '    - cron: "0 6 1 * *"\n',
        '    - cron: "0 6 1 * *"\n    - cron: "*/5 * * * *"\n',
        1,
    )
    assert extra_source != source
    extra_workflow = _load_workflow(extra_source)
    with pytest.raises(AssertionError):
        _assert_exact_monthly_schedule(_workflow_trigger_from(extra_workflow))


def test_ci_cost_os_gate_rejects_unreviewed_job_mutations() -> None:
    baseline = _cost_gate_job()

    missing_permissions = copy.deepcopy(baseline)
    del missing_permissions["permissions"]
    with pytest.raises(AssertionError):
        _assert_exact_cost_gate_job(missing_permissions)

    widened_permissions = copy.deepcopy(baseline)
    widened_permissions["permissions"] = {"contents": "read"}
    with pytest.raises(AssertionError):
        _assert_exact_cost_gate_job(widened_permissions)

    dynamic_token_and_curl = copy.deepcopy(baseline)
    dynamic_token_and_curl["env"] = {"CTX": "${{ github[format('to{0}','ken')] }}"}
    dynamic_token_and_curl["steps"][0]["run"] += (
        'curl -H "Authorization: Bearer $CTX" https://api.github.com/user\n'
    )
    with pytest.raises(AssertionError):
        _assert_exact_cost_gate_job(dynamic_token_and_curl)

    shell_reconstructed_token = copy.deepcopy(baseline)
    shell_reconstructed_token["steps"][0]["run"] += (
        "export G'H'_TOKEN=\"$(jq -r .token <<<\"$CTX\")\"\ng'h' api user\n"
    )
    with pytest.raises(AssertionError):
        _assert_exact_cost_gate_job(shell_reconstructed_token)

    extra_step = copy.deepcopy(baseline)
    extra_step["steps"].append({"id": "unexpected", "run": "echo drift"})
    with pytest.raises(AssertionError):
        _assert_exact_cost_gate_job(extra_step)

    script_drift = copy.deepcopy(baseline)
    script_drift["steps"][0]["run"] += "echo drift\n"
    with pytest.raises(AssertionError):
        _assert_exact_cost_gate_job(script_drift)


def test_ci_cost_os_gate_output_and_trigger_contract_is_unchanged() -> None:
    job = _cost_gate_job()
    _assert_exact_cost_gate_job(job)

    trigger = _workflow_trigger()
    _assert_exact_workflow_trigger(trigger)
    _assert_exact_monthly_schedule(trigger)

    script = job["steps"][0]["run"]
    assert script == _EXPECTED_STEP_SCRIPT
    scheduled_branch, separator, default_branch = script.partition("else")
    assert separator, "the schedule comparison lost its else branch"
    assert "github.event.schedule" in scheduled_branch
    assert _MONTHLY_CRON in scheduled_branch
    assert _SCHEDULED_OS_JSON in scheduled_branch
    assert _DEFAULT_OS_JSON not in scheduled_branch
    assert _DEFAULT_OS_JSON in default_branch
    assert _SCHEDULED_OS_JSON not in default_branch
