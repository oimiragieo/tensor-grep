"""Release metadata must not republish arbitrary commit text."""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_configured_release_generator_does_not_publish_commit_text(tmp_path):
    pytest.importorskip("semantic_release")
    from git import Repo
    from semantic_release.changelog.release_history import ReleaseHistory
    from semantic_release.cli.changelog_writer import generate_release_notes
    from semantic_release.commit_parser import AngularCommitParser
    from semantic_release.hvcs import Github
    from semantic_release.version.translator import VersionTranslator

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    template_relative = config["tool"]["semantic_release"]["changelog"]["template_dir"]
    shutil.copytree(ROOT / template_relative, tmp_path / template_relative)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "1.1.0"\n'
        "[tool.semantic_release.changelog]\ntemplate_dir = " + json.dumps(template_relative) + "\n",
        encoding="utf-8",
    )
    repo = Repo.init(tmp_path, initial_branch="main")
    repo.create_remote("origin", "https://github.com/example/example.git")
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Example")
        writer.set_value("user", "email", "example@example.invalid")
    repo.index.add(["pyproject.toml", template_relative])
    repo.index.commit("feat: PRIVATE_SUBJECT_SENTINEL\n\nPRIVATE_BODY_SENTINEL")
    repo.create_tag("v1.0.0")
    repo.index.commit("fix: PRIVATE_SUBJECT_SENTINEL_2\n\nPRIVATE_BODY_SENTINEL_2")
    repo.create_tag("v1.1.0")
    completed = subprocess.run(
        [sys.executable, "-m", "semantic_release", "changelog"],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    changelog = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    history = ReleaseHistory.from_git_history(repo, VersionTranslator(), AngularCommitParser())
    release = next(iter(history.released.values()))
    notes = generate_release_notes(
        Github("https://github.com/example/example.git"),
        release,
        tmp_path / template_relative,
        history,
        "angular",
        False,
    )
    for text in (changelog, notes):
        assert "## v1.1.0" in text
        assert "/releases/tag/v1.1.0" in text
        assert "PRIVATE_SUBJECT_SENTINEL" not in text
        assert "PRIVATE_BODY_SENTINEL" not in text
    assert "## v1.0.0" in changelog


def test_package_manifests_limit_private_configuration():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    exclusions = config["tool"]["maturin"]["exclude"]
    assert {"**/.env", "**/.env.*", "**/.claude/**", "**/.superpowers/**", "docs/audits/**"} <= set(
        exclusions
    )
    npm = json.loads((ROOT / "npm/package.json").read_text(encoding="utf-8"))
    assert set(npm["files"]) == {"bin/tg.js", "install.js", "LICENSE", "NOTICE"}


def test_source_distribution_excludes_private_files_even_when_tracked(tmp_path):
    pytest.importorskip("maturin")
    import tarfile

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    exclusions = config["tool"]["maturin"]["exclude"]
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["maturin>=1.5,<2"]\nbuild-backend = "maturin"\n'
        '[tool.maturin]\nbindings = "bin"\nexclude = ' + json.dumps(exclusions) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "publication-fixture"\nversion = "1.0.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.rs").write_text("fn main() {}\n", encoding="utf-8")
    private_paths = [
        ".env",
        ".env.production",
        ".claude/private.md",
        "docs/audits/private.md",
        ".superpowers/notes.md",
        "docs/design/private.md",
        "docs/superpowers/private.md",
        "docs/BACKLOG.md",
        "docs/TASK_BOARD.md",
        "GEMINI.md",
        "docs/PAPER.md",
        "docs/world_class_plan.md",
        "docs/FIX_PLAN_new.md",
        "docs/DOGFOOD_new.md",
        "features/private.md",
        "docs/architecture/native_embeddings.md",
    ]
    for relative in private_paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PRIVATE_FIXTURE_SENTINEL", encoding="utf-8")
    fixture = tmp_path / "benchmarks/fixtures/gemini/GEMINI.md"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("Public benchmark fixture", encoding="utf-8")
    with (tmp_path / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            "include = ["
            + ", ".join(
                "{path = " + json.dumps(path) + ', format = "sdist"}'
                for path in [*private_paths, "benchmarks/fixtures/gemini/GEMINI.md"]
            )
            + "]\n"
        )
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    subprocess.run(["git", "add", "--force", "."], cwd=tmp_path, check=True)
    completed = subprocess.run(
        [sys.executable, "-m", "maturin", "sdist", "--out", str(tmp_path / "dist")],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    archive = next((tmp_path / "dist").glob("*.tar.gz"))
    with tarfile.open(archive) as package:
        names = [name.split("/", 1)[-1] for name in package.getnames()]
    assert "src/main.rs" in names
    assert "benchmarks/fixtures/gemini/GEMINI.md" in names, completed.stderr
    assert not set(private_paths).intersection(names)
    # Positive control: the same packager includes every private fixture without exclusions.
    project = tmp_path / "pyproject.toml"
    project.write_text(
        project.read_text(encoding="utf-8").replace(
            "exclude = " + json.dumps(exclusions), "exclude = []"
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, "-m", "maturin", "sdist", "--out", str(tmp_path / "control")],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    with tarfile.open(next((tmp_path / "control").glob("*.tar.gz"))) as package:
        control_names = {name.split("/", 1)[-1] for name in package.getnames()}
    assert set(private_paths) <= control_names


def test_npm_archive_has_only_public_launcher_files(tmp_path):
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        if os.environ.get("TG_TEST_DOCKER_CONTEXT") == "1":
            pytest.fail("npm is required in the publication verification lane")
        pytest.skip("npm is unavailable")
    shutil.copy2(ROOT / "npm/package.json", tmp_path / "package.json")
    (tmp_path / "bin").mkdir()
    for relative in [
        "bin/tg.js",
        "install.js",
        "LICENSE",
        "NOTICE",
        ".env.production",
        "internal-notes.md",
    ]:
        (tmp_path / relative).write_text("fixture", encoding="utf-8")
    completed = subprocess.run(
        [npm, "pack", "--dry-run", "--json", "--ignore-scripts"],
        cwd=tmp_path,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    package = payload[0] if isinstance(payload, list) else next(iter(payload.values()))
    assert {item["path"] for item in package["files"]} == {
        "bin/tg.js",
        "install.js",
        "package.json",
        "LICENSE",
        "NOTICE",
    }


def test_actual_docker_context_excludes_private_files(tmp_path):
    if os.environ.get("TG_TEST_DOCKER_CONTEXT") != "1":
        pytest.skip("Set TG_TEST_DOCKER_CONTEXT=1 for the disposable Docker context check")
    docker = shutil.which("docker")
    assert docker, "Docker must be available when context verification is requested"
    context = tmp_path / "context"
    context.mkdir()
    shutil.copy2(ROOT / ".dockerignore", context / ".dockerignore")
    (context / "Dockerfile").write_text("FROM scratch\nCOPY . /\n", encoding="utf-8")
    private_paths = [
        ".env.production",
        ".claude/private.md",
        "docs/design/private.md",
        "docs/superpowers/private.md",
        "docs/BACKLOG.md",
        "docs/TASK_BOARD.md",
        "GEMINI.md",
        "docs/PAPER.md",
        "docs/FIX_PLAN_new.md",
        "docs/DOGFOOD_new.md",
        "features/private.md",
        "docs/architecture/native_embeddings.md",
    ]
    public = "benchmarks/fixtures/gemini/GEMINI.md"
    for relative in [*private_paths, public]:
        path = context / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Disposable fixture", encoding="utf-8")
    exported = tmp_path / "exported"
    completed = subprocess.run(
        [docker, "build", "--output", f"type=local,dest={exported}", str(context)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert (exported / public).is_file()
    assert not [relative for relative in private_paths if (exported / relative).exists()]


def _configured_docs_exclusions():
    config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    block = config.split("exclude_docs: |\n", 1)[1].split("\n\n", 1)[0]
    return "\n".join(line.removeprefix("  ") for line in block.splitlines()) + "\n"


def test_mkdocs_publication_inputs_match_reviewed_documentation():
    import ast

    tree = ast.parse((ROOT / "scripts/check_repo_hygiene.py").read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "APPROVED_DOC_FILES"
            for target in node.targets
        )
    )
    approved = ast.literal_eval(assignment.value.args[0])
    patterns = _configured_docs_exclusions().splitlines()
    assert patterns[0] == "**"
    assert set(patterns[1:]) == {"!/" + path.removeprefix("docs/") for path in approved}


def test_actual_mkdocs_site_excludes_untracked_private_documents(tmp_path):
    pytest.importorskip("mkdocs")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(
        "# Public documentation\nPUBLIC_FIXTURE_SENTINEL", encoding="utf-8"
    )
    private_paths = [
        "superpowers/private.md",
        "audits/private.md",
        "new-private-folder/private.md",
        "new-private-folder/index.md",
        "new-private-folder/benchmarks.md",
    ]
    for relative in private_paths:
        path = docs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Private notes\nPRIVATE_DOC_SENTINEL", encoding="utf-8")
    config = tmp_path / "mkdocs.yml"
    base = "site_name: Publication fixture\nnav:\n  - Home: index.md\n"
    config.write_text(
        base
        + "exclude_docs: |\n"
        + "".join("  " + line + "\n" for line in _configured_docs_exclusions().splitlines()),
        encoding="utf-8",
    )

    def build(destination):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mkdocs",
                "build",
                "--config-file",
                str(config),
                "--site-dir",
                str(destination),
            ],
            cwd=tmp_path,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return "\n".join(
            path.read_text(encoding="utf-8")
            for path in destination.rglob("*")
            if path.suffix in {".html", ".json"}
        )

    published = build(tmp_path / "site")
    assert "PUBLIC_FIXTURE_SENTINEL" in published
    assert "PRIVATE_DOC_SENTINEL" not in published
    config.write_text(base, encoding="utf-8")
    control = build(tmp_path / "control")
    assert "PRIVATE_DOC_SENTINEL" in control
