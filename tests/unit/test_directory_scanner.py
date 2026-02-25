from tensor_grep.core.config import SearchConfig
from tensor_grep.io.directory_scanner import DirectoryScanner


class TestDirectoryScanner:
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
