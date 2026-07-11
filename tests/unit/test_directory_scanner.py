from tensor_grep.core.config import SearchConfig
from tensor_grep.io.directory_scanner import DirectoryScanner


class TestDirectoryScanner:
    def test_should_skip_gitignored_directories_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tensor_grep.io.directory_scanner.HAS_RUST_SCANNER", False)

        (tmp_path / ".gitignore").write_text("build/\nnode_modules/\n", encoding="utf-8")
        src_dir = tmp_path / "src"
        build_dir = tmp_path / "build"
        node_dir = tmp_path / "node_modules"
        src_dir.mkdir()
        build_dir.mkdir()
        node_dir.mkdir()

        kept = src_dir / "keep.py"
        ignored_build = build_dir / "ignored.py"
        ignored_node = node_dir / "ignored.js"
        kept.write_text("ok", encoding="utf-8")
        ignored_build.write_text("ignore", encoding="utf-8")
        ignored_node.write_text("ignore", encoding="utf-8")

        files = list(DirectoryScanner(SearchConfig()).walk(str(tmp_path)))

        assert files == [str(kept)]

    def test_should_honor_nested_gitignore(self, tmp_path, monkeypatch):
        # MED-5: a nested subdir/.gitignore must be honored, not just the root one.
        monkeypatch.setattr("tensor_grep.io.directory_scanner.HAS_RUST_SCANNER", False)

        (tmp_path / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / ".gitignore").write_text("secret.txt\n", encoding="utf-8")

        kept = sub / "keep.py"
        nested_ignored = sub / "secret.txt"
        root_ignored = tmp_path / "junk.tmp"
        kept.write_text("ok", encoding="utf-8")
        nested_ignored.write_text("no", encoding="utf-8")
        root_ignored.write_text("no", encoding="utf-8")

        files = list(DirectoryScanner(SearchConfig()).walk(str(tmp_path)))

        assert str(kept) in files
        assert str(nested_ignored) not in files  # nested .gitignore honored
        assert str(root_ignored) not in files  # root .gitignore still honored

    def test_should_honor_nested_gitignore_negation_reinclude(self, tmp_path, monkeypatch):
        # MED-5: a deeper `!re-include` must override a parent ignore (git precedence).
        monkeypatch.setattr("tensor_grep.io.directory_scanner.HAS_RUST_SCANNER", False)

        (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / ".gitignore").write_text("!keep.log\n", encoding="utf-8")

        reincluded = sub / "keep.log"
        still_ignored = sub / "other.log"
        root_ignored = tmp_path / "root.log"
        for created in (reincluded, still_ignored, root_ignored):
            created.write_text("x", encoding="utf-8")

        files = list(DirectoryScanner(SearchConfig()).walk(str(tmp_path)))

        assert str(reincluded) in files  # nested !keep.log re-includes it
        assert str(still_ignored) not in files  # parent *.log still applies
        assert str(root_ignored) not in files

    def test_should_include_gitignored_directories_when_no_ignore_is_enabled(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("tensor_grep.io.directory_scanner.HAS_RUST_SCANNER", False)

        (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        ignored_file = build_dir / "ignored.py"
        ignored_file.write_text("ignore", encoding="utf-8")

        files = list(DirectoryScanner(SearchConfig(no_ignore=True)).walk(str(tmp_path)))

        assert str(ignored_file) in files

    def test_should_skip_generated_claude_context_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tensor_grep.io.directory_scanner.HAS_RUST_SCANNER", False)

        claude_dir = tmp_path / ".claude"
        context_dir = claude_dir / "context"
        lib_dir = claude_dir / "lib"
        context_dir.mkdir(parents=True)
        lib_dir.mkdir(parents=True)
        generated_file = context_dir / "snapshot.json"
        source_file = lib_dir / "utils.js"
        generated_file.write_text("safeParseJSON\n", encoding="utf-8")
        source_file.write_text("safeParseJSON\n", encoding="utf-8")

        files = list(DirectoryScanner(SearchConfig()).walk(str(claude_dir)))

        assert str(source_file) in files
        assert str(generated_file) not in files

    def test_should_include_generated_claude_context_when_no_ignore_is_enabled(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("tensor_grep.io.directory_scanner.HAS_RUST_SCANNER", False)

        context_dir = tmp_path / ".claude" / "context"
        context_dir.mkdir(parents=True)
        generated_file = context_dir / "snapshot.json"
        generated_file.write_text("safeParseJSON\n", encoding="utf-8")

        files = list(DirectoryScanner(SearchConfig(no_ignore=True)).walk(str(tmp_path / ".claude")))

        assert str(generated_file) in files

    def test_should_filterGlob_when_dashG_provided(self, tmp_path):
        import os

        os.makedirs(tmp_path / "src")

        file1 = tmp_path / "src" / "test.js"
        file2 = tmp_path / "src" / "test.ts"
        file3 = tmp_path / "ignore.js"

        file1.write_text("a")
        file2.write_text("a")
        file3.write_text("a")

        config = SearchConfig(glob=["*.js"])
        scanner = DirectoryScanner(config)

        files = list(scanner.walk(str(tmp_path)))

        assert len(files) == 2
        assert str(file1) in files
        assert str(file3) in files
        assert str(file2) not in files

    def test_should_filterGlob_case_insensitively_when_requested(self, tmp_path):
        file1 = tmp_path / "test.TXT"
        file2 = tmp_path / "test.py"

        file1.write_text("a")
        file2.write_text("a")

        config = SearchConfig(glob=["*.txt"], glob_case_insensitive=True)
        scanner = DirectoryScanner(config)

        files = list(scanner.walk(str(tmp_path)))

        assert files == [str(file1)]

    def test_should_preserve_recursive_glob_matching_when_case_folded(self, tmp_path):
        nested = tmp_path / "sub"
        nested.mkdir()
        file1 = nested / "sample.TXT"
        file2 = nested / "sample.py"

        file1.write_text("a")
        file2.write_text("a")

        config = SearchConfig(glob=["**/sample.txt"], glob_case_insensitive=True)
        scanner = DirectoryScanner(config)

        files = list(scanner.walk(str(tmp_path)))

        assert files == [str(file1)]

    def test_should_match_recursive_glob_against_relative_posix_paths(self, tmp_path):
        scripts_dir = tmp_path / "scripts" / "agents"
        tests_dir = tmp_path / "tests" / "scripts"
        scripts_dir.mkdir(parents=True)
        tests_dir.mkdir(parents=True)

        agent_file = scripts_dir / "worker.mjs"
        test_file = tests_dir / "worker.test.mjs"
        agent_file.write_text("runCursorWorker()\n", encoding="utf-8")
        test_file.write_text("runCursorWorker()\n", encoding="utf-8")

        config = SearchConfig(glob=["scripts/agents/**"])
        scanner = DirectoryScanner(config)

        files = list(scanner.walk(str(tmp_path)))

        assert files == [str(agent_file)]

    def test_should_filterType_when_dashT_provided(self, tmp_path):
        import os

        os.makedirs(tmp_path / "src")

        file1 = tmp_path / "src" / "test.js"
        file2 = tmp_path / "src" / "test.ts"

        file1.write_text("a")
        file2.write_text("a")

        config = SearchConfig(file_type=["ts"])
        scanner = DirectoryScanner(config)

        files = list(scanner.walk(str(tmp_path)))

        assert len(files) == 1
        assert str(file2) in files

    def test_should_excludeType_when_dashT_not_provided(self, tmp_path):
        import os

        os.makedirs(tmp_path / "src")

        file1 = tmp_path / "src" / "test.js"
        file2 = tmp_path / "src" / "test.ts"

        file1.write_text("a")
        file2.write_text("a")

        config = SearchConfig(type_not=["ts"])
        scanner = DirectoryScanner(config)

        files = list(scanner.walk(str(tmp_path)))

        assert len(files) == 1
        assert str(file1) in files
