"""Agent edit-order control for uncertain import evidence."""

from pathlib import Path

import pytest

from tensor_grep.cli import agent_capsule
from tensor_grep.cli import repo_map as rm


def _write(path: Path, source: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return str(path)


@pytest.mark.requires_grammar
def test_agent_capsule_excludes_uncertain_import_from_edit_order(tmp_path: Path) -> None:
    target = _write(tmp_path / "pkg/a.js", "export function unique_target() { return 1; }\n")
    middle = _write(
        tmp_path / "middle.js",
        'import { unique_target } from "./pkg/a";\n'
        "export function middle() { return unique_target(); }\n",
    )
    uncertain = _write(
        tmp_path / "uncertain.js",
        'import { unique_target } from "a";\nexport function unrelated() { return 2; }\n',
    )
    repo = rm.build_repo_map(tmp_path)
    context = rm.build_context_pack_from_map(repo, "unique_target")
    uncertain_row = next(row for row in context["file_matches"] if row["path"] == uncertain)
    assert "heuristic-import" in uncertain_row["reasons"]

    capsule = agent_capsule.build_agent_capsule_from_map(
        repo,
        "unique_target",
        max_files=10,
        max_sources=10,
        max_tokens=8000,
        include_blast_radius=False,
    )

    assert capsule["primary_target"]["file"] == target
    assert middle in capsule["edit_order"]
    assert uncertain not in capsule["edit_order"]
