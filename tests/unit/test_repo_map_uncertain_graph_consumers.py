"""Resolved graph routes and uncertain consumer evidence remain distinguishable."""

from pathlib import Path

import pytest

from tensor_grep.cli import repo_map as rm


def _write(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return str(path)


def test_confirmed_longer_import_route_wins_over_partial_shortcut(tmp_path):
    target = _write(tmp_path / "pkg/a.js", "export function value() { return 1; }\n")
    middle = _write(
        tmp_path / "middle.js",
        'import { value } from "./pkg/a";\nexport function middle() { return value(); }\n',
    )
    consumer = _write(
        tmp_path / "consumer.js",
        'import { middle } from "./middle";\nimport { value } from "a";\n'
        "export function consume() { return middle(); }\n",
    )
    for importer, module, definition in [
        (middle, "./pkg/a", target),
        (consumer, "./middle", middle),
    ]:
        detail = rm._js_ts_module_match_details(Path(importer), module, definition, tmp_path)
        assert detail["matched"] is True
        assert detail["confidence"] > 0.2
        assert "partial-resolution" not in detail["provenance"]
    partial = rm._js_ts_module_match_details(Path(consumer), "a", target, tmp_path)
    assert partial["matched"] is True
    assert partial["confidence"] == 0.2
    assert "partial-resolution" in partial["provenance"]
    files = [target, middle, consumer]
    imports = {
        path: [entry["module"] for entry in rm._imports_with_lines_for_path(Path(path))]
        for path in files
    }
    distances = rm._reverse_import_distances([target], files, imports)
    assert distances[middle] == 1
    assert distances[consumer] == 2, "partial shortcut overrode the longer resolved import route"


def test_automatic_dependents_exclude_only_heuristic_import_evidence(tmp_path):
    primary = str(tmp_path / "target.js")
    heuristic = str(tmp_path / "heuristic.js")
    confirmed = str(tmp_path / "confirmed.js")
    caller = str(tmp_path / "caller.js")
    radius = {
        "definitions": [{"file": primary}],
        "file_matches": [
            {"path": heuristic, "depth": 1, "score": 10, "reasons": ["heuristic-import"]},
            {"path": confirmed, "depth": 1, "score": 5, "reasons": ["graph-depth"]},
            {"path": caller, "depth": 1, "score": 5, "reasons": ["heuristic-import", "caller"]},
        ],
    }
    matches = rm._ordered_dependent_file_matches(radius, primary_file=primary, max_depth=3)
    paths = [row["path"] for row in matches]
    assert confirmed in paths, "confirmed graph support was dropped"
    assert caller in paths, "direct caller support was dropped"
    assert heuristic not in paths, "heuristic-only row became an automatic edit dependent"


@pytest.fixture
def partial_import_map(tmp_path):
    target = _write(tmp_path / "pkg/a.js", "export function unique_target() { return 1; }\n")
    middle = _write(
        tmp_path / "middle.js",
        'import { unique_target } from "./pkg/a";\n'
        "export function middle() { return unique_target(); }\n",
    )
    partial = _write(
        tmp_path / "partial.js",
        'import { unique_target } from "a";\nexport function unrelated() { return 2; }\n',
    )
    detail = rm._js_ts_module_match_details(Path(partial), "a", target, tmp_path)
    assert detail["matched"] is True and detail["confidence"] == 0.2
    assert "partial-resolution" in detail["provenance"]
    return rm.build_repo_map(tmp_path), target, middle, partial


@pytest.mark.requires_grammar
@pytest.mark.parametrize("builder", ["context", "blast", "callers"])
def test_public_graph_consumers_disclose_partial_import_evidence(partial_import_map, builder):
    repo, target, middle, partial = partial_import_map
    if builder == "context":
        payload = rm.build_context_pack_from_map(repo, "unique_target")
    elif builder == "blast":
        payload = rm.build_symbol_blast_radius_from_map(repo, "unique_target")
    else:
        payload = rm.build_symbol_callers_from_map(repo, "unique_target")

    if builder in {"blast", "callers"}:
        assert any(row["file"] == middle for row in payload["callers"])
        assert all(row["file"] != partial for row in payload["callers"])

    if builder in {"context", "blast"}:
        row = next(row for row in payload["file_matches"] if row["path"] == partial)
        assert "heuristic-import" in row["reasons"], "partial import lacks heuristic disclosure"
        if builder == "context":
            assert "heuristic" in row["provenance"]
        else:
            assert isinstance(row["depth"], int), "blast file_matches must retain tentative depth"
            assert "graph-depth" not in row["reasons"]
            assert all(partial not in level["files"] for level in payload["caller_tree"])
            dependents = rm._ordered_dependent_file_matches(
                payload, primary_file=target, max_depth=3
            )
            assert partial not in {row["path"] for row in dependents}

    if builder in {"blast", "callers"}:
        assert any(
            gap["language"] == "javascript"
            and gap["files_affected"] >= 1
            and any(word in gap["reason"].lower() for word in ("partial", "unresolved"))
            for gap in payload["resolution_gaps"]
        ), "public symbol response did not disclose partial import resolution"
    assert payload["coverage_summary"]["graph_completeness"] == "partial"
