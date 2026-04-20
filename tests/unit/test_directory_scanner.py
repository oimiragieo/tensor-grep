from tensor_grep.core.config import SearchConfig
from tensor_grep.io.directory_scanner import DirectoryScanner


class TestDirectoryScanner:
    def test_should_delegate_directory_walks_to_rust_scanner_when_available(
        self, tmp_path, monkeypatch
    ):
        seen: dict[str, object] = {}
        rust_file = tmp_path / "from_rust.py"
        rust_file.write_text("ok", encoding="utf-8")

        class _FakeRustDirectoryScanner:
            def __init__(self, *, hidden, max_depth):
                seen["hidden"] = hidden
                seen["max_depth"] = max_depth

            def walk(self, path_str):
                seen["path_str"] = path_str
                yield str(rust_file)

        monkeypatch.setattr("tensor_grep.io.directory_scanner.HAS_RUST_SCANNER", True)
        monkeypatch.setattr(
            "tensor_grep.io.directory_scanner.RustDirectoryScanner",
            _FakeRustDirectoryScanner,
            raising=False,
        )

        files = list(DirectoryScanner(SearchConfig()).walk(str(tmp_path)))

        assert files == [str(rust_file)]
        assert seen == {"hidden": False, "max_depth": None, "path_str": str(tmp_path)}

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
