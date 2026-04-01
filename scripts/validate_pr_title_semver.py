from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_TITLE_PATTERN = re.compile(
    r"^(?P<type>feat|fix|perf|refactor|docs|test|build|ci|chore)"
    r"(?:\([^)]+\))?(?P<breaking>!)?: (?P<summary>\S.*)$"
)

_RELEASE_INTENTS = {
    "feat": "minor",
    "fix": "patch",
    "perf": "patch",
    "refactor": "patch",
    "docs": "none",
    "test": "none",
    "build": "none",
    "ci": "none",
    "chore": "none",
}


def _release_intent_for_title(title: str) -> str | None:
    match = _TITLE_PATTERN.match(title.strip())
    if match is None:
        return None
    if match.group("breaking"):
        return "major"
    return _RELEASE_INTENTS[match.group("type")]


def _title_from_event(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        raise ValueError("GITHUB_EVENT_PATH does not contain a pull_request payload")
    title = pull_request.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("pull_request.title is missing from GITHUB_EVENT_PATH")
    return title


def _write_outputs(*, title: str, release_intent: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"pr_title={title}\n")
        handle.write(f"release_intent={release_intent}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate PR titles for semantic-release-compatible version bumps."
    )
    parser.add_argument("--title", help="Explicit PR title to validate.")
    parser.add_argument(
        "--event-path",
        type=Path,
        help="GitHub event payload path. Defaults to GITHUB_EVENT_PATH when omitted.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    title = args.title
    if title is None:
        event_path_value = args.event_path or os.environ.get("GITHUB_EVENT_PATH")
        if event_path_value is None:
            print("No PR title provided. Pass --title or set GITHUB_EVENT_PATH.", file=sys.stderr)
            return 1
        try:
            title = _title_from_event(Path(event_path_value))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Unable to read PR title: {exc}", file=sys.stderr)
            return 1

    release_intent = _release_intent_for_title(title)
    if release_intent is None:
        print(
            "Invalid PR title for semantic release. Use conventional-commit style, for example: "
            "`feat: add context render caching` (minor), "
            "`fix: correct Windows path normalization` (patch), or "
            "`feat!: remove legacy daemon protocol` (major).",
            file=sys.stderr,
        )
        return 1

    print(f"PR title: {title}")
    print(f"release_intent={release_intent}")
    _write_outputs(title=title, release_intent=release_intent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
