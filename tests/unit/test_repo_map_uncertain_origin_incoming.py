"""Unrelated outgoing uncertainty must not suppress concrete incoming edges to a valid seed."""

import ast
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map as rm


@pytest.mark.parametrize("dynamic_outgoing", [False, True])
def test_python_valid_origin_keeps_confirmed_incoming_distance(tmp_path, dynamic_outgoing):
    origin = tmp_path / "origin.py"
    consumer = tmp_path / "consumer.py"
    outgoing = "name = 'unknown'\n__import__(name)\n" if dynamic_outgoing else ""
    origin.write_text(outgoing + "def unique_origin():\n    return 1\n", encoding="utf-8")
    consumer.write_text("from origin import unique_origin\n", encoding="utf-8")
    ast.parse(origin.read_text(encoding="utf-8"))
    raw = rm._imports_with_lines_for_path(origin)
    assert any(entry.get("dynamic_unresolved") for entry in raw) is dynamic_outgoing
    confirmed = rm._confirm_import_edges(consumer, str(origin), tmp_path)
    assert len(confirmed) == 1
    assert confirmed[0]["resolution_confidence"] > 0.2
    files = [str(origin), str(consumer)]
    imports = {
        path: [entry["module"] for entry in rm._imports_with_lines_for_path(Path(path))]
        for path in files
    }
    distances = rm._reverse_import_distances([str(origin)], files, imports)
    assert distances.get(str(consumer)) == 1, (
        "uncertain outgoing origin dependency suppressed a resolved incoming edge"
    )


@pytest.mark.requires_grammar
@pytest.mark.parametrize("missing_outgoing", [False, True])
def test_javascript_blast_valid_origin_keeps_import_only_consumer(tmp_path, missing_outgoing):
    origin = tmp_path / "origin.js"
    consumer = tmp_path / "consumer.js"
    outgoing = 'import { missing } from "./missing";\n' if missing_outgoing else ""
    origin.write_text(
        outgoing + "export function unique_origin() { return 1; }\n", encoding="utf-8"
    )
    consumer.write_text(
        'import { unique_origin } from "./origin";\nexport function unused() { return 0; }\n',
        encoding="utf-8",
    )
    assert not (tmp_path / "missing.js").exists()
    origin_imports = rm._imports_with_lines_for_path(origin)
    assert any(entry["module"] == "./missing" for entry in origin_imports) is missing_outgoing
    detail = rm._js_ts_module_match_details(consumer, "./origin", str(origin), tmp_path)
    assert detail["matched"] is True
    assert detail["confidence"] > 0.2
    assert "partial-resolution" not in detail["provenance"]
    confirmed = rm._confirm_import_edges(consumer, str(origin), tmp_path)
    assert len(confirmed) == 1
    assert confirmed[0]["resolution_confidence"] > 0.2
    payload = rm.build_symbol_blast_radius_from_map(rm.build_repo_map(tmp_path), "unique_origin")
    assert any(row["file"] == str(origin) for row in payload["definitions"])
    assert all(row["file"] != str(consumer) for row in payload["callers"]), (
        "fixture must prove import-only graph reachability without a direct-call override"
    )
    matches = {row["path"]: row for row in payload["file_matches"]}
    assert matches[str(origin)]["depth"] == 0, "valid definition origin lost depth zero"
    assert any(
        level["depth"] == 0 and str(origin) in level["files"] for level in payload["caller_tree"]
    ), "unrelated outgoing uncertainty demoted the valid definition vertex"
    assert str(consumer) in matches, (
        "unresolved outgoing origin import erased a concrete incoming blast consumer"
    )
    assert matches[str(consumer)]["depth"] == 1
    assert "graph-depth" in matches[str(consumer)]["reasons"]
    assert any(
        level["depth"] == 1 and str(consumer) in level["files"] for level in payload["caller_tree"]
    )
