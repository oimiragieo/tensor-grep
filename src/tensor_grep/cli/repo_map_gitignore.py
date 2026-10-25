from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


def _gitignore_pattern_to_regex(pattern: str) -> str:
    """Translate a single gitignore glob body to an anchored regex fragment.

    The fragment matches against a repo-relative POSIX path (no leading slash).
    Handles ``*`` (segment-local), ``**`` (cross-segment), ``?`` and literal
    characters; anchoring/negation/dir-only handling lives in the caller.
    """

    regex_parts: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 2] == "**":
                # ``**`` matches any number of path segments (including none).
                index += 2
                if pattern[index : index + 1] == "/":
                    index += 1
                    regex_parts.append("(?:.*/)?")
                else:
                    regex_parts.append(".*")
            else:
                regex_parts.append("[^/]*")
                index += 1
        elif char == "?":
            regex_parts.append("[^/]")
            index += 1
        else:
            regex_parts.append(re.escape(char))
            index += 1
    return "".join(regex_parts)


class _GitignoreMatcher:
    """Pragmatic ``.gitignore`` matcher covering the common pattern forms.

    Supports directory-only patterns (trailing ``/``), anchored patterns
    (leading or embedded ``/``), unanchored basename patterns, ``*``/``**``/``?``
    globs, and ``!`` negation. Paths are matched relative to ``root`` using
    POSIX separators. This intentionally mirrors ripgrep's gitignore handling
    closely enough that context/repo-map walks stop drowning in vendored and
    cache files; it is not a byte-perfect reimplementation of git's spec.
    """

    def __init__(self, root: Path, lines: list[str]) -> None:
        self._root = root.resolve()
        self._rules: list[tuple[re.Pattern[str], bool, bool]] = []
        for raw_line in lines:
            line = raw_line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            # A literal leading ``#`` or ``!`` is escaped with a backslash.
            negated = False
            if line.startswith("!"):
                negated = True
                line = line[1:]
            elif line.startswith(("\\#", "\\!")):
                line = line[1:]
            line = line.rstrip()
            if not line:
                continue
            dir_only = line.endswith("/")
            body = line[:-1] if dir_only else line
            anchored = body.startswith("/") or ("/" in body.rstrip("/"))
            body = body.lstrip("/")
            if not body:
                continue
            fragment = _gitignore_pattern_to_regex(body)
            if anchored:
                regex = rf"^{fragment}(?:/.*)?$"
            else:
                # Unanchored patterns match at any depth.
                regex = rf"(?:^|.*/){fragment}(?:/.*)?$"
            try:
                compiled = re.compile(regex)
            except re.error:
                continue
            self._rules.append((compiled, negated, dir_only))

    @property
    def has_rules(self) -> bool:
        return bool(self._rules)

    def check(self, path: Path, *, is_dir: bool) -> bool | None:
        # Tri-state result so nested .gitignore specs can be stacked with correct git
        # precedence: True = matched an ignore, False = matched a negated re-include, None =
        # no rule matched (this spec has no opinion). Match the path AS WALKED â€” never
        # ``resolve()`` here. ``self._root`` is resolved once in __init__ and the walk descends
        # from it, so every entry is already absolute and under the root. Calling
        # ``path.resolve()`` per entry would add a stat/symlink syscall for every file in the
        # tree â€” an O(files) regression on large roots (~384k files) and would follow symlinks,
        # which is not how gitignore matches paths.
        candidate = path if path.is_absolute() else (self._root / path)
        try:
            relative = candidate.relative_to(self._root)
        except ValueError:
            return None
        rel_posix = relative.as_posix()
        if not rel_posix or rel_posix == ".":
            return None
        # A dir-only pattern (``dist/``) ignores the directory *and everything
        # inside it*. Test the path itself plus each ancestor segment-prefix so
        # files under an ignored directory are recognised even when checked in
        # isolation (the walk also prunes such directories during traversal).
        segments = rel_posix.split("/")
        ancestor_prefixes = ["/".join(segments[: index + 1]) for index in range(len(segments) - 1)]
        decision: bool | None = None
        for compiled, negated, dir_only in self._rules:
            if dir_only:
                matched = any(compiled.match(prefix) for prefix in ancestor_prefixes)
                if is_dir:
                    matched = matched or bool(compiled.match(rel_posix))
            else:
                matched = bool(compiled.match(rel_posix))
                if not matched:
                    # An unanchored/file pattern still ignores descendants of a
                    # directory it matched (e.g. ``build`` matching ``build/x``).
                    matched = any(compiled.match(prefix) for prefix in ancestor_prefixes)
            if matched:
                decision = not negated
        return decision

    def is_ignored(self, path: Path, *, is_dir: bool) -> bool:
        return bool(self.check(path, is_dir=is_dir))


@lru_cache(maxsize=64)
def _load_gitignore_matcher(root_key: str) -> _GitignoreMatcher:
    root = Path(root_key)
    gitignore_path = root / ".gitignore"
    lines: list[str] = []
    try:
        if gitignore_path.is_file():
            lines = gitignore_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    return _GitignoreMatcher(root, lines)
