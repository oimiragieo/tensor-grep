from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


class StringZillaBackend(ComputeBackend):
    """
    A backend utilizing the StringZilla native C++/SIMD library.
    It specializes in ultra-fast exact string matching and line splitting,
    avoiding standard Python regex overhead completely for simple literal searches.
    """

    def is_available(self) -> bool:
        try:
            import importlib.util

            return importlib.util.find_spec("stringzilla") is not None
        except ImportError:
            return False

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        import stringzilla as sz

        try:
            # Read file via normal python IO for now, wrap in sz.Str
            # In a real implementation we might memory-map directly.
            with open(file_path, encoding="utf-8") as f_obj:
                content = f_obj.read()

            sz_str = sz.Str(content)

            # Since StringZilla 4.x, we can split by lines extremely fast
            lines = sz_str.splitlines()
            matches = []

            # Evaluate using stringzilla's native find
            for i, line in enumerate(lines):
                if line.find(pattern) != -1:
                    matches.append(MatchLine(line_number=i + 1, text=str(line), file=file_path))

            return SearchResult(
                matches=matches,
                total_files=1 if matches else 0,
                total_matches=len(matches),
            )

        except Exception as e:
            raise e
