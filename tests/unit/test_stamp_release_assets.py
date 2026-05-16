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


def test_stamp_release_assets_syncs_gpu_dogfood_labels(tmp_path):
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
    for relative in (
        "README.md",
        "docs/benchmarks.md",
        "docs/gpu_crossover.md",
        "docs/PAPER.md",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("The post-`v1.9.10` GPU dogfood read.\n", encoding="utf-8")

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
        assert "post-`v1.9.12`" in content
        assert "post-`v1.9.10`" not in content
