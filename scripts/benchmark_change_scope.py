import json
import os
import re
import subprocess
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

_CI = ".github/workflows/ci.yml"
_SELF = "scripts/benchmark_change_scope.py"
_GUARD = "needs.changes.outputs.benchmark == 'true'"
_MAX_BYTES = 2_000_000


def _git(root: Path, *args: str) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 and len(result.stdout) <= _MAX_BYTES else None


def _without_scope_guard(value: object) -> object:
    if not isinstance(value, str):
        return value
    if value.strip() == _GUARD:
        return None
    if re.fullmatch(
        rf"\s*{re.escape(_GUARD)}\s*&&\s*\(github.event_name != 'schedule'\)\s*", value
    ):
        return "github.event_name != 'schedule'"
    return value


def _workflow_view(raw: bytes) -> tuple[dict, dict] | None:
    if yaml is None:
        return None
    try:
        document = yaml.safe_load(raw)
        jobs = document["jobs"]
        job = jobs["benchmark-regression"]
        if (
            not isinstance(document, dict)
            or not isinstance(jobs, dict)
            or not isinstance(job, dict)
        ):
            return None
        globals_ = {key: value for key, value in document.items() if key != "jobs"}
        job.pop("if", None)
        steps = job.get("steps", [])
        if not isinstance(steps, list) or not steps:
            return None
        for step in steps:
            if not isinstance(step, dict):
                return None
            if step.get("name") == "Upload benchmark artifacts":
                step.pop("if", None)
            elif "if" in step:
                guardless = _without_scope_guard(step["if"])
                if guardless is None:
                    step.pop("if")
                else:
                    step["if"] = guardless
        return globals_, job
    except (yaml.YAMLError, KeyError, TypeError, ValueError):
        return None


def _valid_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) in (40, 64)
        and bool(re.fullmatch(r"[0-9a-fA-F]+", value))
        and value.strip("0") != ""
    )


def _decision() -> bool:
    try:
        root = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
        event_path = Path(os.environ["GITHUB_EVENT_PATH"])
        if not event_path.is_absolute():
            event_path = root / event_path
        with event_path.open("rb") as event_file:
            raw_event = event_file.read(65_537)
        if not root.is_dir() or len(raw_event) > 65_536 or yaml is None:
            return True
        event = json.loads(raw_event)
        if os.environ["GITHUB_EVENT_NAME"] == "pull_request":
            payload = event["pull_request"]
            base, head, separator = payload["base"]["sha"], payload["head"]["sha"], "..."
        elif os.environ["GITHUB_EVENT_NAME"] == "push":
            base, head, separator = event["before"], event["after"], ".."
        else:
            return True
        if not _valid_sha(base) or not _valid_sha(head) or base == head:
            return True
        revision = f"{base}{separator}{head}"
        output = _git(
            root,
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            revision,
            "--",
        )
        if not output:
            return True
        paths = output.decode("utf-8").rstrip("\0").split("\0")
        if _CI in paths:
            sizes = [_git(root, "cat-file", "-s", f"{sha}:{_CI}") for sha in (base, head)]
            if any(
                size is None or not size.strip().isdigit() or int(size) > _MAX_BYTES
                for size in sizes
            ):
                return True
            old = _git(root, "show", f"{base}:{_CI}")
            new = _git(root, "show", f"{head}:{_CI}")
            old_view = _workflow_view(old) if old is not None else None
            new_view = _workflow_view(new) if new is not None else None
            if old_view is None or new_view is None or old_view != new_view:
                return True
        return any(
            path
            not in {
                _SELF,
                _CI,
            }
            and not (
                path.startswith("tests/unit/test_")
                and path.count("/") == 2
                and path.endswith(".py")
            )
            and not (path.endswith(".md") and (path.startswith("docs/") or "/" not in path))
            for path in paths
        )
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        return True


if __name__ == "__main__":
    print("true" if _decision() else "false")
