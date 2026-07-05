"""``tg agent --ignore`` must exclude vendor/skill trees from the capsule, mirroring the
already-shipped ``tg orient --ignore`` (PR #392).

Dogfood #51 (HIGH): on a harness/doc repo, ``tg agent . "task"`` ranked vendor/SEO/skill-tree
files as the primary target over real code. ``tg orient`` already got ``--ignore`` to exclude
those trees; ``tg agent`` needs the identical escape hatch, applied to the same repo_map (files/
symbols/imports) BEFORE ranking so an ignored tree can never win as primary_target or show up in
alternatives/snippets.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.agent_capsule import build_agent_capsule


def _write_seo_vs_src_repo(tmp_path: Path) -> None:
    # NB: "vendor" is already excluded from the repo walk by tensor-grep's own vendor/cache dir
    # list (repo_map._SKIP_DIR_NAMES); "seo" is not, matching the real dogfood repo (core/skills/
    # seo) that motivated --ignore, so it's a faithful fixture for the --ignore feature itself.
    seo = tmp_path / "seo"
    src = tmp_path / "src"
    seo.mkdir()
    src.mkdir()
    # seo/hub.py: contains the exact query match, so without --ignore it wins as primary_target.
    (seo / "hub.py").write_text(
        "def process_data(payload):\n    return payload\n", encoding="utf-8"
    )
    for i in range(8):
        (src / f"m{i}.py").write_text(
            "from seo.hub import process_data\n\n"
            f"def caller_{i}():\n    return process_data({i})\n",
            encoding="utf-8",
        )
    # src/app.py: real code with no direct query match, present so the ignored capsule still
    # has non-seo source to fall back to.
    (src / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")


def test_agent_ignore_excludes_seo_tree(tmp_path: Path) -> None:
    _write_seo_vs_src_repo(tmp_path)

    baseline = build_agent_capsule("process data", str(tmp_path))
    assert "seo" in Path(baseline["primary_target"]["file"]).parts, (
        "test fixture invalid: seo/hub.py should win as primary target without --ignore"
    )

    ignored = build_agent_capsule("process data", str(tmp_path), ignore=("seo/**",))

    assert "seo" not in Path(ignored["primary_target"]["file"]).parts, "seo tree not excluded"

    def _files(payload: dict) -> list[str]:
        files = [payload["primary_target"].get("file", "")]
        files += [alt.get("file", "") for alt in payload.get("alternative_targets", [])]
        files += [snip.get("file", "") for snip in payload.get("snippets", [])]
        return [f for f in files if f]

    referenced = _files(ignored)
    assert not any("seo" in Path(f).parts for f in referenced), (
        f"seo path leaked into ignored capsule: {referenced}"
    )
    assert any("src" in Path(f).parts for f in referenced), (
        f"real src code missing from ignored capsule: {referenced}"
    )


def test_agent_ignore_empty_is_identity(tmp_path: Path) -> None:
    _write_seo_vs_src_repo(tmp_path)

    no_ignore_kw = build_agent_capsule("process data", str(tmp_path))
    empty_ignore = build_agent_capsule("process data", str(tmp_path), ignore=())

    assert empty_ignore["primary_target"] == no_ignore_kw["primary_target"]
    assert empty_ignore["alternative_targets"] == no_ignore_kw["alternative_targets"]
    assert empty_ignore["snippets"] == no_ignore_kw["snippets"]
