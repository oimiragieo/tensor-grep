from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRIAGE_LABELS = {
    "area:agent",
    "area:ast",
    "area:ci",
    "area:cli",
    "area:docs",
    "area:gpu",
    "area:install",
    "area:performance",
    "area:python",
    "area:release",
    "area:rust",
    "area:security",
    "area:windows",
    "benchmark-required",
    "manual-review",
    "needs-info",
    "needs-private-security-review",
    "needs-repro",
    "needs-triage",
    "priority:high",
    "priority:low",
    "priority:medium",
    "security-review",
    "type:bug",
    "type:docs",
    "type:feature",
    "type:question",
}

SECURITY_PATTERNS = (
    r"\bvulnerab(?:ility|le)\b",
    r"\bexploit\b",
    r"\bcve-\d{4}-\d+\b",
    r"\brce\b",
    r"\bremote code execution\b",
    r"\bcommand injection\b",
    r"\bpath traversal\b",
    r"\bcredential(?:s)?\b",
    r"\bsecret(?:s)?\b",
    r"\btoken leak\b",
    r"\bapi[_ -]?key\b",
    r"\bprivate key\b",
)

AREA_KEYWORDS = {
    "area:agent": ("agent", "capsule", "context-render", "edit-plan", "mcp", "llm"),
    "area:ast": ("ast", "structural", "tree-sitter", "ast-grep", "tg run"),
    "area:ci": ("ci", "workflow", "github actions", "action", "check", "job"),
    "area:cli": ("cli", "search", "tg search", "rg", "format", "flag", "option"),
    "area:docs": ("docs", "documentation", "readme", "help text", "--help"),
    "area:gpu": ("gpu", "cuda", "nvidia", "cudf", "nativegpu"),
    "area:install": ("install", "upgrade", "launcher", "shim", "path", "uvx", "pipx"),
    "area:performance": ("performance", "slow", "speed", "benchmark", "regression", "latency"),
    "area:python": ("python", "pypi", "wheel", "maturin", "uv"),
    "area:release": ("release", "pypi", "homebrew", "winget", "asset", "version"),
    "area:rust": ("rust", "cargo", "native", "pyo3"),
    "area:windows": ("windows", "powershell", "cmd", ".cmd", ".ps1", "createprocess"),
}


@dataclass(frozen=True)
class TriageResult:
    labels: list[str]
    comment_body: str


def _issue_text(issue: dict[str, Any]) -> str:
    return f"{issue.get('title') or ''}\n{issue.get('body') or ''}".lower()


def _existing_label_names(issue: dict[str, Any]) -> set[str]:
    labels = issue.get("labels") or []
    names: set[str] = set()
    for label in labels:
        if isinstance(label, dict) and isinstance(label.get("name"), str):
            names.add(label["name"])
        elif isinstance(label, str):
            names.add(label)
    return names


def _checkbox_checked(body: str, phrase: str) -> bool:
    normalized = re.sub(r"\s+", " ", body.lower())
    return f"[x] {phrase.lower()}" in normalized


def _strip_security_confirmation_lines(body: str) -> str:
    return "\n".join(
        line
        for line in body.splitlines()
        if "does not contain an undisclosed vulnerability" not in line.lower()
    )


def _looks_security_sensitive(text: str, body: str) -> bool:
    searchable_text = (
        f"{_strip_security_confirmation_lines(text)}\n"
        f"{_strip_security_confirmation_lines(body).lower()}"
    )
    if any(
        re.search(pattern, searchable_text, flags=re.IGNORECASE) for pattern in SECURITY_PATTERNS
    ):
        return True
    return (
        not _checkbox_checked(
            body,
            "this report does not contain an undisclosed vulnerability, exploit, secret, credential, or private token",
        )
        and not _checkbox_checked(
            body,
            "this request does not contain an undisclosed vulnerability, exploit, secret, credential, or private token",
        )
        and not _checkbox_checked(
            body,
            "this question does not contain an undisclosed vulnerability, exploit, secret, credential, or private token",
        )
        and not _checkbox_checked(
            body,
            "this issue does not contain an undisclosed vulnerability, exploit, secret, credential, or private token",
        )
    )


def _classify_type(text: str, existing_labels: set[str]) -> str:
    if "bug" in existing_labels or text.startswith("bug:"):
        return "type:bug"
    if "enhancement" in existing_labels or text.startswith(("feat:", "perf:")):
        return "type:feature"
    if "documentation" in existing_labels or text.startswith("docs:"):
        return "type:docs"
    if "question" in existing_labels or text.startswith("question:"):
        return "type:question"
    if re.search(r"\b(crash|traceback|exception|error|fails?|broken|regression|wrong)\b", text):
        return "type:bug"
    if re.search(r"\b(feature|request|proposal|support|add|enhancement)\b", text):
        return "type:feature"
    if re.search(r"\b(docs?|documentation|readme|typo)\b", text):
        return "type:docs"
    return "type:question"


def _contains_keyword(text: str, keyword: str) -> bool:
    if re.fullmatch(r"[a-z0-9_-]+", keyword):
        return re.search(rf"(?<![a-z0-9_-]){re.escape(keyword)}(?![a-z0-9_-])", text) is not None
    return keyword in text


def _classify_areas(text: str, type_label: str) -> set[str]:
    areas = {
        label
        for label, keywords in AREA_KEYWORDS.items()
        if any(_contains_keyword(text, keyword) for keyword in keywords)
    }
    if type_label == "type:docs":
        areas.add("area:docs")
    if not areas:
        areas.add("area:cli")
    return areas


def _has_repro(body: str) -> bool:
    lower = body.lower()
    return (
        "### reproduction steps" in lower
        and bool(re.search(r"(?m)^\s*(1\.|-|\*)\s+\S", body))
        and (
            "### command or api call" in lower or "tg " in lower or "python -m tensor_grep" in lower
        )
    )


def _missing_info(type_label: str, body: str) -> list[str]:
    lower = body.lower()
    missing: list[str] = []
    if type_label == "type:bug":
        if "### tensor-grep version" not in lower:
            missing.append("version")
        if not _has_repro(body):
            missing.append("minimal reproduction")
        if "### expected behavior" not in lower:
            missing.append("expected behavior")
        if "### actual behavior" not in lower:
            missing.append("actual behavior")
    elif type_label == "type:feature":
        if "### problem" not in lower:
            missing.append("problem statement")
        if "### proposed solution" not in lower:
            missing.append("proposed solution")
        if "### acceptance criteria" not in lower:
            missing.append("acceptance criteria")
    return missing


def _priority(type_label: str, areas: set[str], security_sensitive: bool, text: str) -> str:
    if security_sensitive:
        return "priority:high"
    if "area:install" in areas or "area:release" in areas:
        return "priority:high"
    if type_label == "type:bug" and re.search(
        r"\b(regression|crash|cannot install|data loss)\b", text
    ):
        return "priority:high"
    if type_label in {"type:docs", "type:question"}:
        return "priority:low"
    return "priority:medium"


def triage_issue(issue: dict[str, Any]) -> TriageResult:
    body = str(issue.get("body") or "")
    text = _issue_text(issue)
    classification_text = _strip_security_confirmation_lines(text)
    existing_labels = _existing_label_names(issue)
    type_label = _classify_type(classification_text, existing_labels)
    areas = _classify_areas(classification_text, type_label)
    security_sensitive = _looks_security_sensitive(text, body)
    missing = _missing_info(type_label, body)

    labels: set[str] = {
        "needs-triage",
        type_label,
        _priority(type_label, areas, security_sensitive, classification_text),
    }
    labels.update(areas)

    if "area:gpu" in areas or "area:performance" in areas:
        labels.add("benchmark-required")
    if type_label == "type:bug" and not _has_repro(body):
        labels.add("needs-repro")
    if missing:
        labels.add("needs-info")
    if security_sensitive:
        labels.update({
            "area:security",
            "manual-review",
            "needs-private-security-review",
            "security-review",
        })

    safe_labels = sorted(label for label in labels if label in TRIAGE_LABELS)
    return TriageResult(
        labels=safe_labels,
        comment_body=_comment_body(
            type_label=type_label,
            areas=areas,
            priority=next(label for label in safe_labels if label.startswith("priority:")),
            missing=missing,
            security_sensitive=security_sensitive,
        ),
    )


def _comment_body(
    *,
    type_label: str,
    areas: set[str],
    priority: str,
    missing: list[str],
    security_sensitive: bool,
) -> str:
    area_names = ", ".join(f"`{area.removeprefix('area:')}`" for area in sorted(areas))
    lines = [
        "## Automated issue triage",
        "",
        "This monitor classified the issue from structured metadata and text signals. It did not run commands, open links, inspect attachments, or execute reporter-provided content.",
        "",
        f"- Type: `{type_label.removeprefix('type:')}`",
        f"- Areas: {area_names}",
        f"- Priority: `{priority.removeprefix('priority:')}`",
    ]

    if security_sensitive:
        lines.extend([
            "",
            "**Security handling:** this public issue appears security-sensitive or did not include the required non-security confirmation. Please move vulnerability details to private vulnerability reporting:",
            "https://github.com/oimiragieo/tensor-grep/security/advisories/new",
            "",
            "Maintainers should avoid discussing exploit details publicly until the report is assessed privately.",
        ])
    elif missing:
        lines.extend([
            "",
            "Reporter action requested:",
            *[f"- Add {item}." for item in missing],
        ])
    else:
        lines.extend([
            "",
            "Maintainer next step: verify the classification, reproduce if this is a bug, and decide whether this is release-bearing.",
        ])

    return "\n".join(lines)


def _load_issue_from_event(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    issue = payload.get("issue")
    if not isinstance(issue, dict):
        raise ValueError("event payload does not contain an issue object")
    return issue


def main() -> int:
    parser = argparse.ArgumentParser(description="Securely classify a GitHub issue event.")
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = triage_issue(_load_issue_from_event(args.event_path))
    args.output.write_text(
        json.dumps({"labels": result.labels, "comment_body": result.comment_body}, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
