"""Release publisher security contracts."""

from __future__ import annotations

import re

import yaml

from .helpers import _normalize_pinned_actions

_AUTHORITATIVE_PUBLISHER = ".github/workflows/ci.yml"
_PUBLISH_ACTIONS = (
    "softprops/action-gh-release",
    "pypa/gh-action-pypi-publish",
    "python-semantic-release/python-semantic-release",
)
_PUBLISH_COMMAND = re.compile(
    r"(?m)^\s*(?:gh\s+release\s+(?:create|upload)\b|npm\s+publish\b|"
    r"(?:twine|maturin)\s+(?:upload|publish)\b)"
)


def validate_release_publisher_census(*, workflows: dict[str, str]) -> list[str]:
    """Require every registered release publisher to live in authoritative CI."""
    errors: list[str] = []
    publishers: dict[str, set[str]] = {}
    for filename, content in workflows.items():
        try:
            parsed = yaml.safe_load(_normalize_pinned_actions(content)) or {}
        except yaml.YAMLError as exc:
            errors.append(f"Workflow publisher census cannot parse {filename}: {exc}")
            continue
        jobs = parsed.get("jobs", {}) if isinstance(parsed, dict) else {}
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps", [])
            if not isinstance(steps, list):
                errors.append(
                    f"Workflow publisher census requires {filename}:{job_name} steps to be a list"
                )
                continue
            for step in steps:
                if not isinstance(step, dict):
                    continue
                action = str(step.get("uses", ""))
                run = "\n".join(
                    line
                    for line in str(step.get("run", "")).splitlines()
                    if not line.lstrip().startswith("#")
                )
                if any(action.startswith(prefix + "@") for prefix in _PUBLISH_ACTIONS) or (
                    _PUBLISH_COMMAND.search(run)
                ):
                    publishers.setdefault(filename, set()).add(str(job_name))

    if _AUTHORITATIVE_PUBLISHER not in publishers:
        errors.append("Workflow publisher census did not find the authoritative CI publisher")
    for filename in sorted(set(publishers) - {_AUTHORITATIVE_PUBLISHER}):
        jobs = ", ".join(sorted(publishers[filename]))
        errors.append(
            "Release publishing is only allowed in .github/workflows/ci.yml; "
            f"found {filename}:{jobs}"
        )
    return errors
