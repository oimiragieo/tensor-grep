import importlib.util
from pathlib import Path


def _load_module(root: Path):
    script_path = root / "scripts" / "stamp_release_assets.py"
    spec = importlib.util.spec_from_file_location("stamp_release_assets", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stamp_release_assets_updates_brew_and_winget(tmp_path):
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (root / "scripts" / "tensor-grep.rb").write_text(
        (
            "class TensorGrep < Formula\n"
            '  TENSOR_GREP_VERSION = "0.9.0"\n'
            "  version TENSOR_GREP_VERSION\n"
            "end\n"
        ),
        encoding="utf-8",
    )
    (root / "scripts" / "oimiragieo.tensor-grep.yaml").write_text(
        "# Winget Manifest for tensor-grep v0.9.0\n"
        "PackageVersion: 0.9.0\n"
        "InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v0.9.0/tg-windows-amd64-cpu.exe\n",
        encoding="utf-8",
    )

    module = _load_module(Path(__file__).resolve().parents[2])
    module.ROOT = root
    rc = module.stamp_assets(check_only=False)

    assert rc == 0
    assert 'TENSOR_GREP_VERSION = "1.2.3"' in (root / "scripts" / "tensor-grep.rb").read_text(
        encoding="utf-8"
    )
    winget = (root / "scripts" / "oimiragieo.tensor-grep.yaml").read_text(encoding="utf-8")
    assert "# Winget Manifest for tensor-grep v1.2.3" in winget
    assert "PackageVersion: 1.2.3" in winget
    assert (
        "    InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v1.2.3/tg-windows-amd64-cpu.exe"
        in winget
    )


def test_stamp_release_assets_check_mode_fails_when_drifted(tmp_path):
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (root / "scripts" / "tensor-grep.rb").write_text(
        (
            "class TensorGrep < Formula\n"
            '  TENSOR_GREP_VERSION = "0.9.0"\n'
            "  version TENSOR_GREP_VERSION\n"
            "end\n"
        ),
        encoding="utf-8",
    )
    (root / "scripts" / "oimiragieo.tensor-grep.yaml").write_text(
        "# Winget Manifest for tensor-grep v0.9.0\n"
        "PackageVersion: 0.9.0\n"
        "InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v0.9.0/tg-windows-amd64-cpu.exe\n",
        encoding="utf-8",
    )

    module = _load_module(Path(__file__).resolve().parents[2])
    module.ROOT = root
    rc = module.stamp_assets(check_only=True)
    assert rc == 1


def test_stamp_release_assets_syncs_release_doc_current_version_prose(tmp_path):
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "docs").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.9.12"\n', encoding="utf-8"
    )
    (root / "scripts" / "tensor-grep.rb").write_text(
        (
            "class TensorGrep < Formula\n"
            '  TENSOR_GREP_VERSION = "1.9.12"\n'
            "  version TENSOR_GREP_VERSION\n"
            "end\n"
        ),
        encoding="utf-8",
    )
    (root / "scripts" / "oimiragieo.tensor-grep.yaml").write_text(
        "# Winget Manifest for tensor-grep v1.9.12\n"
        "PackageVersion: 1.9.12\n"
        "InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v1.9.12/tg-windows-amd64-cpu.exe\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "release_docs_current_tag: v1.9.10\n"
        "This checks the current `v1.9.10` shell/version resolution.\n",
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(
        "release_docs_current_tag: v1.9.10\n"
        "The current tagged version is `v1.9.10`, and the latest complete public PyPI/release-asset distribution is also `v1.9.10`.\n"
        "- Current release tag: `v1.9.10`.\n"
        "- PyPI/public install proof: `uvx --refresh-package tensor-grep --from tensor-grep==1.9.10 tg --version` reports `tensor-grep 1.9.10`.\n"
        "This gate checks current `v1.9.9` positioning.\n",
        encoding="utf-8",
    )
    (root / "docs" / "CONTRACTS.md").write_text(
        "release_docs_current_tag: v1.9.10\n"
        "For the current `v1.9.10` release line it checks readiness.\n",
        encoding="utf-8",
    )
    for relative in ("AGENTS.md", "docs/SESSION_HANDOFF.md", "docs/CONTINUATION_PLAN.md"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "release_docs_current_tag: v1.9.10\n"
            "The latest complete public PyPI/release-asset distribution is also `v1.9.10`.\n",
            encoding="utf-8",
        )

    module = _load_module(Path(__file__).resolve().parents[2])
    module.ROOT = root

    assert module.stamp_assets(check_only=True) == 1
    assert module.stamp_assets(check_only=False) == 0
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "release_docs_current_tag: v1.9.12" in readme
    assert "current `v1.9.12` shell/version resolution" in readme
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "current `v1.9.12` positioning" in skill
    assert "latest complete public PyPI/release-asset distribution is also `v1.9.12`" in skill
    assert "latest complete public PyPI/release-asset distribution is also `v1.9.10`" not in skill
    assert "- Current release tag: `v1.9.12`." in skill
    assert "tensor-grep==1.9.12 tg --version` reports `tensor-grep 1.9.12`" in skill
    assert "tensor-grep==1.9.10" not in skill
    for relative in ("AGENTS.md", "docs/SESSION_HANDOFF.md", "docs/CONTINUATION_PLAN.md"):
        content = (root / relative).read_text(encoding="utf-8")
        assert "latest complete public PyPI/release-asset distribution is also `v1.9.12`" in content
        assert (
            "latest complete public PyPI/release-asset distribution is also `v1.9.10`"
            not in content
        )
    assert "current `v1.9.12` release line" in (root / "docs" / "CONTRACTS.md").read_text(
        encoding="utf-8"
    )


def test_stamp_release_assets_syncs_latest_release_labels(tmp_path):
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "docs").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.9.12"\n', encoding="utf-8"
    )
    (root / "scripts" / "tensor-grep.rb").write_text(
        (
            "class TensorGrep < Formula\n"
            '  TENSOR_GREP_VERSION = "1.9.12"\n'
            "  version TENSOR_GREP_VERSION\n"
            "end\n"
        ),
        encoding="utf-8",
    )
    (root / "scripts" / "oimiragieo.tensor-grep.yaml").write_text(
        "# Winget Manifest for tensor-grep v1.9.12\n"
        "PackageVersion: 1.9.12\n"
        "InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v1.9.12/tg-windows-amd64-cpu.exe\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "release_docs_current_tag: v1.9.12\n"
        "Latest tagged GitHub release: [`v1.9.10`](https://github.com/oimiragieo/tensor-grep/releases/tag/v1.9.10).\n"
        "Latest complete PyPI release: [`v1.9.10`](https://github.com/oimiragieo/tensor-grep/releases/tag/v1.9.10).\n",
        encoding="utf-8",
    )
    for relative in (
        "AGENTS.md",
        "SKILL.md",
        "docs/CONTINUATION_PLAN.md",
        "docs/CONTRACTS.md",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("release_docs_current_tag: v1.9.12\n", encoding="utf-8")
    (root / "docs" / "SESSION_HANDOFF.md").write_text(
        "release_docs_current_tag: v1.9.12\n"
        "- Latest tagged version: `v1.9.10`\n"
        "- Latest complete PyPI version: `v1.9.10`\n",
        encoding="utf-8",
    )

    module = _load_module(Path(__file__).resolve().parents[2])
    module.ROOT = root

    assert module.stamp_assets(check_only=False) == 0
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Latest tagged GitHub release: [`v1.9.12`]" in readme
    assert "Latest complete PyPI release: [`v1.9.12`]" in readme
    assert "/releases/tag/v1.9.12" in readme
    handoff = (root / "docs" / "SESSION_HANDOFF.md").read_text(encoding="utf-8")
    assert "- Latest tagged version: `v1.9.12`" in handoff
    assert "- Latest complete PyPI version: `v1.9.12`" in handoff


def test_stamp_release_assets_preserves_verified_release_proof_blocks(tmp_path):
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "docs").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.9.12"\n', encoding="utf-8"
    )
    (root / "scripts" / "tensor-grep.rb").write_text(
        (
            "class TensorGrep < Formula\n"
            '  TENSOR_GREP_VERSION = "1.9.12"\n'
            "  version TENSOR_GREP_VERSION\n"
            "end\n"
        ),
        encoding="utf-8",
    )
    (root / "scripts" / "oimiragieo.tensor-grep.yaml").write_text(
        "# Winget Manifest for tensor-grep v1.9.12\n"
        "PackageVersion: 1.9.12\n"
        "InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v1.9.12/tg-windows-amd64-cpu.exe\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "release_docs_current_tag: v1.9.10\n"
        "Latest tagged GitHub release: [`v1.9.10`](https://github.com/oimiragieo/tensor-grep/releases/tag/v1.9.10).\n"
        "Latest complete PyPI release: [`v1.9.10`](https://github.com/oimiragieo/tensor-grep/releases/tag/v1.9.10).\n"
        "Latest verified release proof: `v1.9.9` completed in main CI run `123`; CodeQL run `456` passed.\n\n"
        "What `v1.9.9` closed:\n\n"
        "- GitHub release assets for `v1.9.9` include native CPU front doors.\n",
        encoding="utf-8",
    )
    for relative in (
        "AGENTS.md",
        "SKILL.md",
        "docs/SESSION_HANDOFF.md",
        "docs/CONTINUATION_PLAN.md",
        "docs/CONTRACTS.md",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("release_docs_current_tag: v1.9.10\n", encoding="utf-8")

    module = _load_module(Path(__file__).resolve().parents[2])
    module.ROOT = root

    assert module.stamp_assets(check_only=False) == 0
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Latest tagged GitHub release: [`v1.9.12`]" in readme
    assert "Latest complete PyPI release: [`v1.9.12`]" in readme
    assert "Latest verified release proof: `v1.9.9`" in readme
    assert "What `v1.9.9` closed:" in readme
    assert "GitHub release assets for `v1.9.9`" in readme
    assert "Latest verified release proof: `v1.9.12`" not in readme
    assert "What `v1.9.12` closed:" not in readme
    assert "GitHub release assets for `v1.9.12`" not in readme


def test_stamp_release_assets_syncs_gpu_dogfood_live_pointers_only(tmp_path):
    # Regression test for audit #71/#73: the old unanchored `post-`vX`` sweep rewrote EVERY
    # occurrence of the phrase on every release, including dated historical notes in
    # docs/PAPER.md and dated audit entries in docs/gpu_crossover.md, silently marching a frozen
    # historical version forward release after release (e.g. a 2026-05-14 note ending up stamped
    # `post-`v1.51.4``, a much later release). The fix anchors the sweep to the small number of
    # genuine "current state" live-pointer line shapes (verified against real doc history to be
    # periodically hand-refreshed, not frozen) that `scripts/agent_readiness.py`'s
    # `gpu_fragments` check depends on -- those still advance -- while a dated historical note
    # never matches any anchor and is left alone.
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "docs").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.9.12"\n', encoding="utf-8"
    )
    (root / "scripts" / "tensor-grep.rb").write_text(
        (
            "class TensorGrep < Formula\n"
            '  TENSOR_GREP_VERSION = "1.9.12"\n'
            "  version TENSOR_GREP_VERSION\n"
            "end\n"
        ),
        encoding="utf-8",
    )
    (root / "scripts" / "oimiragieo.tensor-grep.yaml").write_text(
        "# Winget Manifest for tensor-grep v1.9.12\n"
        "PackageVersion: 1.9.12\n"
        "InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v1.9.12/tg-windows-amd64-cpu.exe\n",
        encoding="utf-8",
    )
    for relative in (
        "AGENTS.md",
        "SKILL.md",
        "docs/SESSION_HANDOFF.md",
        "docs/CONTINUATION_PLAN.md",
        "docs/CONTRACTS.md",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("release_docs_current_tag: v1.9.12\n", encoding="utf-8")

    live_pointer_lines = "\n".join([
        "## Current post-`v1.9.10` GPU dogfood Read",
        "",
        "The post-`v1.9.10` dogfood keeps public GPU not promotion-ready.",
        "",
        "- Latest post-`v1.9.10` dogfood tool comparison medians (3 samples): "
        "standard corpus `rg 0.087s`, `tg 0.097s`.",
        "",
        "`benchmarks/run_agent_workflow_benchmarks.py` is the canonical workflow benchmark for "
        "the post-`v1.9.10` dogfood wedge: agent capsule routing plus safe edit-loop execution.",
    ])
    historical_note = (
        "> post-`v1.2.3` dogfood GPU performance note (2020-01-01): this dated historical note "
        "must never be rewritten by a later release stamp."
    )
    gpu_doc_content = f"{live_pointer_lines}\n\n{historical_note}\n"
    for relative in (
        "README.md",
        "docs/benchmarks.md",
        "docs/gpu_crossover.md",
        "docs/PAPER.md",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(gpu_doc_content, encoding="utf-8")

    module = _load_module(Path(__file__).resolve().parents[2])
    module.ROOT = root

    assert module.stamp_assets(check_only=True) == 1
    assert module.stamp_assets(check_only=False) == 0
    for relative in (
        "README.md",
        "docs/benchmarks.md",
        "docs/gpu_crossover.md",
        "docs/PAPER.md",
    ):
        content = (root / relative).read_text(encoding="utf-8")
        # The four anchored live-pointer shapes advance to the current tag.
        assert "## Current post-`v1.9.12` GPU dogfood Read" in content
        assert "The post-`v1.9.12` dogfood keeps public GPU not promotion-ready." in content
        assert "- Latest post-`v1.9.12` dogfood tool comparison medians" in content
        assert "canonical workflow benchmark for the post-`v1.9.12` dogfood wedge:" in content
        assert "v1.9.10" not in content
        # The dated historical note is untouched: it keeps its own frozen version and date
        # instead of being silently marched forward to the new release tag.
        assert historical_note in content
        assert "post-`v1.9.12` dogfood GPU performance note" not in content


def test_stamp_release_assets_does_not_touch_undated_prose_without_a_live_pointer_shape(
    tmp_path,
):
    # A doc with a `post-`vX`` occurrence that matches none of the four anchored live-pointer
    # shapes (e.g. a plain narrative sentence, not the canonical GPU dogfood header/bullet
    # forms) must be left alone rather than rewritten by a broad/unanchored fallback.
    root = tmp_path
    (root / "scripts").mkdir()
    (root / "docs").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "tensor-grep"\nversion = "1.9.12"\n', encoding="utf-8"
    )
    (root / "scripts" / "tensor-grep.rb").write_text(
        (
            "class TensorGrep < Formula\n"
            '  TENSOR_GREP_VERSION = "1.9.12"\n'
            "  version TENSOR_GREP_VERSION\n"
            "end\n"
        ),
        encoding="utf-8",
    )
    (root / "scripts" / "oimiragieo.tensor-grep.yaml").write_text(
        "# Winget Manifest for tensor-grep v1.9.12\n"
        "PackageVersion: 1.9.12\n"
        "InstallerUrl: https://github.com/oimiragieo/tensor-grep/releases/download/v1.9.12/tg-windows-amd64-cpu.exe\n",
        encoding="utf-8",
    )
    for relative in (
        "AGENTS.md",
        "SKILL.md",
        "docs/SESSION_HANDOFF.md",
        "docs/CONTINUATION_PLAN.md",
        "docs/CONTRACTS.md",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("release_docs_current_tag: v1.9.12\n", encoding="utf-8")
    unanchored_sentence = (
        "The active post-`v1.2.3` branch moves stable script installs toward a release-native "
        "public front door.\n"
    )
    for relative in (
        "README.md",
        "docs/benchmarks.md",
        "docs/gpu_crossover.md",
        "docs/PAPER.md",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(unanchored_sentence, encoding="utf-8")

    module = _load_module(Path(__file__).resolve().parents[2])
    module.ROOT = root
    module.stamp_assets(check_only=False)

    for relative in (
        "README.md",
        "docs/benchmarks.md",
        "docs/gpu_crossover.md",
        "docs/PAPER.md",
    ):
        content = (root / relative).read_text(encoding="utf-8")
        assert content == unanchored_sentence
