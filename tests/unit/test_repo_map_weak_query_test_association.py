"""Concrete import chains do not strengthen an unrelated query's heuristic origin."""

import pytest

from tensor_grep.cli import repo_map as rm


@pytest.mark.requires_grammar
@pytest.mark.parametrize("hops", [1, 2])
@pytest.mark.parametrize("real_definition", [False, True])
def test_public_test_association_preserves_query_origin_trust(tmp_path, hops, real_definition):
    chosen = tmp_path / "chosen.js"
    origin_source = (
        "export function unique_target() { return 2; }\n"
        if real_definition
        else 'import { missing } from "./unique_target_missing";\n'
    )
    chosen.write_text(
        origin_source + "export function unrelated() { return 1; }\n", encoding="utf-8"
    )
    test_file = tmp_path / "tests/test_other.js"
    test_file.parent.mkdir()
    if hops == 2:
        middle = tmp_path / "middle.js"
        middle.write_text(
            'import { unrelated } from "./chosen";\nexport function bridge() { return 0; }\n',
            encoding="utf-8",
        )
        test_file.write_text(
            'import { bridge } from "../middle";\nexport function test_other() { return 0; }\n',
            encoding="utf-8",
        )
        edges = [(middle, chosen), (test_file, middle)]
    else:
        test_file.write_text(
            'import { unrelated } from "../chosen";\nexport function test_other() { return 0; }\n',
            encoding="utf-8",
        )
        edges = [(test_file, chosen)]
    for importer, target in edges:
        confirmed = rm._confirm_import_edges(importer, str(target), tmp_path)
        assert len(confirmed) == 1, "concrete import-chain positive control failed"
        assert confirmed[0]["resolution_confidence"] > 0.2
    payload = rm.build_context_pack_from_map(rm.build_repo_map(tmp_path), "unique_target")
    rows = [row for row in payload["test_matches"] if row["path"] == str(test_file)]
    if real_definition:
        assert any(symbol["name"] == "unique_target" for symbol in payload["symbols"])
        assert len(rows) == 1
        assert rows[0]["association"]["edge_kind"] == "import-graph"
        assert rows[0]["association"]["confidence"] == "strong"
    else:
        assert payload["symbols"] == [], "weak query fixture accidentally gained a definition"
        origin = next(row for row in payload["file_matches"] if row["path"] == str(chosen))
        assert "heuristic-import" in origin["reasons"]
        assert payload["coverage_summary"]["graph_completeness"] == "partial"
        assert len(rows) == 1, (
            "known concrete consumer disappeared instead of retaining heuristic evidence"
        )
        for row in rows:
            assert row["association"]["confidence"] != "strong", (
                "exact incoming imports inflated an import-only query origin into strong test evidence"
            )
            assert row["association"]["confidence"] == "weak"
            assert "heuristic-import" in row["reasons"]
            assert "heuristic" in row["provenance"]
            assert "heuristic" in row["association"]["provenance"]
