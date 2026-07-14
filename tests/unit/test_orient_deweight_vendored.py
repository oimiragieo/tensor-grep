"""Tests for auto-de-weighting of bundled vendor/skill/generated CODE subtrees in `tg orient`
centrality (#132 / #55 PR6), known AI-tool/harness config directories (#164), and the M1 broader
whole-tree detection (unambiguous STRONG-0 vendor names + STRONG-3 skill-tree shape heuristic).

A subtree fires on STRONG-0 (closed-vocabulary directory NAME -- a tool-config dir like `.claude`,
OR an unambiguous vendor/dependency dir like `node_modules`/`vendor`/`third_party`/`_vendored`/
`external_repos`) ALONE, on STRONG-3 (a `skills`-named directory whose immediate children have the
SHAPE of independent leaf skills) ALONE, or on STRONG-1 (nested package manifest) AND (STRONG-2
(import island) OR WEAK (name prior)). The de-weight multiplies a subtree file's composite
centrality by ``_DEWEIGHT_FACTOR`` -- it LOWERS the score, it never EXCLUDES the file, so a
genuinely central vendored file can still surface. A monorepo subproject that has a manifest but IS
imported across the repo is protected by the import-island test (de-weight would hide real product
code); a `skills`-named directory imported across the repo is protected the same way by STRONG-3's
own external-import guard (see `_is_skill_leaf_tree`).

`_detect_vendored_subtrees` reads the real filesystem (it checks ``root.is_dir()`` and each
candidate directory for a manifest via ``.exists()``), so these tests build real ``tmp_path``
trees with on-disk manifests -- unlike the hand-built-``rm`` centrality tests which never touch
disk.
"""

from pathlib import Path
from typing import Any

from tensor_grep.cli.orient_capsule import (
    _DEWEIGHT_FACTOR,
    _central_files_from_map,
    _detect_vendored_subtrees,
    _file_centrality_scores,
)


def _rm(root: Path, files: list[Path], imports: dict[Path, list[str]]) -> dict[str, Any]:
    """Build a repo-map dict rooted at a real (resolved) directory."""
    return {
        "path": str(root),
        "files": [str(f) for f in files],
        "imports": [{"file": str(f), "imports": mods} for f, mods in imports.items()],
        "symbols": [],
    }


def test_fires_on_manifest_plus_import_island(tmp_path: Path) -> None:
    # `bundled/` has its own pyproject.toml (STRONG-1). No file OUTSIDE bundled/ imports into it,
    # and its name is NOT a vendor-prior -> fires on manifest + import-island (STRONG-2) alone.
    root = tmp_path.resolve()
    (root / "bundled").mkdir()
    (root / "bundled" / "pyproject.toml").write_text("[project]\nname = 'bundled'\n")
    rm = _rm(
        root,
        [root / "app.py", root / "bundled" / "lib.py", root / "bundled" / "helper.py"],
        {root / "bundled" / "lib.py": ["helper"]},  # internal edge only
    )
    trees = _detect_vendored_subtrees(rm)
    assert str(root / "bundled") in trees
    reasons = trees[str(root / "bundled")]["reasons"]
    assert any(r.startswith("nested-manifest:") for r in reasons)
    assert "import-island" in reasons
    assert not any(r.startswith("name-prior:") for r in reasons)


def test_fires_on_manifest_plus_name_prior_even_when_not_island(tmp_path: Path) -> None:
    # `node_modules/` has a manifest AND is imported from outside (app.js imports its module, so it
    # is NOT an island) -- but "node_modules" is a name-prior, so it still fires.
    root = tmp_path.resolve()
    (root / "node_modules").mkdir()
    (root / "node_modules" / "package.json").write_text('{"name": "dep"}\n')
    rm = _rm(
        root,
        [root / "app.js", root / "node_modules" / "dep.js"],
        {root / "app.js": ["dep"]},  # OUTSIDE importer -> not an island
    )
    trees = _detect_vendored_subtrees(rm)
    assert str(root / "node_modules") in trees
    reasons = trees[str(root / "node_modules")]["reasons"]
    assert any(r.startswith("name-prior:") for r in reasons)
    assert "import-island" not in reasons


def test_skips_manifest_only_monorepo_subproject(tmp_path: Path) -> None:
    # THE false-positive guard: `packages/core/` has a manifest but is imported across the repo
    # (app.py imports its module) AND has a neutral name (no vendor-prior) -> NOT an island, NO
    # name prior -> NOT de-weighted. De-weighting a genuine subproject would hide real product code.
    root = tmp_path.resolve()
    (root / "packages" / "core").mkdir(parents=True)
    (root / "packages" / "core" / "pyproject.toml").write_text("[project]\nname = 'core'\n")
    rm = _rm(
        root,
        [root / "app.py", root / "packages" / "core" / "mod.py"],
        {root / "app.py": ["mod"]},  # imported from outside the subtree
    )
    trees = _detect_vendored_subtrees(rm)
    assert str(root / "packages" / "core") not in trees
    assert trees == {}


def test_skips_neutral_dirname_with_no_manifest_no_island_no_name_prior(tmp_path: Path) -> None:
    # An arbitrary, non-prior directory name ("helpers") with no manifest and no import-island
    # evidence must still NOT fire -- the base "no signal at all" contract is unchanged by M1's
    # STRONG-0 vendor-name promotion (below): a bare, unlisted name is still insufficient alone.
    root = tmp_path.resolve()
    (root / "helpers").mkdir()
    rm = _rm(root, [root / "app.py", root / "helpers" / "lib.py"], {})
    assert _detect_vendored_subtrees(rm) == {}


# ---------------------------------------------------------------------------
# M1: STRONG-0 unambiguous vendor/dependency directory names (`_STRONG0_VENDOR_DIR_NAMES`) fire on
# the NAME ALONE, no manifest and no import-island required -- unlike the old WEAK name-prior role
# of `_VENDOR_NAME_PRIOR`, which only ever broke a STRONG-1-manifest tie. Dogfood gap: a bundled
# `node_modules/`/`vendor/`/`third_party/`/`_vendored/`/`external_repos/` subtree with no manifest
# copied into the scanned tree previously required STRONG-1 to even be CONSIDERED a candidate, so
# the whole tree ranked as "central" alongside real product code. `skills` is deliberately EXCLUDED
# from this promotion (see the STRONG-3 shape-heuristic tests further below).
# ---------------------------------------------------------------------------


def test_strong0_vendor_name_fires_without_any_manifest(tmp_path: Path) -> None:
    # M1: `vendor/` (an unambiguous STRONG-0 vendor name) now fires on the name ALONE -- no
    # manifest required. Supersedes the old "name prior is only a tie-breaker" contract for these 5
    # unambiguous names specifically.
    root = tmp_path.resolve()
    (root / "vendor").mkdir()
    rm = _rm(root, [root / "app.py", root / "vendor" / "lib.py"], {})
    trees = _detect_vendored_subtrees(rm)
    assert str(root / "vendor") in trees
    info = trees[str(root / "vendor")]
    assert "vendor-name-strong0:vendor" in info["reasons"]
    assert not any(r.startswith("nested-manifest:") for r in info["reasons"])
    assert info["ignore_glob"] == "vendor/**"


def test_strong0_node_modules_fires_on_name_alone_task_scenario_a(tmp_path: Path) -> None:
    # Task scenario (a): a whole `node_modules/` tree with NO nested manifest anywhere must be
    # de-weighted and its root surfaced as a ready-to-paste `--ignore` glob.
    root = tmp_path.resolve()
    pkg_dir = root / "node_modules" / "left-pad"
    pkg_dir.mkdir(parents=True)
    lib = pkg_dir / "index.js"
    lib.write_text("module.exports = () => {};\n")
    rm = _rm(root, [root / "app.js", lib], {})

    trees = _detect_vendored_subtrees(rm)

    assert str(root / "node_modules") in trees
    info = trees[str(root / "node_modules")]
    assert "vendor-name-strong0:node_modules" in info["reasons"]
    assert info["ignore_glob"] == "node_modules/**"


def test_strong0_node_modules_deweights_central_score_but_keeps_the_file(tmp_path: Path) -> None:
    # De-weight-not-exclude (task scenario d) for the NEW STRONG-0 vendor mechanism: a genuinely
    # central file inside a bare (no-manifest) `node_modules/` tree still surfaces in central_files,
    # just at a lowered (`_DEWEIGHT_FACTOR`-multiplied) score.
    root = tmp_path.resolve()
    pkg_dir = root / "node_modules" / "left-pad"
    pkg_dir.mkdir(parents=True)
    lib = pkg_dir / "index.js"
    lib.write_text("module.exports = () => {};\n")
    rm = _rm(root, [root / "app.js", lib], {})
    rm["symbols"] = [{"name": f"Sym{i}", "kind": "function", "file": str(lib)} for i in range(4)]

    raw = _central_files_from_map(rm, max_central_files=10, auto_deweight=False)
    dew = _central_files_from_map(rm, max_central_files=10, auto_deweight=True)

    lib_str = str(lib)
    raw_lib = next(f for f in raw if f["file"] == lib_str)
    dew_lib = next(
        f for f in dew if f["file"] == lib_str
    )  # still present -> de-weight, not exclude
    assert raw_lib["score"] > 0
    assert dew_lib["score"] == round(raw_lib["score"] * _DEWEIGHT_FACTOR, 6)


# ---------------------------------------------------------------------------
# M1: STRONG-3 `skills`-named directory SHAPE heuristic. Unlike the unambiguous STRONG-0 vendor
# names above, `skills` stays AMBIGUOUS -- a repo's own feature/plugin package could plausibly be
# named `skills/` -- so it is gated on the SHAPE of its immediate children (predominantly
# self-contained leaf skills) plus a false-positive guard (nothing outside the tree imports into
# it), not a bare name-alone promotion.
# ---------------------------------------------------------------------------


def test_skill_tree_of_many_leaf_skills_is_deweighted(tmp_path: Path) -> None:
    # Task scenario (b): `core/skills/` has 3 immediate children, each a self-contained leaf skill
    # (its own SKILL.md plus a small script) -- the whole tree fires on SHAPE alone.
    root = tmp_path.resolve()
    skills_dir = root / "core" / "skills"
    files = [root / "app.py"]
    for name in ("alpha", "beta", "gamma"):
        leaf = skills_dir / name
        leaf.mkdir(parents=True)
        (leaf / "SKILL.md").write_text(f"# {name}\n")
        script = leaf / "run.py"
        script.write_text("def run():\n    pass\n")
        files.append(script)
    rm = _rm(root, files, {})

    trees = _detect_vendored_subtrees(rm)

    assert str(skills_dir) in trees
    info = trees[str(skills_dir)]
    assert "skill-tree-shape" in info["reasons"]
    assert info["ignore_glob"] == "core/skills/**"


def test_skill_tree_of_pure_markdown_leaf_skills_is_still_deweighted(tmp_path: Path) -> None:
    # Regression: a skill tree whose leaves carry ONLY a SKILL.md (no accompanying code file at
    # all -- a very common real-world shape, e.g. this repo's own `.claude/skills/*/SKILL.md`
    # onboarding library) must still be reported. `_is_skill_leaf_tree` validates the SHAPE against
    # the real filesystem child-directory listing, independent of the code-only `tree_files` used
    # for the centrality de-weight multiplier -- an earlier draft of this fix silently dropped this
    # exact case via a stale `if not tree_files: continue` guard that pre-dated STRONG-3.
    root = tmp_path.resolve()
    skills_dir = root / "core" / "skills"
    files = [root / "app.py"]
    for name in ("alpha", "beta", "gamma"):
        leaf = skills_dir / name
        leaf.mkdir(parents=True)
        skill_md = leaf / "SKILL.md"
        skill_md.write_text(f"# {name}\n")
        files.append(skill_md)  # no accompanying code file in this leaf at all
    rm = _rm(root, files, {})

    trees = _detect_vendored_subtrees(rm)

    assert str(skills_dir) in trees
    assert "skill-tree-shape" in trees[str(skills_dir)]["reasons"]
    assert trees[str(skills_dir)]["ignore_glob"] == "core/skills/**"


def test_skill_tree_deweights_central_score_but_keeps_the_file(tmp_path: Path) -> None:
    # De-weight-not-exclude (task scenario d) for the NEW STRONG-3 skill-tree-shape mechanism: a
    # genuinely central file inside a detected skill-leaf tree still surfaces in central_files, just
    # at a lowered score.
    root = tmp_path.resolve()
    skills_dir = root / "core" / "skills"
    files = [root / "app.py"]
    hub: Path | None = None
    for name in ("alpha", "beta", "gamma"):
        leaf = skills_dir / name
        leaf.mkdir(parents=True)
        (leaf / "SKILL.md").write_text(f"# {name}\n")
        script = leaf / "run.py"
        script.write_text("def run():\n    pass\n")
        files.append(script)
        if hub is None:
            hub = script
    assert hub is not None
    rm = _rm(root, files, {})
    rm["symbols"] = [{"name": f"Sym{i}", "kind": "function", "file": str(hub)} for i in range(4)]

    raw = _central_files_from_map(rm, max_central_files=10, auto_deweight=False)
    dew = _central_files_from_map(rm, max_central_files=10, auto_deweight=True)

    hub_str = str(hub)
    raw_hub = next(f for f in raw if f["file"] == hub_str)
    dew_hub = next(
        f for f in dew if f["file"] == hub_str
    )  # still present -> de-weight, not exclude
    assert raw_hub["score"] > 0
    assert dew_hub["score"] == round(raw_hub["score"] * _DEWEIGHT_FACTOR, 6)


def test_skips_skill_named_package_imported_across_repo(tmp_path: Path) -> None:
    # Task scenario (c) -- the false-positive guard: `skills/plugin_a/` superficially LOOKS
    # leaf-shaped (its own SKILL.md), but the tree as a whole IS imported from OUTSIDE it
    # (`main.py` imports `plugin_a/handler.py`) -- a genuine product package, not a bundled/vendored
    # skill library. Must NOT be de-weighted no matter how leaf-shaped its children look.
    root = tmp_path.resolve()
    skills_dir = root / "skills"
    plugin = skills_dir / "plugin_a"
    plugin.mkdir(parents=True)
    (plugin / "SKILL.md").write_text("# plugin_a\n")
    handler = plugin / "handler.py"
    handler.write_text("def run():\n    pass\n")
    main = root / "main.py"
    rm = _rm(root, [main, handler], {main: ["handler"]})

    assert _detect_vendored_subtrees(rm) == {}


def test_skips_flat_skills_package_with_no_subdirectories(tmp_path: Path) -> None:
    # A `skills/` package with flat `.py` files directly inside (no folder-per-skill layout) has NO
    # children to score as "leaf skills" -- a real flat Python package named `skills/` is never
    # mistaken for a bundle of independent skills.
    root = tmp_path.resolve()
    skills_dir = root / "skills"
    skills_dir.mkdir()
    (skills_dir / "foo.py").write_text("def foo():\n    pass\n")
    (skills_dir / "bar.py").write_text("def bar():\n    pass\n")
    rm = _rm(
        root,
        [root / "app.py", skills_dir / "foo.py", skills_dir / "bar.py"],
        {},
    )

    assert _detect_vendored_subtrees(rm) == {}


def test_skill_tree_below_leaf_fraction_threshold_is_not_deweighted(tmp_path: Path) -> None:
    # Only half (2 of 4) of the immediate children look like self-contained leaf skills -- below
    # `_SKILL_LEAF_FRACTION_THRESHOLD` -- so the SHAPE check must not fire even though the tree as a
    # whole is not imported from outside it.
    root = tmp_path.resolve()
    skills_dir = root / "skills"

    real_skill = skills_dir / "real_skill"
    real_skill.mkdir(parents=True)
    (real_skill / "SKILL.md").write_text("# real_skill\n")

    shared = skills_dir / "shared"
    shared.mkdir(parents=True)
    shared_util = shared / "util.py"
    shared_util.write_text("def util():\n    pass\n")

    consumer_a = skills_dir / "consumer_a"
    consumer_a.mkdir(parents=True)
    a_impl = consumer_a / "a_impl.py"
    a_impl.write_text("def a():\n    pass\n")

    consumer_b = skills_dir / "consumer_b"
    consumer_b.mkdir(parents=True)
    b_impl = consumer_b / "b_impl.py"
    b_impl.write_text("def b():\n    pass\n")

    rm = _rm(
        root,
        [root / "app.py", shared_util, a_impl, b_impl],
        {a_impl: ["util"], b_impl: ["util"]},  # both consumers reach OUT to a sibling child
    )

    assert _detect_vendored_subtrees(rm) == {}


def test_central_files_deweights_but_keeps_the_file(tmp_path: Path) -> None:
    # `bundled/hub.py` is highly central WITHIN its island (imported by two siblings). With
    # de-weight ON its composite score is multiplied by _DEWEIGHT_FACTOR -- but it is still PRESENT
    # in central_files (de-weight != exclude).
    root = tmp_path.resolve()
    (root / "bundled").mkdir()
    (root / "bundled" / "pyproject.toml").write_text("[project]\nname = 'bundled'\n")
    rm = _rm(
        root,
        [
            root / "bundled" / "hub.py",
            root / "bundled" / "a.py",
            root / "bundled" / "b.py",
        ],
        {root / "bundled" / "a.py": ["hub"], root / "bundled" / "b.py": ["hub"]},
    )
    raw = _central_files_from_map(rm, max_central_files=10, auto_deweight=False)
    dew = _central_files_from_map(rm, max_central_files=10, auto_deweight=True)

    hub = str(root / "bundled" / "hub.py")
    raw_hub = next(f for f in raw if f["file"] == hub)
    dew_hub = next(f for f in dew if f["file"] == hub)  # still present -> de-weight, not exclude
    assert raw_hub["score"] > 0
    assert dew_hub["score"] == round(raw_hub["score"] * _DEWEIGHT_FACTOR, 6)


def test_file_centrality_scores_are_raw_not_deweighted(tmp_path: Path) -> None:
    # `_file_centrality_scores` (which `_suggested_scope_from_map` reads) must return the RAW
    # composite score -- the de-weight lives in `_central_files_from_map`, so suggested_scope keeps
    # its unmodified signal.
    root = tmp_path.resolve()
    (root / "bundled").mkdir()
    (root / "bundled" / "pyproject.toml").write_text("[project]\nname = 'bundled'\n")
    rm = _rm(
        root,
        [root / "bundled" / "hub.py", root / "bundled" / "a.py", root / "bundled" / "b.py"],
        {root / "bundled" / "a.py": ["hub"], root / "bundled" / "b.py": ["hub"]},
    )
    _code_files, centrality = _file_centrality_scores(rm)
    hub = str(root / "bundled" / "hub.py")
    # hub.py: fan_in=2 (a,b import it) + fan_out=0 + density=0 = 2.0, RAW (not * _DEWEIGHT_FACTOR)
    assert centrality[hub] == 2.0


def test_skips_graph_invisible_subproject_with_no_import_edges(tmp_path: Path) -> None:
    # A non-Python subproject -- e.g. `rust_core/` with its own Cargo.toml + .rs files -- is INVISIBLE
    # to the Python-centric stem import graph, so it has ZERO import edges. That trivially satisfies
    # "externally isolated" but is NOT an import island (no internal cohesion), so with a neutral name
    # it must NOT be de-weighted -- else a legitimate Rust/Go crate at the repo root gets buried.
    # Regression for the agent-capsule rust-language-hint hardcase (#525 CI: rust_core was falsely
    # de-weighted, so a Python file won primary_target over src/lib.rs).
    root = tmp_path.resolve()
    (root / "rust_core").mkdir()
    (root / "rust_core" / "Cargo.toml").write_text("[package]\nname = 'rc'\nversion = '0.1.0'\n")
    rm = _rm(
        root,
        [
            root / "app.py",
            root / "rust_core" / "src" / "lib.rs",
            root / "rust_core" / "src" / "mod.rs",
        ],
        {},  # no resolvable import edges anywhere (Rust files; the Python stem graph is blind)
    )
    assert _detect_vendored_subtrees(rm) == {}


def test_deeply_nested_subtree_file_counts_as_a_member(tmp_path: Path) -> None:
    # A file NESTED several levels inside a detected subtree (`bundled/pkg/mod.py`) must be counted as
    # a tree member. The import-island verdict here HINGES on it: `lib.py` imports `mod` (an internal
    # edge) so the island fires ONLY if the nested `mod.py` is in `tree_files`. Guards the lexical
    # tuple-prefix membership test (`parts[:depth] == prefix`) that replaced the per-file, per-subtree
    # `_path_is_relative_to` resolve() check (perf, 2026-07-11) -- a prefix bug that dropped nested
    # files would silently turn this island into a non-island.
    root = tmp_path.resolve()
    (root / "bundled" / "pkg").mkdir(parents=True)
    (root / "bundled" / "pyproject.toml").write_text("[project]\nname = 'bundled'\n")
    rm = _rm(
        root,
        [root / "app.py", root / "bundled" / "lib.py", root / "bundled" / "pkg" / "mod.py"],
        {
            root / "bundled" / "lib.py": ["mod"]
        },  # internal edge lib -> pkg/mod (both inside bundled/)
    )
    trees = _detect_vendored_subtrees(rm)
    assert str(root / "bundled") in trees
    assert "import-island" in trees[str(root / "bundled")]["reasons"]


# ---------------------------------------------------------------------------
# STRONG-0: tool/harness config directory names (#164 dogfood -- `.claude/hooks|lib|tools`
# ranked as top-10 orient central_files on every Claude-Code-harness repo, because such a
# directory carries NO manifest of its own, so the old STRONG-1-gated detector could never flag
# it no matter what name list it was added to).
# ---------------------------------------------------------------------------


def test_fires_on_tool_config_dir_name_without_any_manifest(tmp_path: Path) -> None:
    # `.claude/hooks/` has NO manifest anywhere in the tree (real agent-studio shape: no
    # package.json/pyproject.toml directly inside .claude, .claude/hooks, etc.) -- the manifest-
    # gated STRONG-1 path would never even consider it a candidate. STRONG-0 (exact dir-name
    # match) must fire on the name alone.
    root = tmp_path.resolve()
    (root / ".claude" / "hooks").mkdir(parents=True)
    rm = _rm(
        root,
        [root / "app.py", root / ".claude" / "hooks" / "run-hook.cjs"],
        {},
    )
    trees = _detect_vendored_subtrees(rm)
    assert str(root / ".claude") in trees
    info = trees[str(root / ".claude")]
    assert info["reasons"] == ["tool-config-name:.claude"]
    assert not any(r.startswith("nested-manifest:") for r in info["reasons"])
    assert info["ignore_glob"] == ".claude/**"


def test_tool_config_dir_deeply_nested_file_counts_as_a_member(tmp_path: Path) -> None:
    # A file several levels inside `.claude/` (e.g. `.claude/hooks/audit/check.cjs`) must still be
    # attributed to the `.claude` subtree -- same tuple-prefix membership test as the manifest path.
    root = tmp_path.resolve()
    (root / ".claude" / "hooks" / "audit").mkdir(parents=True)
    rm = _rm(
        root,
        [root / "app.py", root / ".claude" / "hooks" / "audit" / "check.cjs"],
        {},
    )
    trees = _detect_vendored_subtrees(rm)
    assert str(root / ".claude") in trees


def test_does_not_fire_on_dirname_without_leading_dot(tmp_path: Path) -> None:
    # "claude" (no leading dot, not the closed STRONG-0 vocabulary) with no manifest and no vendor
    # name-prior hit must NOT fire -- guards against an over-broad substring/prefix match.
    root = tmp_path.resolve()
    (root / "claude").mkdir()
    rm = _rm(root, [root / "app.py", root / "claude" / "lib.py"], {})
    assert _detect_vendored_subtrees(rm) == {}


def test_tool_config_dir_deweights_central_files_score_but_keeps_the_file(tmp_path: Path) -> None:
    # End-to-end through _central_files_from_map (mirrors test_central_files_deweights_but_keeps_
    # the_file for the manifest path): the composite score of a file under `.claude/` is multiplied
    # by _DEWEIGHT_FACTOR when auto_deweight is on -- de-weight, never exclude, so the file is still
    # present in central_files either way.
    root = tmp_path.resolve()
    (root / ".claude" / "hooks").mkdir(parents=True)
    hook = root / ".claude" / "hooks" / "run-hook.cjs"
    rm = _rm(root, [root / "app.py", hook], {})
    rm["symbols"] = [{"name": f"Sym{i}", "kind": "function", "file": str(hook)} for i in range(4)]

    raw = _central_files_from_map(rm, max_central_files=10, auto_deweight=False)
    dew = _central_files_from_map(rm, max_central_files=10, auto_deweight=True)

    hook_str = str(hook)
    raw_hook = next(f for f in raw if f["file"] == hook_str)
    dew_hook = next(
        f for f in dew if f["file"] == hook_str
    )  # still present -> de-weight, not exclude
    assert raw_hook["score"] > 0
    assert dew_hook["score"] == round(raw_hook["score"] * _DEWEIGHT_FACTOR, 6)
