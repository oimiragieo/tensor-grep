"""Public test associations require real import evidence, not click/cli substrings."""

import pytest

from tensor_grep.cli import repo_map as rm


@pytest.mark.parametrize("module", ["pkg.cli", "click"])
def test_public_context_test_association_requires_actual_import_edge(tmp_path, module):
    target = tmp_path / "pkg/cli.py"
    test_file = tmp_path / "tests/test_other.py"
    target.parent.mkdir()
    test_file.parent.mkdir()
    target.write_text("def unique_target():\n    return 1\n", encoding="utf-8")
    test_file.write_text(
        f"import {module}\ndef test_unrelated():\n    assert True\n", encoding="utf-8"
    )
    raw = rm._imports_with_lines_for_path(test_file)
    assert [entry["module"] for entry in raw] == [module]
    confirmed = rm._confirm_import_edges(test_file, str(target), tmp_path)
    if module == "pkg.cli":
        assert len(confirmed) == 1
        assert confirmed[0]["resolution_confidence"] > 0.2
    else:
        assert confirmed == []
    repo = rm.build_repo_map(tmp_path)
    imports = {row["file"]: row["imports"] for row in repo["imports"]}
    distances = rm._reverse_import_distances(
        [str(target)], [str(target), str(test_file)], imports, repo_root=tmp_path
    )
    assert distances.get(str(test_file)) == (1 if module == "pkg.cli" else None)
    payload = rm.build_context_pack_from_map(repo, "unique_target")
    rows = [row for row in payload["test_matches"] if row["path"] == str(test_file)]
    if module == "pkg.cli":
        assert len(rows) == 1
        assert rows[0]["association"]["edge_kind"] == "import-graph"
        assert rows[0]["association"]["confidence"] == "strong"
        assert "test-graph" in rows[0]["reasons"]
    else:
        for row in rows:
            association = row["association"]
            assert not (
                association["confidence"] == "strong"
                and (
                    association["edge_kind"] in {"import-graph", "test-graph"}
                    or "test-graph" in row["reasons"]
                )
            ), "parsed click nonedge became a strong import-based public test association"
            assert association["edge_kind"] != "import-graph"
            assert "test-graph" not in row["reasons"]
            assert association["confidence"] == "weak" or "heuristic" in row["provenance"]
