import hashlib
import json
import logging
import os
import re
import time
import warnings
from collections import OrderedDict, deque
from pathlib import Path
from typing import ClassVar

from tensor_grep.backends.base import BackendExecutionError, ComputeBackend
from tensor_grep.cli.subprocess_policy import configured_ripgrep_timeout_seconds
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult

logger = logging.getLogger(__name__)
_CPU_LITERAL_INDEX_CACHE_MAX_ENTRIES_ENV = "TENSOR_GREP_CPU_LITERAL_INDEX_CACHE_MAX_ENTRIES"
_DEFAULT_CPU_LITERAL_INDEX_CACHE_MAX_ENTRIES = 512


def compute_native_walk_deadline() -> float:
    """Wall-clock deadline (``time.monotonic()`` epoch) for a native, in-process multi-file
    search walk (the CLI's per-file loop over ``CPUBackend``/``TorchBackend`` results).

    Reuses the SAME resolver ripgrep's own subprocess timeout uses
    (``subprocess_policy.configured_ripgrep_timeout_seconds``) so the native engine path is
    bounded by the identical budget as the rg route. Without this, a search that cannot
    route through rg (native ``--json`` aggregate, ``--rank``, tensor-only flags, or rg
    absent from PATH) has NO limit at all and can hang until manually killed on a
    large/unscoped tree -- the critical unscoped-search-hang bug (Fix B).
    """
    return time.monotonic() + configured_ripgrep_timeout_seconds()


def native_walk_deadline_exceeded(deadline: float) -> bool:
    return time.monotonic() >= deadline


class InvalidRegexError(ValueError):
    """Raised when regex syntax is invalid and fixed-string fallback was not requested."""


class _RustUtf8DecodeMismatch(RuntimeError):
    """Internal signal: Rust returned no matches on a non-UTF-8 file; retry via Python decode.

    Typed so the fallback handler can triage a non-UTF-8 retry (fall open, safe) apart from a
    Rust *syntax* rejection (ReDoS class, must fail closed) by exception type, not message luck.
    """


class CPUBackend(ComputeBackend):
    _shared_literal_index_cache: ClassVar[
        OrderedDict[tuple[str, bool], tuple[tuple[int, int], list[str], dict[str, list[int]]]]
    ] = OrderedDict()

    @classmethod
    def _clear_shared_caches(cls) -> None:
        cls._shared_literal_index_cache.clear()

    @staticmethod
    def _configured_positive_int(env_var: str, default: int) -> int:
        raw_value = os.environ.get(env_var)
        if raw_value is None:
            return default
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    @classmethod
    def _literal_index_cache_max_entries(cls) -> int:
        return cls._configured_positive_int(
            _CPU_LITERAL_INDEX_CACHE_MAX_ENTRIES_ENV,
            _DEFAULT_CPU_LITERAL_INDEX_CACHE_MAX_ENTRIES,
        )

    @classmethod
    def _remember_literal_index(
        cls,
        cache_key: tuple[str, bool],
        cache_entry: tuple[tuple[int, int], list[str], dict[str, list[int]]],
    ) -> None:
        cls._shared_literal_index_cache.pop(cache_key, None)
        cls._shared_literal_index_cache[cache_key] = cache_entry
        while len(cls._shared_literal_index_cache) > cls._literal_index_cache_max_entries():
            cls._shared_literal_index_cache.popitem(last=False)

    @staticmethod
    def _build_file_signature(file_path: str) -> tuple[int, int]:
        stat_result = Path(file_path).stat()
        return stat_result.st_mtime_ns, stat_result.st_size

    @staticmethod
    def _should_search_binary_as_text(config: SearchConfig | None) -> bool:
        return bool(config and (config.text or config.binary))

    @staticmethod
    def _is_binary_file(path: Path) -> bool:
        try:
            with open(path, "rb") as file_obj:
                return b"\x00" in file_obj.read(4096)
        except OSError:
            return False

    @staticmethod
    def _is_persistent_prefilter_enabled() -> bool:
        return os.environ.get("TENSOR_GREP_CPU_REGEX_INDEX", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    @staticmethod
    def _get_prefilter_cache_dir() -> Path:
        override = os.environ.get("TENSOR_GREP_CPU_REGEX_INDEX_DIR")
        if override:
            return Path(override).expanduser().resolve()
        if os.name == "nt":
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                return Path(local_appdata) / "tensor-grep" / "cpu-regex-index"
        xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache_home:
            return Path(xdg_cache_home) / "tensor-grep" / "cpu-regex-index"
        return Path.home() / ".cache" / "tensor-grep" / "cpu-regex-index"

    @classmethod
    def _get_prefilter_cache_path(cls, file_path: str, ignore_case: bool) -> Path:
        key = f"{Path(file_path).resolve()}::{int(ignore_case)}"
        digest = hashlib.sha256(key.encode()).hexdigest()
        return cls._get_prefilter_cache_dir() / f"{digest}.json"

    @staticmethod
    def _build_line_trigram_index(lines: list[str]) -> dict[str, list[int]]:
        index: dict[str, set[int]] = {}
        for line_idx, line in enumerate(lines):
            if len(line) < 3:
                continue
            for start in range(len(line) - 2):
                trigram = line[start : start + 3]
                index.setdefault(trigram, set()).add(line_idx)
        return {trigram: sorted(line_numbers) for trigram, line_numbers in index.items()}

    @staticmethod
    def _extract_required_literal(pattern: str) -> str | None:
        if any(token in pattern for token in ("|", "(", ")", "[", "]", "{", "}", "?", "+", "\\")):
            return None

        literals: list[str] = []
        current: list[str] = []
        for ch in pattern:
            if ch == "*":
                # The atom immediately before '*' is optional (zero-or-more) and must never be
                # folded into a required literal — e.g. "colou*r" matches "color" (zero u's), so
                # the required substring is "colo", not "colou". The guard above already excludes
                # groups/classes/alternation, so the atom is always the single trailing char.
                # Pop just that char (not the whole run) so "flagx*ok" still yields "flag". The
                # `if current` guards avoid IndexError when '*' leads or follows .^$* (".*abc").
                if current:
                    current.pop()
                if current:
                    literals.append("".join(current))
                current = []
                continue
            if ch in {".", "^", "$"}:
                if current:
                    literals.append("".join(current))
                    current = []
                continue
            current.append(ch)

        if current:
            literals.append("".join(current))

        literal = max(literals, key=len, default="")
        return literal if len(literal) >= 3 else None

    def _load_literal_index(
        self, file_path: str, ignore_case: bool
    ) -> tuple[list[str], dict[str, list[int]]] | None:
        cache_key = (file_path, ignore_case)
        cache_signature = self._build_file_signature(file_path)
        cached = self._shared_literal_index_cache.get(cache_key)
        if cached and cached[0] == cache_signature:
            self._shared_literal_index_cache.move_to_end(cache_key)
            return cached[1], cached[2]
        if cached:
            self._shared_literal_index_cache.pop(cache_key, None)
        if not self._is_persistent_prefilter_enabled():
            return None
        cache_path = self._get_prefilter_cache_path(file_path, ignore_case)
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("file_signature") != list(cache_signature):
            return None
        raw_lines = payload.get("lines")
        raw_index = payload.get("trigram_index")
        if not isinstance(raw_lines, list) or not isinstance(raw_index, dict):
            return None
        lines = [str(line) for line in raw_lines]
        trigram_index: dict[str, list[int]] = {}
        for trigram, values in raw_index.items():
            if not isinstance(trigram, str) or not isinstance(values, list):
                return None
            trigram_index[trigram] = [int(v) for v in values]
        self._remember_literal_index(cache_key, (cache_signature, lines, trigram_index))
        return lines, trigram_index

    def _store_literal_index(
        self,
        file_path: str,
        ignore_case: bool,
        lines: list[str],
        trigram_index: dict[str, list[int]],
    ) -> None:
        cache_signature = self._build_file_signature(file_path)
        self._remember_literal_index(
            (file_path, ignore_case),
            (
                cache_signature,
                lines,
                trigram_index,
            ),
        )
        if not self._is_persistent_prefilter_enabled():
            return
        cache_path = self._get_prefilter_cache_path(file_path, ignore_case)
        payload = {
            "file_signature": list(cache_signature),
            "lines": lines,
            "trigram_index": trigram_index,
        }
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            return

    @classmethod
    def _candidate_line_indexes(
        cls, trigram_index: dict[str, list[int]], literal: str
    ) -> list[int]:
        trigrams = [literal[i : i + 3] for i in range(len(literal) - 2)]
        candidate_sets = []
        for trigram in trigrams:
            line_numbers = trigram_index.get(trigram)
            if not line_numbers:
                return []
            candidate_sets.append(set(line_numbers))
        return sorted(set.intersection(*candidate_sets)) if candidate_sets else []

    @staticmethod
    def _compile_regexes(
        pattern: str, flags: int, config: SearchConfig
    ) -> tuple[re.Pattern[str], re.Pattern[bytes]]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            try:
                if config.fixed_strings:
                    escaped = re.escape(pattern)
                    return re.compile(escaped, flags), re.compile(escaped.encode("utf-8"), flags)
                if config.line_regexp:
                    wrapped = f"^{pattern}$"
                    return re.compile(wrapped, flags), re.compile(wrapped.encode(), flags)
                if config.word_regexp:
                    wrapped = f"\\b{pattern}\\b"
                    return re.compile(wrapped, flags), re.compile(wrapped.encode(), flags)
                return re.compile(pattern, flags), re.compile(pattern.encode("utf-8"), flags)
            except re.error as exc:
                raise InvalidRegexError(f"invalid regex pattern: {exc}") from exc

    @staticmethod
    def _fallback_pattern_is_provably_linear(config: SearchConfig) -> bool:
        """Gate for EVERY path that could re-run a pattern through Python's backtracking `re`
        after the linear-time Rust engine declined/failed (audit #111 + Opus-gate hardening;
        sibling to the audit #6/#16 fixes in `_search_word_line_context_via_rust` / `_search_ltl`
        / the `--pcre2` residual).

        The ONLY pattern shape this backend can prove is safe for Python's backtracking engine is
        `fixed_strings`: `_compile_regexes` runs those through `re.escape`, producing a literal
        automaton with no quantifier and no alternation, which cannot catastrophically backtrack
        regardless of the raw pattern text. EVERY other pattern fails closed.

        No STATIC analysis of the raw pattern is or can be the gate. "Rust already ran this
        pattern in O(n)" is not evidence Python's `re` can run the SAME pattern safely (the
        premise already refuted for `--pcre2`: nested quantifiers like `(a+)+$` are valid,
        linear-time-safe Rust syntax that catastrophically backtracks under Python's engine). And
        an earlier attempt to admit "patterns with no quantifier metacharacter (`*+?{`)" was
        PROVABLY UNSOUND -- catastrophic backtracking has a second source besides repetition:
        variable-length ALTERNATION. `(a|aa)(a|aa)...(a|aa)b` (i.e. `"(a|aa)"*k + "b"`) contains
        no quantifier char at all yet backtracks 2^k, so any static char allow-list is a bypass
        waiting to be dialed. Only the structural `fixed_strings` guarantee is sound.
        """
        return bool(config.fixed_strings)

    def is_available(self) -> bool:
        return True

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        routing_reason = "cpu_python_regex"
        if config is None:
            from tensor_grep.core.config import SearchConfig

            config = SearchConfig()

        # A `--max-count 0` request means "return zero matches" (ripgrep's contract, matched by the
        # rg-routed path and the Rust-delegated `rust_results[: config.max_count]` slice below). The
        # pure-Python loops check the cap AFTER appending, and `config.max_count and ...` treats a 0
        # cap as falsy -- so without this guard `-m 0` on the pure-Python path (forced by
        # -C/-A/-B/-w/-x or an LTL query) would emit EVERY match instead of none. Short-circuit here
        # so all three CPUBackend paths agree on zero.
        if config.max_count == 0:
            return SearchResult(
                matches=[],
                total_files=0,
                total_matches=0,
                routing_backend="CPUBackend",
                routing_reason="cpu_max_count_zero",
                routing_distributed=False,
                routing_worker_count=1,
            )

        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return SearchResult(
                matches=[],
                total_files=0,
                total_matches=0,
                routing_backend="CPUBackend",
                routing_reason="cpu_missing_file",
                routing_distributed=False,
                routing_worker_count=1,
            )

        if not self._should_search_binary_as_text(config) and self._is_binary_file(path):
            return SearchResult(
                matches=[],
                total_files=0,
                total_matches=0,
                routing_backend="CPUBackend",
                routing_reason="cpu_binary_skipped",
                routing_distributed=False,
                routing_worker_count=1,
            )

        if config.ltl:
            result = self._search_ltl(path, pattern, config)
            result.routing_backend = "CPUBackend"
            result.routing_reason = "cpu_ltl_python"
            result.routing_distributed = False
            result.routing_worker_count = 1
            return result

        # ReDoS Protection:
        # Instead of using Python's standard `re` module (which uses backtracking and is vulnerable
        # to ReDoS attacks), we route complex pure-python CPU requests to the native Rust `regex` crate.
        # Rust's regex engine uses Finite Automata which mathematically guarantees O(m) linear time execution.
        #
        # Audit #6 (ReDoS gate bypass): -C/-A/-B/-w/-x used to disable this Rust attempt entirely
        # (the old `rust_semantics_supported` gate below) and drop straight to the unbounded
        # backtracking Python loop further down -- e.g. `(a+)+$` via `-w`/`-C` could hang
        # forever, even though the SAME pattern via a plain (no -C/-w/-x) search is safe. These
        # flags now route through `_search_word_line_context_via_rust`, which resolves the
        # MATCH-SET via the linear Rust engine and assembles context windows / applies -w/-x
        # wrapping in pure Python (no regex evaluation at all on that side) -- see that method's
        # docstring for the full rationale, including why it fails closed instead of falling
        # open to Python `re` on its residual (Rust absent / --pcre2).
        needs_word_or_context_rust_routing = bool(
            getattr(config, "context", False)
            or getattr(config, "before_context", False)
            or getattr(config, "after_context", False)
            or getattr(config, "line_regexp", False)
            or getattr(config, "word_regexp", False)
        )
        if needs_word_or_context_rust_routing:
            return self._search_word_line_context_via_rust(path, file_path, pattern, config)

        # Every request reaching this point is the "simple" pattern case (no -C/-A/-B/-w/-x --
        # those already returned above via `_search_word_line_context_via_rust`, and --ltl
        # returned even earlier); always attempt the linear-time Rust engine first.
        try:
            from tensor_grep.rust_core import RustBackend

            rust_backend = RustBackend()
            try:
                rust_results = rust_backend.search(
                    pattern=pattern,
                    path=file_path,
                    ignore_case=config.ignore_case or (config.smart_case and pattern.islower()),
                    fixed_strings=config.fixed_strings,
                    invert_match=config.invert_match,
                )
            except TypeError:
                rust_results = rust_backend.search(
                    pattern=pattern,
                    path=file_path,
                    ignore_case=config.ignore_case or (config.smart_case and pattern.islower()),
                    fixed_strings=config.fixed_strings,
                )

            # If Rust returns no matches on a file that is not valid UTF-8, fall back to Python
            # decoding path (latin-1/replace) for compatibility.
            if not rust_results:
                try:
                    Path(file_path).read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise _RustUtf8DecodeMismatch(
                        "Rust backend UTF-8 decode mismatch, using Python fallback"
                    ) from exc

            if config.max_count is not None:
                rust_results = rust_results[: config.max_count]

            matches = [
                MatchLine(line_number=r[0], text=str(r[1]).rstrip("\n\r"), file=file_path)
                for r in rust_results
            ]

            return SearchResult(
                matches=matches,
                total_files=1 if matches else 0,
                total_matches=len(matches),
                routing_backend="CPUBackend",
                routing_reason="cpu_rust_regex",
                routing_distributed=False,
                routing_worker_count=1,
            )

        except _RustUtf8DecodeMismatch as exc:
            # Audit #111 (ReDoS gate bypass, third instance -- sibling to #6/#16 above): this
            # branch used to assume "Rust already compiled + ran the pattern in O(n), so it's
            # ReDoS-safe" and fall through UNCONDITIONALLY to the Python latin-1/replace decode
            # loop below. That premise is the SAME one already refuted for `--pcre2` two blocks
            # down: nested quantifiers like `(a+)+$` are valid, linear-time-safe Rust syntax that
            # catastrophically backtracks under Python's backtracking `re` -- and, as the Opus
            # security gate proved, so does quantifier-free variable-length ALTERNATION
            # (`(a|aa)...(a|aa)b` backtracks 2^k with no `*+?{` char), so NO static pattern check
            # is a sound gate. The only shape provably safe for the Python fallback is
            # `fixed_strings` (re.escape'd -> literal automaton). Fail CLOSED for everything else
            # (Backend Fail-Closed Contract, matching the -w/-x/-C/--ltl/--pcre2 siblings) rather
            # than silently swapping to the ReDoS-hazardous backtracking engine. This does fail
            # closed a legit non-ASCII regex on a non-UTF-8 file (e.g. `caf\xe9\d+` on latin-1) --
            # the correct security-over-availability trade; such users can pass --fixed-strings
            # or use ripgrep (a genuine byte-safe engine).
            if not self._fallback_pattern_is_provably_linear(config):
                raise BackendExecutionError(
                    "cannot safely evaluate this non-fixed-strings pattern through CPUBackend's "
                    f"non-UTF-8-file Python fallback ({type(exc).__name__}: {exc}); its "
                    "backtracking engine has no linear-time guarantee for an arbitrary pattern; "
                    "use ripgrep (a genuine byte-safe engine) or pass --fixed-strings"
                ) from exc
            # fixed_strings only: re.escape'd -> provably linear -> safe to decode the file in
            # Python (latin-1/replace) and match the literal as text.
            logger.debug(
                "Rust UTF-8 decode mismatch for %s, using Python decode (fixed-strings): %s",
                file_path,
                exc,
            )
        except (ImportError, ModuleNotFoundError) as exc:
            # Native extension genuinely absent: Python `re` is the ONLY available engine.
            # Availability over ReDoS-strictness for this environment condition (expected).
            logger.debug("rust_core unavailable for %s, using Python regex: %s", file_path, exc)
        except Exception as exc:
            # Lazy import avoids a circular import (rust_backend imports InvalidRegexError from
            # this module); by call time both modules are fully loaded. Single source of truth.
            from tensor_grep.backends.rust_backend import _is_invalid_regex_error

            if _is_invalid_regex_error(exc) and not getattr(config, "pcre2", False):
                # Rust rejected the pattern's SYNTAX (backreference / look-around) — the
                # canonical ReDoS class. Refuse to run it through the backtracking Python
                # engine; direct the user to the explicit --pcre2 opt-in (mirrors ripgrep -P).
                raise InvalidRegexError(
                    "pattern needs backreference/look-around syntax the linear-time engine "
                    f"rejects; pass --pcre2 to opt into the backtracking engine explicitly ({exc})"
                ) from exc
            if getattr(config, "pcre2", False):
                # Audit #16: --pcre2 is a "Python-re-is-unavoidable" residual -- CPUBackend
                # has no real PCRE2 engine, only Python `re` as an approximation, and Python
                # `re` is backtracking (ReDoS-hazardous) regardless of WHY Rust could not
                # service the request. Previously this fell open silently on the premise
                # that "Rust accepted the syntax, so the pattern provably contains no
                # catastrophic-backtracking construct" -- that premise is FALSE (nested
                # quantifiers like `(a+)+$` are valid Rust syntax that Rust's automata engine
                # runs in guaranteed O(n), but the exact construct that blows up Python's
                # backtracking engine). Fail closed (Backend Fail-Closed Contract) instead of
                # silently swapping to a ReDoS-hazardous engine; direct users who need real
                # PCRE2 semantics to ripgrep itself (which has a genuine PCRE2 engine).
                raise BackendExecutionError(
                    "cannot safely evaluate this pattern through CPUBackend's --pcre2 "
                    f"approximation ({type(exc).__name__}: {exc}); use ripgrep for real "
                    "PCRE2 support or drop --pcre2"
                ) from exc
            # Rust failed at runtime for a reason unrelated to pattern syntax (native panic /
            # IO / version skew) and the caller did NOT request --pcre2. Opus-gate hardening
            # (audit #111, must-fix #2): this used to fall open to Python `re` "for robustness",
            # but that is the NEXT ReDoS hole -- a hazard pattern (`(a+)+$` OR the quantifier-free
            # alternation bomb `(a|aa)...b`) would then backtrack unbounded whenever Rust hit a
            # transient runtime fault. We cannot prove an arbitrary pattern safe for the
            # backtracking engine, so fail CLOSED unless it is a `fixed_strings` literal
            # (re.escape'd -> provably linear), matching the -w/-x/-C/--ltl/--pcre2 siblings which
            # all fail closed on ANY Rust failure. (Genuine Rust ABSENCE is handled by the
            # ImportError branch above, which still falls open -- Python is then the only engine,
            # a dev/broken-install condition, not the shipped MCP-reachable binary.)
            if not self._fallback_pattern_is_provably_linear(config):
                raise BackendExecutionError(
                    "cannot safely evaluate this non-fixed-strings pattern through CPUBackend "
                    f"after a native-engine runtime failure ({type(exc).__name__}: {exc}); its "
                    "backtracking engine has no linear-time guarantee for an arbitrary pattern; "
                    "use ripgrep or pass --fixed-strings"
                ) from exc
            # fixed_strings only: re.escape'd -> provably linear -> safe to run through Python.
            logger.warning(
                "Rust backend failed for %s, using Python regex (fixed-strings): %s",
                file_path,
                exc,
            )

        matches = []
        flags = 0

        if config.ignore_case or (config.smart_case and pattern.islower()):
            flags |= re.IGNORECASE

        regex_str, regex = self._compile_regexes(pattern=pattern, flags=flags, config=config)
        prefilter_literal = None
        routing_reason = "cpu_python_regex"
        ignore_case = bool(config.ignore_case or (config.smart_case and pattern.islower()))
        source_lines: list[str] | None = None
        candidate_line_indexes: set[int] | None = None
        if not (
            config.fixed_strings
            or config.invert_match
            or config.context
            or config.before_context
            or config.after_context
            or config.line_regexp
            or config.word_regexp
            or config.ltl
        ):
            prefilter_literal = self._extract_required_literal(pattern)
            if prefilter_literal:
                cached_index = self._load_literal_index(file_path, ignore_case)
                if cached_index is None:
                    source_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                    normalized_lines = (
                        [line.lower() for line in source_lines] if ignore_case else source_lines
                    )
                    trigram_index = self._build_line_trigram_index(normalized_lines)
                    self._store_literal_index(file_path, ignore_case, source_lines, trigram_index)
                    routing_reason = "cpu_python_regex_prefilter"
                else:
                    source_lines, trigram_index = cached_index
                    routing_reason = "cpu_python_regex_prefilter_cache"
                literal = prefilter_literal.lower() if ignore_case else prefilter_literal
                candidate_line_indexes = set(self._candidate_line_indexes(trigram_index, literal))

        total_matches_count = 0
        before_lines = getattr(config, "before_context", 0) or 0
        after_lines = getattr(config, "after_context", 0) or 0
        if getattr(config, "context", None):
            before_lines = config.context
            after_lines = config.context

        try:
            from collections import deque

            before_queue: deque[tuple[int, str]] = deque(maxlen=before_lines)
            context_after_remaining = 0
            if source_lines is not None:
                line_iter = (
                    (idx + 1, f"{line}\n".encode()) for idx, line in enumerate(source_lines)
                )
                for line_idx, line_bytes in line_iter:
                    if (
                        candidate_line_indexes is not None
                        and (line_idx - 1) not in candidate_line_indexes
                    ):
                        continue
                    # Try using python regex to decode byte string, else try the decoded string
                    matched = False
                    try:
                        matched = bool(regex.search(line_bytes))
                    except Exception:
                        pass

                    if not matched:
                        try:
                            line_text = line_bytes.decode("utf-8").rstrip("\n\r")
                            matched = bool(regex_str.search(line_text))
                        except Exception:
                            try:
                                line_text = line_bytes.decode("latin-1").rstrip("\n\r")
                                matched = bool(regex_str.search(line_text))
                            except Exception:
                                pass

                    if config.invert_match:
                        matched = not matched

                    if matched or before_lines > 0 or context_after_remaining > 0:
                        # Decode lazily only what we need to return
                        try:
                            line = line_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            try:
                                line = line_bytes.decode("latin-1")
                            except Exception:
                                line = line_bytes.decode("utf-8", errors="replace")
                        line_text = line.rstrip("\n\r")

                        # Apply python regex search for decoded text to be safe
                        matched = bool(regex_str.search(line_text))

                        if config.invert_match:
                            matched = not matched

                    if matched:
                        while before_queue:
                            b_idx, b_text = before_queue.popleft()
                            matches.append(
                                MatchLine(line_number=b_idx, text=b_text, file=file_path)
                            )

                        matches.append(
                            MatchLine(line_number=line_idx, text=line_text, file=file_path)
                        )
                        total_matches_count += 1
                        context_after_remaining = after_lines

                        if config.max_count and total_matches_count >= config.max_count:
                            break
                    elif context_after_remaining > 0:
                        matches.append(
                            MatchLine(line_number=line_idx, text=line_text, file=file_path)
                        )
                        context_after_remaining -= 1
                    else:
                        if before_lines > 0:
                            before_queue.append((line_idx, line_text))
            else:
                with open(path, "rb") as f:
                    for line_idx, line_bytes in enumerate(f, 1):
                        if (
                            candidate_line_indexes is not None
                            and (line_idx - 1) not in candidate_line_indexes
                        ):
                            continue
                        # Try using python regex to decode byte string, else try the decoded string
                        matched = False
                        try:
                            matched = bool(regex.search(line_bytes))
                        except Exception:
                            pass

                        if not matched:
                            try:
                                line_text = line_bytes.decode("utf-8").rstrip("\n\r")
                                matched = bool(regex_str.search(line_text))
                            except Exception:
                                try:
                                    line_text = line_bytes.decode("latin-1").rstrip("\n\r")
                                    matched = bool(regex_str.search(line_text))
                                except Exception:
                                    pass

                        if config.invert_match:
                            matched = not matched

                        if matched or before_lines > 0 or context_after_remaining > 0:
                            # Decode lazily only what we need to return
                            try:
                                line = line_bytes.decode("utf-8")
                            except UnicodeDecodeError:
                                try:
                                    line = line_bytes.decode("latin-1")
                                except Exception:
                                    line = line_bytes.decode("utf-8", errors="replace")
                            line_text = line.rstrip("\n\r")

                            # Apply python regex search for decoded text to be safe
                            matched = bool(regex_str.search(line_text))

                            if config.invert_match:
                                matched = not matched

                        if matched:
                            while before_queue:
                                b_idx, b_text = before_queue.popleft()
                                matches.append(
                                    MatchLine(line_number=b_idx, text=b_text, file=file_path)
                                )

                            matches.append(
                                MatchLine(line_number=line_idx, text=line_text, file=file_path)
                            )
                            total_matches_count += 1
                            context_after_remaining = after_lines

                            if config.max_count and total_matches_count >= config.max_count:
                                break
                        elif context_after_remaining > 0:
                            matches.append(
                                MatchLine(line_number=line_idx, text=line_text, file=file_path)
                            )
                            context_after_remaining -= 1
                        else:
                            if before_lines > 0:
                                before_queue.append((line_idx, line_text))
        except Exception as exc:
            raise RuntimeError(f"CPU backend search failed for {file_path}: {exc}") from exc

        return SearchResult(
            matches=matches,
            total_files=1 if total_matches_count > 0 else 0,
            total_matches=total_matches_count,
            routing_backend="CPUBackend",
            routing_reason=routing_reason,
            routing_distributed=False,
            routing_worker_count=1,
        )

    @staticmethod
    def _decode_line(line_bytes: bytes) -> str:
        try:
            return line_bytes.decode("utf-8").rstrip("\n\r")
        except UnicodeDecodeError:
            try:
                return line_bytes.decode("latin-1").rstrip("\n\r")
            except Exception:
                return line_bytes.decode("utf-8", errors="replace").rstrip("\n\r")

    @staticmethod
    def _build_rust_query(pattern: str, config: SearchConfig) -> tuple[str, bool]:
        """Mirror `_compile_regexes`'s exact wrapping precedence (fixed_strings takes priority
        over -w/-x wrapping, matching that method's existing behavior) so the match-set the
        Rust engine computes is equivalent to what the Python regex path would have matched for
        the same flag combination. Returns ``(pattern_to_send_to_rust, fixed_strings_flag)``.
        """
        if config.fixed_strings:
            return pattern, True
        if config.line_regexp:
            # The Rust engine splits on '\n' and keeps a trailing '\r' on CRLF files, whereas
            # the old Python path stripped '\r' before applying '^pat$'. To preserve -x's
            # "whole line equals pattern" semantics on BOTH LF and CRLF inputs (byte-identical
            # to the pre-fix Python behavior on benign LF files, and non-regressive on Windows
            # CRLF files), allow an optional trailing '\r'. `\r?` is a single-atom, non-nested
            # quantifier -- it cannot itself introduce catastrophic backtracking, and the user
            # pattern is evaluated only by the linear-time Rust engine regardless.
            return f"^(?:{pattern})\\r?$", False
        if config.word_regexp:
            return f"\\b{pattern}\\b", False
        return pattern, False

    def _rust_match_set(
        self,
        file_path: str,
        rust_pattern: str,
        ignore_case: bool,
        fixed_strings: bool,
        invert_match: bool,
    ) -> list[tuple[int, str]]:
        """Run ONE pattern through the linear-time Rust engine and return its raw
        ``(line_number, text)`` matches (Rust applies ``invert_match`` itself, so the returned
        set is already the correct one either way). Raises on ANY failure (Rust genuinely
        absent, syntax rejection, or a runtime fault) -- callers decide the fail-closed
        response; this helper never falls open to Python `re` itself (that is exactly the
        audit #6/#16 hazard).
        """
        from tensor_grep.rust_core import RustBackend

        rust_backend = RustBackend()
        try:
            results: list[tuple[int, str]] = rust_backend.search(
                pattern=rust_pattern,
                path=file_path,
                ignore_case=ignore_case,
                fixed_strings=fixed_strings,
                invert_match=invert_match,
            )
        except TypeError:
            # Older rust_core builds without the `invert_match` kwarg (matches the same
            # defensive fallback the primary Rust-attempt block above uses).
            results = rust_backend.search(
                pattern=rust_pattern,
                path=file_path,
                ignore_case=ignore_case,
                fixed_strings=fixed_strings,
            )
        return results

    def _search_word_line_context_via_rust(
        self, path: Path, file_path: str, pattern: str, config: SearchConfig
    ) -> SearchResult:
        """Route the -w/-x/-C/-A/-B match-set through the linear-time Rust engine, then
        assemble context windows (or apply -w/-x wrapping) purely in Python -- no backtracking
        regex is ever evaluated on this path.

        Audit #6 (ReDoS gate bypass): -C/-A/-B/-w/-x previously disabled the Rust attempt
        entirely and routed straight to Python's backtracking `re` with NO deadline --
        `(a+)+$` via `-w`/`-C` could hang forever even though nested quantifiers are valid Rust
        syntax that Rust's automata engine runs in guaranteed O(n) (the "Rust accepted syntax
        so it's safe" reasoning that justified skipping Rust here was false). When Rust is
        present (the common case) this eliminates the Python-re hazard entirely: no residual,
        no subprocess, no possibility of hanging.

        THE RESIDUAL (audit #16, "robustness over completeness"): if Rust is genuinely absent,
        or Rust cannot service the request for any reason (syntax rejection, --pcre2 needing an
        engine CPUBackend does not have, or a runtime fault), we FAIL CLOSED -- raise
        `BackendExecutionError` -- rather than silently falling open to backtracking Python
        `re` with no bound. A visible refusal cannot hang and is fully compliant with the
        Backend Fail-Closed Contract; callers can install ripgrep (which has a real, separate
        engine) or drop the -C/-A/-B/-w/-x/--pcre2 flag combination.
        """
        rust_query_pattern, rust_query_fixed_strings = self._build_rust_query(pattern, config)
        ignore_case = bool(config.ignore_case or (config.smart_case and pattern.islower()))

        try:
            rust_results = self._rust_match_set(
                file_path,
                rust_query_pattern,
                ignore_case,
                rust_query_fixed_strings,
                config.invert_match,
            )
        except Exception as exc:
            raise BackendExecutionError(
                "cannot safely evaluate this pattern through CPUBackend's -C/-A/-B/-w/-x path "
                f"without the linear-time Rust engine ({type(exc).__name__}: {exc}); install "
                "ripgrep, or drop the -C/-A/-B/-w/-x/--pcre2 flag combination"
            ) from exc

        needs_context = bool(
            getattr(config, "context", False)
            or getattr(config, "before_context", False)
            or getattr(config, "after_context", False)
        )

        if not needs_context:
            if config.max_count is not None:
                rust_results = rust_results[: config.max_count]
            matches = [
                MatchLine(line_number=r[0], text=str(r[1]).rstrip("\n\r"), file=file_path)
                for r in rust_results
            ]
            return SearchResult(
                matches=matches,
                total_files=1 if matches else 0,
                total_matches=len(matches),
                routing_backend="CPUBackend",
                routing_reason="cpu_rust_regex",
                routing_distributed=False,
                routing_worker_count=1,
            )

        matched_line_numbers = {int(r[0]) for r in rust_results}
        return self._assemble_context_matches(path, file_path, config, matched_line_numbers)

    def _assemble_context_matches(
        self,
        path: Path,
        file_path: str,
        config: SearchConfig,
        matched_line_numbers: set[int],
    ) -> SearchResult:
        """Build -A/-B/-C context windows around a PRECOMPUTED match-line-number set (produced
        by the linear-time Rust engine, already reflecting -w/-x wrapping and invert_match).
        This is pure line-number bookkeeping -- no regex is evaluated here at all, so it cannot
        ReDoS regardless of how hostile the underlying pattern or file content is.
        """
        before_lines = getattr(config, "before_context", 0) or 0
        after_lines = getattr(config, "after_context", 0) or 0
        if getattr(config, "context", None):
            before_lines = config.context
            after_lines = config.context

        matches: list[MatchLine] = []
        total_matches_count = 0
        before_queue: deque[tuple[int, str]] = deque(maxlen=before_lines)
        context_after_remaining = 0

        with open(path, "rb") as file_obj:
            for line_idx, line_bytes in enumerate(file_obj, 1):
                line_text = self._decode_line(line_bytes)
                matched = line_idx in matched_line_numbers

                if matched:
                    while before_queue:
                        b_idx, b_text = before_queue.popleft()
                        matches.append(MatchLine(line_number=b_idx, text=b_text, file=file_path))
                    matches.append(MatchLine(line_number=line_idx, text=line_text, file=file_path))
                    total_matches_count += 1
                    context_after_remaining = after_lines

                    if config.max_count and total_matches_count >= config.max_count:
                        break
                elif context_after_remaining > 0:
                    matches.append(MatchLine(line_number=line_idx, text=line_text, file=file_path))
                    context_after_remaining -= 1
                else:
                    if before_lines > 0:
                        before_queue.append((line_idx, line_text))

        return SearchResult(
            matches=matches,
            total_files=1 if total_matches_count > 0 else 0,
            total_matches=total_matches_count,
            routing_backend="CPUBackend",
            routing_reason="cpu_rust_regex_context",
            routing_distributed=False,
            routing_worker_count=1,
        )

    @staticmethod
    def _compile_ltl(pattern: str, flags: int) -> tuple[re.Pattern[str], re.Pattern[str]]:
        # Supported grammar (minimal v1): A -> eventually B
        ltl_match = re.match(r"^\s*(.+?)\s*->\s*eventually\s+(.+?)\s*$", pattern, re.IGNORECASE)
        if ltl_match is None:
            raise ValueError("Unsupported LTL query. Use: 'A -> eventually B'")
        left_expr, right_expr = ltl_match.group(1), ltl_match.group(2)
        return re.compile(left_expr, flags), re.compile(right_expr, flags)

    def _search_ltl(self, path: Path, pattern: str, config: SearchConfig) -> SearchResult:
        flags = 0
        if config.ignore_case or (config.smart_case and pattern.islower()):
            flags |= re.IGNORECASE

        # `_compile_ltl` is preserved as the sole grammar parser (tests monkeypatch it) and
        # still gives us the two sub-expression strings via `.pattern`; we no longer evaluate
        # these compiled objects with `.search()` against untrusted file content, though --
        # audit #6/#16 (ReDoS gate bypass): --ltl unconditionally used Python's backtracking
        # `re.search()` per line with NO deadline, so e.g. `(a+)+$ -> eventually X` could hang
        # forever. Both sub-expressions are now resolved to a MATCH-SET via the linear-time
        # Rust engine; the existing O(n) two-pointer sequence assembly below is unchanged, just
        # driven by set-membership instead of a live regex call.
        left_regex, right_regex = self._compile_ltl(pattern, flags)
        ignore_case = bool(flags & re.IGNORECASE)

        try:
            left_match_lines = {
                int(r[0])
                for r in self._rust_match_set(
                    str(path), left_regex.pattern, ignore_case, False, False
                )
            }
            right_match_lines = {
                int(r[0])
                for r in self._rust_match_set(
                    str(path), right_regex.pattern, ignore_case, False, False
                )
            }
        except Exception as exc:
            # THE RESIDUAL (rust-absent or any Rust-side failure): Python `re` is the only other
            # engine, but running it unbounded on --ltl sub-expressions is exactly the hazard
            # this fix closes. FAIL CLOSED (Backend Fail-Closed Contract) rather than silently
            # falling open -- a visible refusal cannot hang.
            raise BackendExecutionError(
                "cannot safely evaluate this --ltl query through CPUBackend without the "
                f"linear-time Rust engine ({type(exc).__name__}: {exc}); install ripgrep or "
                "drop --ltl"
            ) from exc

        lines: list[tuple[int, str]] = []
        with open(path, "rb") as file_obj:
            for line_idx, line_bytes in enumerate(file_obj, 1):
                lines.append((line_idx, self._decode_line(line_bytes)))

        matches: list[MatchLine] = []
        sequence_count = 0

        # DoS fix: the old inner "scan forward per left-match" was O(n^2) — a
        # left-matches-often/right-rarely query (every line matches A, none matches B)
        # scanned to EOF for each of the ~n left hits. Precompute, in ONE backward pass, the
        # nearest right-match index at-or-after each position so each left hit resolves its
        # "eventually B" in O(1). Total O(n) with identical results (still the FIRST right
        # match strictly after the left line).
        total_lines = len(lines)
        next_right_at_or_after: list[int | None] = [None] * (total_lines + 1)
        nearest_right: int | None = None
        for probe in range(total_lines - 1, -1, -1):
            if lines[probe][0] in right_match_lines:
                nearest_right = probe
            next_right_at_or_after[probe] = nearest_right

        for idx, (left_line_no, left_text) in enumerate(lines):
            if left_line_no not in left_match_lines:
                continue
            right_match_idx = next_right_at_or_after[idx + 1]  # first right STRICTLY after idx
            if right_match_idx is None:
                continue

            right_line_no, right_text = lines[right_match_idx]
            matches.append(MatchLine(line_number=left_line_no, text=left_text, file=str(path)))
            matches.append(MatchLine(line_number=right_line_no, text=right_text, file=str(path)))
            sequence_count += 1

            if config.max_count and sequence_count >= config.max_count:
                break

        return SearchResult(
            matches=matches,
            total_files=1 if sequence_count > 0 else 0,
            total_matches=sequence_count,
            routing_backend="CPUBackend",
            routing_reason="cpu_ltl_python",
            routing_distributed=False,
            routing_worker_count=1,
        )
