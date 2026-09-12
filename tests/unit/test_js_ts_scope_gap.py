"""A JS/TS scan rooted below its tsconfig.json must SAY so, not under-report quietly.

Measured on a real 110-file Next.js corpus (2026-09-12): `tg callers <project>/src AppSidebar`
reported `import_graph_consumer_count: 0`, while `tg callers <project> AppSidebar` reported
**1** and named `layout.tsx`. Same symbol, same tree -- only the scan root differed, because
`_parse_js_ts_tsconfig` looks for `tsconfig.json` at the SCAN ROOT and the alias map was out
of scope. Nothing in the payload named that cause, so the zero read as proven absence.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.js_ts_scope_gap import js_ts_scope_gap


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
