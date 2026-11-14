"""Explicit graph uncertainty preserves useful evidence."""

from dataclasses import replace
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map as rm
from tensor_grep.cli import repo_map_graph as graph


@pytest.mark.requires_grammar
@pytest.mark.parametrize("parser_fails", [False, True])
def test_optional_parser_failure_retains_public_heuristic_candidate(
    tmp_path, monkeypatch, parser_fails
):
    target = tmp_path / "target.js"
    target.write_text("export function unique_target() { return 1; }\n", encoding="utf-8")
    candidate = tmp_path / "candidate.js"
    candidate.write_text(
        'import { unique_target } from "./target";\nexport function unrelated() { return 0; }\n',
        encoding="utf-8",
    )
    spec = rm.lang_registry.spec_for_path(candidate)
    parser = spec.parser_for_path(candidate)
    assert parser is not None
    assert parser.parse(candidate.read_bytes()).root_node.has_error is False
    assert rm._confirm_import_edges(candidate, str(target), tmp_path)
    repo = rm.build_repo_map(tmp_path)
    failures = []
    if parser_fails:

        class FailedParser:
            def parse(self, source):
                failures.append(source)
                raise RuntimeError("controlled optional parser failure")

        failed_spec = replace(spec, parser_for_path=lambda path: FailedParser())
        original_spec_for_path = rm.lang_registry.spec_for_path
        monkeypatch.setattr(
            rm.lang_registry,
            "spec_for_path",
            lambda path: failed_spec if Path(path) == candidate else original_spec_for_path(path),
        )
    imports = {entry["file"]: entry["imports"] for entry in repo["imports"]}
    evidence = graph._candidate_evidence(
        rm, str(candidate), tmp_path, imports, deadline_monotonic=None, deadline_hit=None
    )
    distances = rm._reverse_import_distances(
        [str(target)], [str(path) for path in repo["files"]], imports
    )
    payload = rm.build_context_pack_from_map(repo, "unique_target")
    row = next(row for row in payload["file_matches"] if row["path"] == str(candidate))
    if parser_fails:
        assert failures
        assert evidence == {"status": "uncertain", "reason": "parser-failure", "raw": []}
        assert str(candidate) not in distances
        assert "heuristic-import" in row["reasons"]
        assert "heuristic" in row["provenance"]
        assert payload["coverage_summary"]["graph_completeness"] == "partial"
        assert any("candidate.js" in str(gap["files_sample"]) for gap in payload["resolution_gaps"])
    else:
        assert evidence["status"] == "complete"
        assert evidence["reason"] == ""
        assert distances[str(candidate)] == 1
        assert "heuristic-import" not in row["reasons"]


@pytest.mark.parametrize("resolve_fails", [False, True])
def test_uncertainty_display_resolve_failure_preserves_count_and_sample(
    tmp_path, monkeypatch, resolve_fails
):
    paths = [tmp_path / "first.py", tmp_path / "second.py"]
    for path in paths:
        path.write_text("value = 1\n", encoding="utf-8")
    if resolve_fails:
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            if path in paths:
                raise OSError("controlled owned path resolution failure")
            return original_resolve(path, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", resolve)
    gaps = graph.uncertainty_gaps(rm, [str(paths[0]), str(paths[1]), str(paths[0])], tmp_path)
    assert len(gaps) == 1
    assert gaps[0]["language"] == "python"
    assert gaps[0]["files_affected"] == 3
    expected = (
        sorted(path.as_posix() for path in paths) if resolve_fails else ["first.py", "second.py"]
    )
    assert gaps[0]["files_sample"] == expected
    assert gaps[0]["reason"]
