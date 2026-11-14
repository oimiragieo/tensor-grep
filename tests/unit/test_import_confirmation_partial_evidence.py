"""Partial import-resolution evidence must survive existing reverse-edge confirmation."""

import pytest

from tensor_grep.cli import repo_map as rm


def _write(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.parametrize("extension", ["js", "ts", "rs"])
def test_partial_module_match_is_not_promoted_to_confirmed_confidence(tmp_path, extension):
    source = "use a;\n" if extension == "rs" else 'import { value } from "a";\n'
    importer = _write(tmp_path / f"consumer.{extension}", source)
    _write(
        tmp_path / f"pkg/a.{extension}",
        "pub fn thing() {}" if extension == "rs" else "export const value=1;",
    )
    target = _write(
        tmp_path / f"other/a.{extension}",
        "pub fn thing() {}" if extension == "rs" else "export const value=2;",
    )
    detail = (
        rm._rust_module_match_details(importer, "a", str(target), tmp_path)
        if extension == "rs"
        else rm._js_ts_module_match_details(importer, "a", str(target), tmp_path)
    )
    assert detail["matched"] is True, "fixture did not reach partial-match positive control"
    assert detail["confidence"] == 0.2
    assert "partial-resolution" in detail["provenance"]
    edges = rm._confirm_import_edges(importer, str(target), tmp_path)
    for edge in edges:
        assert edge["resolution_confidence"] <= detail["confidence"], (
            "partial suffix match was promoted to stronger confirmed evidence"
        )
        assert "partial-resolution" in str(edge.get("path_provenance", edge["provenance"]))


@pytest.mark.parametrize("extension", ["js", "ts", "rs"])
def test_resolved_module_match_still_confirms_an_edge(tmp_path, extension):
    source = "use crate::pkg::a;\n" if extension == "rs" else 'import { value } from "./pkg/a";\n'
    module = "crate::pkg::a" if extension == "rs" else "./pkg/a"
    importer = _write(tmp_path / f"consumer.{extension}", source)
    target = _write(
        tmp_path / f"pkg/a.{extension}",
        "pub fn thing() {}" if extension == "rs" else "export const value=1;",
    )
    detail = (
        rm._rust_module_match_details(importer, module, str(target), tmp_path)
        if extension == "rs"
        else rm._js_ts_module_match_details(importer, module, str(target), tmp_path)
    )
    assert detail["matched"] is True
    assert detail["confidence"] > 0.2
    assert "partial-resolution" not in detail["provenance"]
    edges = rm._confirm_import_edges(importer, str(target), tmp_path)
    assert len(edges) == 1
    assert edges[0]["resolution_confidence"] > 0.2
