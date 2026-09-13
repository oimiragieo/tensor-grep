"""A JS/TS scan rooted below its tsconfig.json must SAY so, not under-report quietly.

Measured on a real 110-file Next.js corpus (2026-09-12): `tg callers <project>/src AppSidebar`
reported `import_graph_consumer_count: 0`, while `tg callers <project> AppSidebar` reported
**1** and named `layout.tsx`. Same symbol, same tree -- only the scan root differed, because
`_parse_js_ts_tsconfig` looks for `tsconfig.json` at the SCAN ROOT and the alias map was out
of scope. Nothing in the payload named that cause, so the zero read as proven absence.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.js_ts_scope_gap import _common_ancestor, js_ts_scope_gap


def _project(tmp_path: Path, *, with_tsconfig: bool = True) -> Path:
    root = tmp_path / "proj"
    (root / "src" / "app").mkdir(parents=True)
    if with_tsconfig:
        (root / "tsconfig.json").write_text('{"compilerOptions":{"paths":{}}}', encoding="utf-8")
    return root


def test_scan_scoped_below_tsconfig_is_disclosed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    files = [root / "src" / "app" / f"page{i}.tsx" for i in range(3)]
    for f in files:
        f.write_text("export const x = 1;\n", encoding="utf-8")

    gap = js_ts_scope_gap(files)

    assert gap is not None, "a scan below tsconfig.json must be disclosed, not silently degraded"
    assert gap["files_affected"] == 3
    assert "tsconfig" in gap["reason"]
    # The remediation must name BOTH paths, or the user cannot act on it.
    assert str(root) in gap["remediation"]
    assert "UNRESOLVED, not proven absent" in gap["remediation"]


def test_correctly_scoped_scan_reports_no_gap(tmp_path: Path) -> None:
    """MUTATION CONTROL.

    A helper that always fired would satisfy the test above while stamping a false gap onto
    EVERY correctly-scoped TS scan -- downgrading graph trust repo-wide for no reason. A scan
    rooted at the tsconfig must stay byte-identical, i.e. return None.
    """
    root = _project(tmp_path)
    files = [root / "index.tsx"]
    files[0].write_text("export const x = 1;\n", encoding="utf-8")

    assert js_ts_scope_gap(files) is None


def test_project_without_any_tsconfig_reports_no_gap(tmp_path: Path) -> None:
    """No alias map exists anywhere, so there is nothing to miss and nothing to warn about."""
    root = _project(tmp_path, with_tsconfig=False)
    files = [root / "src" / "app" / "page.tsx"]
    files[0].write_text("export const x = 1;\n", encoding="utf-8")

    assert js_ts_scope_gap(files) is None


def test_non_js_ts_universe_reports_no_gap(tmp_path: Path) -> None:
    root = _project(tmp_path)
    files = [root / "src" / "app" / "thing.py"]
    files[0].write_text("x = 1\n", encoding="utf-8")

    assert js_ts_scope_gap(files) is None


def test_sources_in_a_subdirectory_of_a_correctly_scoped_root_emit_no_gap(tmp_path) -> None:
    """REGRESSION (validator seat, 2026-09-12). The original control was topologically
    vacuous per Oracle Form 5: it placed `index.tsx` BESIDE `tsconfig.json`, the one layout
    where the common parent of the matched files IS the scan root, so the inference bug it
    was meant to catch could not appear.

    The real-world shape -- tsconfig at the project root, every source under `src/` -- made
    the common parent `<project>/src`, which has no tsconfig, so a CORRECTLY scoped scan was
    told it was mis-scoped. Shipped in v1.119.7 and reproduced on the in-tree fixture
    `benchmarks/bakeoff_fixtures/js_ts/tsconfig_path_alias`.
    """
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "tsconfig.json").write_text("{}", encoding="utf-8")
    files = []
    for name in ("service.ts", "payments.ts"):
        f = root / "src" / name
        f.write_text("export const x = 1;\n", encoding="utf-8")
        files.append(f)

    # The common parent is <project>/src -- NOT the scan root. That divergence is the bug.
    assert _common_ancestor(files) == root / "src"
    assert not (root / "src" / "tsconfig.json").exists()

    # Scanned at the project root, aliases resolve: no gap.
    assert js_ts_scope_gap(files, root) is None


def test_the_same_tree_scanned_below_the_tsconfig_still_reports_the_gap(tmp_path) -> None:
    """Positive arm of the regression above: identical tree, identical files, only the scan
    root differs. Without this, the fix could pass by never emitting a gap at all.
    """
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "tsconfig.json").write_text("{}", encoding="utf-8")
    files = []
    for name in ("service.ts", "payments.ts"):
        f = root / "src" / name
        f.write_text("export const x = 1;\n", encoding="utf-8")
        files.append(f)

    gap = js_ts_scope_gap(files, root / "src")
    assert gap is not None
    assert gap["files_affected"] == 2
    assert str(root) in gap["remediation"]
