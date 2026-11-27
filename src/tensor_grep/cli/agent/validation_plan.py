from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from tensor_grep.cli import predictive_test_selector as _predictive_test_selector
from tensor_grep.cli import repo_map as _repo_map
from tensor_grep.cli import repo_map_shell_inert as _inert
from tensor_grep.cli.repo_map_cache import _mtime_aware_cache


def _read_package_json(root: _repo_map.Path) -> dict[str, Any]:
    """O(1) fixed-path read of ``<root>/package.json`` -- no directory scan. Returns ``{}``
    when the file is missing, unreadable, or not a JSON object."""
    package_json_path = root / "package.json"
    if not _repo_map._is_file_with_receipt(package_json_path):
        return {}
    try:
        loaded = json.loads(_repo_map._read_text_with_receipt(package_json_path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _infer_js_package_manager(root: _repo_map.Path, package_json: dict[str, Any]) -> str:
    package_manager = package_json.get("packageManager")
    if isinstance(package_manager, str):
        normalized = package_manager.split("@", 1)[0].strip().lower()
        if normalized in {"npm", "pnpm", "yarn", "bun"}:
            return normalized
    if _repo_map._is_file_with_receipt(root / "pnpm-lock.yaml") or _repo_map._is_file_with_receipt(
        root / "pnpm-workspace.yaml"
    ):
        return "pnpm"
    if _repo_map._is_file_with_receipt(root / "yarn.lock"):
        return "yarn"
    if _repo_map._is_file_with_receipt(root / "bun.lockb"):
        return "bun"
    return "npm"


def _package_test_script_command(root: _repo_map.Path, package_json: dict[str, Any]) -> str | None:
    test_script = _repo_map._package_test_script(package_json)
    if test_script is None:
        return None
    return _repo_map._javascript_repo_fallback_command(
        _repo_map._infer_js_package_manager(root, package_json)
    )


def _package_test_script(package_json: dict[str, Any]) -> str | None:
    scripts = package_json.get("scripts")
    if not isinstance(scripts, dict):
        return None
    test_script = scripts.get("test")
    if not isinstance(test_script, str) or not test_script.strip():
        return None
    if "no test specified" in test_script.lower():
        return None
    return test_script.strip()


def _package_json_dependency_names(package_json: dict[str, Any]) -> set[str]:
    dependency_names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        current = package_json.get(key)
        if not isinstance(current, dict):
            continue
        dependency_names.update(str(name) for name in current.keys())
    return dependency_names


def _ts_jest_configured(
    repo_root: _repo_map.Path,
    package_json: dict[str, Any],
    package_text: str,
    dependency_names: set[str],
) -> bool:
    if "ts-jest" in dependency_names:
        return True
    if "ts-jest" in package_text:
        return True
    jest_config = package_json.get("jest")
    if jest_config is not None and "ts-jest" in json.dumps(jest_config, sort_keys=True):
        return True
    for config_name in (
        "jest.config.js",
        "jest.config.cjs",
        "jest.config.mjs",
        "jest.config.ts",
        "jest.config.json",
    ):
        config_path = repo_root / config_name
        if not _repo_map._is_file_with_receipt(config_path):
            continue
        try:
            if "ts-jest" in _repo_map._read_text_with_receipt(config_path):
                return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


def _detect_validation_runners_from_root(
    root: _repo_map.Path,
    *,
    precomputed_file_paths: list[str | _repo_map.Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
) -> _repo_map._ValidationRunnerInfo:
    """#642 gate nit-1 fast-follow: ``deadline_monotonic``/``deadline_hit`` are optional (default
    ``None``, fully backward compatible with every other call site) and thread straight into
    ``_precomputed_validation_files_for_root``'s own per-entry ``Path.resolve()`` loop -- the
    SECOND validation-plan chain the #642 review named as still-unbounded for
    ``tg context-render``/``tg edit-plan``/``tg context`` (repo_map.py ~11987, reached via
    ``_build_edit_plan_seed``). Mirrors the SAME optional-kwarg contract that function already
    documents for its other callers.
    """
    if not _repo_map._path_exists_with_receipt(root):
        _repo_map.record_content_receipt_digest(root, "")
        return _repo_map._ValidationRunnerInfo(
            False, False, False, "generic", False, (), (), None, None, None
        )

    all_files = _repo_map._precomputed_validation_files_for_root(
        root,
        precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    if all_files is None:
        # #222 residual fix -- same gap and shape as `_discover_validation_tests_for_primary_
        # file`'s identical fallback above: thread the deadline this function already accepts
        # into the un-deadlined walk it falls back to.
        all_files = _repo_map._iter_repo_files(
            root,
            max_files=_repo_map._VALIDATION_RUNNER_SCAN_LIMIT,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        )
    has_python = any(current.suffix.lower() == ".py" for current in all_files)
    has_python_tests = any(
        current.suffix.lower() == ".py" and _repo_map._is_test_file(current)
        for current in all_files
    )
    has_python_project_marker = any(
        _repo_map._is_file_with_receipt(root / marker)
        for marker in (
            "pyproject.toml",
            "pytest.ini",
            "setup.py",
            "setup.cfg",
            "tox.ini",
        )
    )
    if has_python_project_marker or has_python_tests:
        python_detection = "detected"
    elif has_python:
        python_detection = "heuristic"
    else:
        python_detection = "generic"
    has_rust = _repo_map._is_file_with_receipt(root / "Cargo.toml") or any(
        current.name == "Cargo.toml" for current in all_files
    )
    has_javascript = any(
        current.suffix.lower() in _repo_map._JS_TS_SUFFIXES for current in all_files
    )

    package_json: dict[str, Any] = {}
    package_text = ""
    package_json_path = root / "package.json"
    has_package_json = _repo_map._is_file_with_receipt(package_json_path)
    if has_package_json:
        try:
            package_text = _repo_map._read_text_with_receipt(package_json_path)
            loaded = json.loads(package_text)
            if isinstance(loaded, dict):
                package_json = loaded
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            package_json = {}
            package_text = ""

    dependency_names = _repo_map._package_json_dependency_names(package_json)
    js_runners = tuple(
        runner for runner in _repo_map._JS_RUNNER_ORDER if runner in dependency_names
    )

    ts_runners: list[str] = []
    if "vitest" in dependency_names:
        ts_runners.append("vitest")
    if "jest" in dependency_names and _repo_map._ts_jest_configured(
        root, package_json, package_text, dependency_names
    ):
        ts_runners.append("jest")
    if "mocha" in dependency_names and ("ts-node" in dependency_names or "tsx" in dependency_names):
        ts_runners.append("mocha")
    js_fallback_command = (
        _repo_map._javascript_repo_fallback_command(
            _repo_map._infer_js_package_manager(root, package_json)
        )
        if has_javascript and has_package_json
        else None
    )
    js_test_script = (
        _repo_map._package_test_script(package_json)
        if has_javascript and has_package_json
        else None
    )
    js_script_command = (
        _repo_map._package_test_script_command(root, package_json)
        if has_javascript and has_package_json
        else None
    )

    return _repo_map._ValidationRunnerInfo(
        has_python=has_python,
        has_rust=has_rust,
        has_javascript=has_javascript,
        python_detection=python_detection,
        has_package_json=has_package_json,
        js_runners=js_runners,
        ts_runners=tuple(ts_runners),
        js_script_command=js_script_command,
        js_fallback_command=js_fallback_command,
        js_test_script=js_test_script,
    )


@lru_cache(maxsize=64)
def _cached_detect_validation_runners(repo_root: str) -> _repo_map._ValidationRunnerInfo:
    return _repo_map._detect_validation_runners_from_root(
        _repo_map._validation_repo_root(repo_root)
    )


def _detect_validation_runners(repo_root: str) -> _repo_map._ValidationRunnerInfo:
    if _repo_map.content_receipts_active():
        return _repo_map._detect_validation_runners_from_root(
            _repo_map._validation_repo_root(repo_root)
        )
    return _repo_map._cached_detect_validation_runners(repo_root)


def _relative_validation_path(path: _repo_map.Path, repo_root: _repo_map.Path) -> str:
    try:
        return (
            _repo_map
            ._resolved_input_path(path)
            .relative_to(_repo_map._resolved_input_path(repo_root))
            .as_posix()
        )
    except ValueError:
        return path.name


def _shell_safe_arg(value: str) -> str:
    if not value:
        return '""'
    if any(char.isspace() for char in value) or '"' in value:
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _candidate_terms(value: str | None) -> list[str]:
    if not value:
        return []
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return _repo_map._query_terms(normalized.replace("_", " "))


def _best_test_function_candidate(
    candidates: list[str],
    *,
    primary_symbol_name: str | None,
    query: str | None,
) -> str | None:
    return _inert.best_test_function_candidate(
        candidates,
        symbol_terms=_repo_map._candidate_terms(primary_symbol_name),
        query_terms=_repo_map._candidate_terms(query),
    )


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _javascript_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = _repo_map.Path(test_path)
    try:
        source = _repo_map.lang_registry.read_source_text(path)
    except OSError:
        return ()
    describe_pattern = re.compile(r"""\bdescribe(?:\.(?:only|skip))?\s*\(\s*["']([^"']+)["']""")
    test_pattern = re.compile(
        r"""(?x)
        (?:
            \b(?:test|it)(?:\.(?:only|skip|todo|concurrent))?\s*\(\s*["']([^"']+)["']
            |
            \b(?:test|it)\.each\s*\([^)]*\)\s*\(\s*["']([^"']+)["']
            |
            \bDeno\.test\s*\(\s*["']([^"']+)["']
        )
        """
    )
    describe_candidates = [
        (match.start(), match.group(1)) for match in describe_pattern.finditer(source)
    ]
    candidates: list[str] = []
    for match in test_pattern.finditer(source):
        target_name = next((group for group in match.groups() if group), None)
        if not target_name:
            continue
        suite_name = None
        for position, candidate in describe_candidates:
            if position >= match.start():
                break
            suite_name = candidate
        if suite_name:
            candidates.append(f"{suite_name} {target_name}".strip())
        candidates.append(target_name)
    return tuple(dict.fromkeys(candidates))


def _framework_test_function_candidates(test_path: str) -> tuple[str, ...]:
    path = _repo_map.Path(test_path)
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _repo_map._python_parametrized_test_function_candidates(test_path)
    if suffix in _repo_map._JS_TS_SUFFIXES:
        return _repo_map._javascript_test_function_candidates(test_path)
    if suffix in _repo_map._RUST_SUFFIXES:
        return _repo_map._rust_test_function_candidates(test_path)
    return ()


def _framework_test_pattern_bonus(
    test_path: str,
    terms: list[str],
    *,
    raw_query: str | None = None,
) -> int:
    expanded_terms: list[str] = list(terms)
    for candidate_term in _repo_map._candidate_terms(raw_query):
        if candidate_term not in expanded_terms:
            expanded_terms.append(candidate_term)
    if not expanded_terms:
        return 0
    # perf(edit-plan): textual pre-check -- `_framework_test_function_candidates` triggers an
    # expensive per-file AST parse for every `.py` candidate (`_python_parametrized_test_function_
    # candidates` -> `_cached_ast_parse`), profiled at 46% of `tg context-render`'s wall / 23.9% of
    # `tg prepare`'s, even though most candidates contribute a 0 bonus. Read the file's raw text
    # ONCE and skip straight to the identical `return 0` outcome the expensive parse would have
    # produced when nothing in `expanded_terms` could possibly score.
    #
    # BYTE-IDENTICAL: every string `_score_text_terms` is ever asked to score against is a literal
    # substring of the raw file text -- EXCEPT the JS/TS `describe`+`test` name synthesized in
    # `_javascript_test_function_candidates` (``f"{suite_name} {target_name}"``), which joins two
    # literal-but-non-adjacent quoted-string substrings with an artificial single space. A term can
    # only score against that synthesized string by being a literal substring of `suite_name` or of
    # `target_name` alone (both real file substrings), OR by straddling exactly that one synthetic
    # join space -- and in the straddle case each half is still individually a literal file
    # substring. Checking each whitespace-split WORD of a term (not just the term as one contiguous
    # run) against the raw file text closes that seam: it is a strictly safer over-approximation (a
    # substring of a term that would have scored is still checked), so this short-circuit never
    # produces a different answer than the un-short-circuited computation below.
    try:
        file_text = _repo_map._read_source_text_cached(test_path).lower()
    except (OSError, UnicodeDecodeError):
        file_text = None  # can't prove absence -- fall through and let the real path handle it
    if file_text is not None:
        atoms = {word for term in expanded_terms for word in term.lower().split()}
        if not any(atom in file_text for atom in atoms):
            return 0
    candidates = _repo_map._framework_test_function_candidates(test_path)
    if not candidates:
        return 0
    return (
        max(
            (_repo_map._score_text_terms(candidate, expanded_terms) for candidate in candidates),
            default=0,
        )
        * 3
    )


@_mtime_aware_cache(maxsize=256)  # B7: mtime+size in key; replaces plain @lru_cache
def _javascript_test_file_uses_node_test(test_path: str) -> bool:
    path = _repo_map.Path(test_path)
    try:
        source = _repo_map.lang_registry.read_source_text(path)
    except OSError:
        return False
    return bool(
        re.search(
            r"""\b(?:import\s+test\s+from\s+["']node:test["']|require\(["']node:test["']\))""",
            source,
        )
        or re.search(r"""\bnode:test\b""", source)
    )


def _nearest_cargo_manifest_for_path(
    path: str | _repo_map.Path, repo_root: _repo_map.Path
) -> _repo_map.Path | None:
    try:
        current = _repo_map._resolved_input_path(path)
    except (OSError, RuntimeError):
        return None
    if _repo_map._is_file_with_receipt(current) or current.suffix:
        current = current.parent
    try:
        boundary = _repo_map._resolved_input_path(repo_root)
    except (OSError, RuntimeError):
        boundary = repo_root
    while True:
        manifest = current / "Cargo.toml"
        if _repo_map._is_file_with_receipt(manifest):
            return manifest
        if current == boundary or current.parent == current:
            break
        current = current.parent
    return None


def _cargo_test_command_for_primary_file(
    primary_file: str | _repo_map.Path | None,
    repo_root: _repo_map.Path,
) -> str | None:
    if primary_file is None or _repo_map._target_language_for_path(primary_file) != "rust":
        return None
    manifest = _repo_map._nearest_cargo_manifest_for_path(primary_file, repo_root)
    if manifest is None:
        return None
    try:
        if _repo_map._resolved_input_path(manifest.parent) == _repo_map._resolved_input_path(
            repo_root
        ):
            return "cargo test"
    except (OSError, RuntimeError):
        pass
    command = _inert.render_command(
        "cargo",
        "test",
        "--manifest-path",
        _inert.Derived(_repo_map._relative_validation_path(manifest, repo_root)),
    )
    return command if isinstance(command, str) else None


def _primary_language_fallback_validation_steps(
    *,
    repo_root: str | _repo_map.Path,
    primary_file: str | _repo_map.Path | None,
    precomputed_file_paths: list[str | _repo_map.Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
) -> list[dict[str, Any]]:
    if primary_file is None:
        return []
    root = _repo_map._validation_repo_root(repo_root)
    primary_language = _repo_map._target_language_for_path(primary_file)
    steps: list[dict[str, Any]] = []
    if primary_language == "rust":
        command = _repo_map._cargo_test_command_for_primary_file(primary_file, root)
        if command is not None:
            steps.append({
                "command": command,
                "scope": "repo",
                "runner": "cargo",
                "confidence": 0.5,
                "detection": "detected",
            })
    elif primary_language == "python" and _repo_map._has_python_validation_fallback_evidence(
        root,
        precomputed_file_paths=precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    ):
        detected = _repo_map._detect_validation_runners_from_root(
            root,
            precomputed_file_paths=precomputed_file_paths,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        )
        if detected.python_detection == "detected":
            steps.append({
                "command": "uv run pytest -q" if _inert.repo_uses_uv(root) else "pytest -q",
                "scope": "repo",
                "runner": "pytest",
                "confidence": 0.55,
                "detection": "detected",
            })
    elif primary_language in ("javascript", "typescript") and _repo_map._is_file_with_receipt(
        root / "package.json"
    ):
        # dogfood F3: a TS/JS primary target used to get NO fallback step at all here (this
        # branch only had rust/python) -- on a repo where the raw per-test validation plan
        # comes back empty (e.g. a scan-ceiling-capped root on a large monorepo), that meant an
        # EMPTY validation_plan even though the repo obviously has a package.json test runner.
        # Fixed-path `.is_file()` probe only -- no directory scan (see AGENTS.md no-repo-wide-
        # scan rule).
        package_json = _repo_map._read_package_json(root)
        command = _repo_map._javascript_repo_fallback_command(
            _repo_map._infer_js_package_manager(root, package_json)
        )
        steps.append({
            "command": command,
            "scope": "repo",
            "runner": "javascript",
            "confidence": 0.5,
            "detection": "detected",
        })
    return steps


def _has_python_validation_fallback_evidence(
    root: _repo_map.Path,
    *,
    precomputed_file_paths: list[str | _repo_map.Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
) -> bool:
    if any(
        _repo_map._is_file_with_receipt(root / marker)
        for marker in (
            "pyproject.toml",
            "pytest.ini",
            "setup.py",
            "setup.cfg",
            "tox.ini",
        )
    ):
        return True
    candidate_files = _repo_map._precomputed_validation_files_for_root(
        root,
        precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    if candidate_files is None:
        # #222 residual fix -- same gap and shape as the two identical fallbacks above.
        candidate_files = _repo_map._iter_repo_files(
            root,
            max_files=_repo_map._VALIDATION_RUNNER_SCAN_LIMIT,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        )
    return any(c.suffix.lower() == ".py" and _repo_map._is_test_file(c) for c in candidate_files)


def _suggested_validation_command_candidates(
    source_path: _repo_map.Path,
    *,
    repo_root: _repo_map.Path | None = None,
) -> list[_repo_map.Path]:
    """Pure-filename test-neighbor CANDIDATES for one source file â€” no execution, no repo
    scan, no manifest lookup. Distinct from `_has_python_validation_fallback_evidence` (the
    strict runner-evidence gate); this only feeds the additive, unverified suggestion field.

    `repo_root`, when given and distinct from the primary file's own directory, ALSO probes a
    fixed set of repo-root test-tree paths (`<root>/tests/test_<stem>.py`,
    `<root>/test|tests|__tests__/<stem>.test<suffix>`) so a test living in a root-level test
    tree (not next to the source file) is still discoverable. Every probe is a single
    `is_file()` check on a fully-determined path â€” never a directory walk/glob (dogfood F3: a
    root-tree GLOB scan here would violate the no-repo-wide-scan promise)."""
    suffix = source_path.suffix.lower()
    stem = source_path.stem
    parent = source_path.parent
    root = repo_root if repo_root is not None and repo_root != parent else None
    if suffix == ".py":
        candidates = [
            parent / "tests" / f"test_{stem}.py",
            parent / f"{stem}_test.py",
            parent / f"test_{stem}.py",
        ]
        if root is not None:
            candidates.append(root / "tests" / f"test_{stem}.py")
        return candidates
    if suffix in _repo_map._JS_TS_SUFFIXES:
        candidates = [
            parent / "__tests__" / f"{stem}{suffix}",
            parent / "__tests__" / f"{stem}.test{suffix}",
            parent / f"{stem}.test{suffix}",
            parent / f"{stem}.spec{suffix}",
        ]
        if root is not None:
            candidates.extend(
                root / dir_name / f"{stem}.test{suffix}"
                for dir_name in _repo_map._ROOT_TEST_DIR_NAMES
            )
        return candidates
    return []


def _suggested_validation_command_for_primary_file(
    primary_file: str | _repo_map.Path | None,
    repo_root: str | _repo_map.Path,
) -> dict[str, Any] | None:
    """Build the ADDITIVE `suggested_validation_commands` entry (verified: false) from a pure
    filename probe of the primary target's directory. NEVER feeds the strict, evidence-gated
    `validation_commands` list â€” no subprocess, no manifest read, no repo-wide scan."""
    if not primary_file:
        return None
    try:
        source_path = _repo_map._resolved_input_path(primary_file)
    except (OSError, RuntimeError):
        return None
    if not _repo_map._is_file_with_receipt(source_path):
        return None
    try:
        root = _repo_map._validation_repo_root(repo_root)
    except (OSError, RuntimeError):
        root = source_path.parent
    candidates = _repo_map._suggested_validation_command_candidates(source_path, repo_root=root)
    if not candidates:
        return None
    test_path = next(
        (candidate for candidate in candidates if _repo_map._is_file_with_receipt(candidate)), None
    )
    if test_path is None:
        return None
    relative_test = _repo_map._relative_validation_path(test_path, root)
    suffix = source_path.suffix.lower()
    if suffix == ".py":
        argv = ["pytest", relative_test]
    elif suffix in _repo_map._TS_SUFFIXES:
        argv = ["vitest", "run", relative_test]
    else:
        argv = ["jest", relative_test]
    command = _inert.render_command(*argv[:-1], _inert.Derived(relative_test))
    if not isinstance(command, str):  # unknown paste shell: fail closed
        return _inert.unsafe_neighbour_entry(argv, relative_test)

    return {
        "command": command,
        "target_test": relative_test,
        "basis": "test-neighbor-heuristic",
        "verified": False,
    }


def _without_heuristic_repo_cargo_fallback(
    validation_plan: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        dict(step)
        for step in validation_plan
        if not (
            str(step.get("runner") or "") == "cargo"
            and str(step.get("command") or "") == "cargo test"
            and str(step.get("scope") or "") == "repo"
            and str(step.get("detection") or "") == "heuristic"
        )
    ]


def _ensure_primary_language_validation_fallback(
    validation_plan: list[dict[str, Any]],
    *,
    repo_root: str | _repo_map.Path,
    primary_file: str | _repo_map.Path | None,
    precomputed_file_paths: list[str | _repo_map.Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
) -> list[dict[str, Any]]:
    primary_language = _repo_map._target_language_for_path(primary_file)
    if primary_language is None:
        return validation_plan
    fallback_steps = _repo_map._primary_language_fallback_validation_steps(
        repo_root=repo_root,
        primary_file=primary_file,
        precomputed_file_paths=precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    if any(
        _repo_map._validation_step_matches_primary_language(step, primary_language)
        for step in validation_plan
    ):
        fallback_commands = {str(step.get("command") or "") for step in fallback_steps}
        if (
            primary_language == "rust"
            and fallback_commands
            and not any(
                str(step.get("command") or "") in fallback_commands for step in validation_plan
            )
            and any(
                str(step.get("runner") or "") == "cargo"
                and str(step.get("command") or "") == "cargo test"
                and str(step.get("detection") or "") == "heuristic"
                for step in validation_plan
            )
        ):
            augmented = [
                dict(step)
                for step in validation_plan
                if not (
                    str(step.get("runner") or "") == "cargo"
                    and str(step.get("command") or "") == "cargo test"
                    and str(step.get("detection") or "") == "heuristic"
                )
            ]
            augmented.extend(dict(step) for step in fallback_steps)
            return augmented
        if primary_language == "python" and fallback_commands:
            return _repo_map._without_heuristic_repo_cargo_fallback(validation_plan)
        return validation_plan
    if not fallback_steps:
        return validation_plan
    base_plan = (
        _repo_map._without_heuristic_repo_cargo_fallback(validation_plan)
        if primary_language == "python"
        else validation_plan
    )
    seen = {str(step.get("command") or "") for step in base_plan}
    augmented = [dict(step) for step in base_plan]
    for step in fallback_steps:
        command = str(step.get("command") or "")
        if command and command not in seen:
            seen.add(command)
            augmented.append(dict(step))
    return augmented


def _validation_commands_for_tests(
    tests: list[str],
    *,
    repo_root: str | _repo_map.Path,
    primary_test: str | None = None,
    primary_symbol: dict[str, Any] | None = None,
    query: str | None = None,
    precomputed_file_paths: list[str | _repo_map.Path] | None = None,
) -> list[str]:
    return [
        str(step["command"])
        for step in _repo_map._validation_plan_for_tests(
            tests,
            repo_root=repo_root,
            primary_test=primary_test,
            primary_symbol=primary_symbol,
            query=query,
            precomputed_file_paths=precomputed_file_paths,
        )
    ]


@_inert.lists_omissions
def _raw_validation_plan_for_tests(
    tests: list[str],
    *,
    repo_root: str | _repo_map.Path,
    primary_test: str | None = None,
    primary_symbol: dict[str, Any] | None = None,
    query: str | None = None,
    precomputed_file_paths: list[str | _repo_map.Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
) -> list[dict[str, Any]]:
    explicit_root = _repo_map._resolved_input_path(repo_root)
    if not _repo_map._is_dir_with_receipt(explicit_root):
        explicit_root = explicit_root.parent
    resolved_tests = [_repo_map._resolved_input_path(current) for current in tests]
    tests_under_explicit_root = bool(resolved_tests) and all(
        _repo_map._path_is_relative_to(current, explicit_root) for current in resolved_tests
    )
    root = (
        explicit_root
        if tests_under_explicit_root
        else _repo_map._validation_repo_root(explicit_root)
    )
    if tests:
        detected = _repo_map._detect_validation_runners_from_root(
            root,
            precomputed_file_paths=precomputed_file_paths,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        )
    else:
        local_files = _repo_map._precomputed_validation_files_for_root(
            explicit_root,
            precomputed_file_paths,
            deadline_monotonic=deadline_monotonic,
            deadline_hit=deadline_hit,
        )
        if local_files is None:
            # #222 residual fix -- same gap and shape as `_detect_validation_runners_from_root`'s
            # identical fallback (the "has tests" branch above): thread the deadline this
            # function already accepts into the un-deadlined walk it falls back to.
            local_files = _repo_map._iter_repo_files(
                explicit_root,
                max_files=_repo_map._VALIDATION_RUNNER_SCAN_LIMIT,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=deadline_hit,
            )
        local_has_python = any(current.suffix.lower() == ".py" for current in local_files)
        local_has_rust = any(
            current.suffix.lower() in _repo_map._RUST_SUFFIXES for current in local_files
        )
        local_has_javascript = any(
            current.suffix.lower() in _repo_map._JS_TS_SUFFIXES for current in local_files
        )
        if local_has_python and not local_has_javascript and not local_has_rust:
            detected = _repo_map._detect_validation_runners_from_root(
                explicit_root,
                precomputed_file_paths=precomputed_file_paths,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=deadline_hit,
            )
        elif precomputed_file_paths is not None:
            detected = _repo_map._detect_validation_runners_from_root(
                root,
                precomputed_file_paths=precomputed_file_paths,
                deadline_monotonic=deadline_monotonic,
                deadline_hit=deadline_hit,
            )
        else:
            detected = _repo_map._detect_validation_runners(str(root))
    has_javascript_validation = bool(
        detected.js_runners
        or detected.ts_runners
        or detected.js_script_command
        or detected.js_fallback_command
    )
    has_python_validation = detected.python_detection == "detected"
    primary_symbol_name = (
        str(primary_symbol.get("name"))
        if isinstance(primary_symbol, dict) and primary_symbol.get("name")
        else None
    )
    primary_file_path = (
        str(primary_symbol.get("file"))
        if isinstance(primary_symbol, dict) and primary_symbol.get("file")
        else None
    )
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    requested_javascript_runners: list[str] = []
    include_python_fallback = False
    include_rust_fallback = False
    uv_prefix: tuple[str, ...] = ("uv", "run") if _inert.repo_uses_uv(root) else ()

    def remember_runner(runner: str) -> None:
        if runner not in requested_javascript_runners:
            requested_javascript_runners.append(runner)

    def add_step(
        command: str | _inert.Omission,
        *,
        scope: str,
        runner: str,
        target: str | None = None,
        confidence: float,
        detection: str,
    ) -> None:
        if isinstance(command, _inert.Omission):  # already recorded by render_command
            return
        if command in seen:
            return
        seen.add(command)
        step: dict[str, Any] = {
            "command": command,
            "scope": scope,
            "runner": runner,
            "confidence": round(min(1.0, max(0.0, confidence)), 3),
            "detection": detection,
        }
        if target:
            step["target"] = target
        plan.append(step)

    for current in tests:
        path = _repo_map.Path(current)
        suffix = path.suffix.lower()
        absolute_path = str(_repo_map._resolved_input_path(path))
        relative_path = _repo_map._relative_validation_path(path, root)
        if not _inert.is_shell_inert_path(relative_path):
            _inert.record_omission(relative_path)  # disclosure only; commands gated per token
        is_primary_test = primary_test is not None and absolute_path == str(
            _repo_map._resolved_input_path(primary_test)
        )

        if suffix == ".py":
            include_python_fallback = True
            if is_primary_test:
                test_filter = _repo_map._best_test_function_candidate(
                    list(_repo_map._python_test_function_candidates(absolute_path)),
                    primary_symbol_name=primary_symbol_name,
                    query=query,
                )
                if test_filter:
                    add_step(
                        _inert.render_command(
                            *uv_prefix,
                            "pytest",
                            _inert.Derived(relative_path),
                            "-k",
                            _inert.Derived(test_filter),
                            "-q",
                        ),
                        scope="symbol",
                        runner="pytest",
                        target=relative_path,
                        confidence=_predictive_test_selector.calibrate_test_step_confidence(
                            test_path=absolute_path,
                            scope="symbol",
                            primary_file=primary_file_path,
                            primary_symbol_name=primary_symbol_name,
                            query=query,
                            repo_map_mod=_repo_map,
                        ),
                        detection="detected",
                    )
            add_step(
                _inert.render_command(*uv_prefix, "pytest", _inert.Derived(relative_path), "-q"),
                scope="file",
                runner="pytest",
                target=relative_path,
                confidence=_predictive_test_selector.calibrate_test_step_confidence(
                    test_path=absolute_path,
                    scope="file",
                    primary_file=primary_file_path,
                    primary_symbol_name=primary_symbol_name,
                    query=query,
                    repo_map_mod=_repo_map,
                ),
                detection="detected",
            )
            continue

        if suffix in _repo_map._TS_SUFFIXES:
            test_candidates = (
                list(_repo_map._javascript_test_function_candidates(absolute_path))
                if is_primary_test
                else []
            )
            test_filter = _repo_map._best_test_function_candidate(
                test_candidates,
                primary_symbol_name=primary_symbol_name,
                query=query,
            )
            for runner in detected.ts_runners:
                remember_runner(runner)
                if test_filter:
                    add_step(
                        _repo_map._javascript_runner_specific_command(
                            runner, relative_path, test_filter
                        ),
                        scope="symbol",
                        runner=runner,
                        target=relative_path,
                        confidence=0.9,
                        detection="detected",
                    )
                add_step(
                    _repo_map._javascript_runner_file_command(runner, relative_path),
                    scope="file",
                    runner=runner,
                    target=relative_path,
                    confidence=0.78,
                    detection="detected",
                )
            continue

        if suffix in _repo_map._JS_TS_SUFFIXES:
            test_candidates = (
                list(_repo_map._javascript_test_function_candidates(absolute_path))
                if is_primary_test
                else []
            )
            test_filter = _repo_map._best_test_function_candidate(
                test_candidates,
                primary_symbol_name=primary_symbol_name,
                query=query,
            )
            script_uses_node_test = _repo_map._javascript_test_script_uses_node_test(
                detected.js_test_script
            )
            primary_file_uses_node_test = (
                is_primary_test
                and not script_uses_node_test
                and _repo_map._javascript_test_file_uses_node_test(absolute_path)
            )
            if script_uses_node_test or primary_file_uses_node_test:
                add_step(
                    _repo_map._javascript_node_test_file_command(relative_path),
                    scope="file",
                    runner="node:test",
                    target=relative_path,
                    confidence=0.84,
                    detection="detected",
                )
            for runner in detected.js_runners:
                remember_runner(runner)
                if test_filter:
                    add_step(
                        _repo_map._javascript_runner_specific_command(
                            runner, relative_path, test_filter
                        ),
                        scope="symbol",
                        runner=runner,
                        target=relative_path,
                        confidence=0.9,
                        detection="detected",
                    )
                add_step(
                    _repo_map._javascript_runner_file_command(runner, relative_path),
                    scope="file",
                    runner=runner,
                    target=relative_path,
                    confidence=0.78,
                    detection="detected",
                )
            continue

        if suffix in _repo_map._RUST_SUFFIXES:
            include_rust_fallback = True
            rust_target = _repo_map._rust_test_target(path, root)
            file_level_command = (
                _inert.render_command("cargo", "test", "--test", _inert.Derived(rust_target))
                if rust_target
                else None
            )
            if is_primary_test:
                test_filter = _repo_map._best_test_function_candidate(
                    list(_repo_map._rust_test_function_candidates(absolute_path)),
                    primary_symbol_name=primary_symbol_name,
                    query=query,
                )
                if test_filter:
                    named = _inert.Derived(test_filter)
                    targeted_command = (
                        _inert.render_command(
                            "cargo", "test", "--test", _inert.Derived(rust_target), named
                        )
                        if rust_target and _repo_map._rust_uses_nested_test_target(path, root)
                        else _inert.render_command("cargo", "test", named)
                    )
                    add_step(
                        targeted_command,
                        scope="symbol",
                        runner="cargo",
                        target=relative_path,
                        confidence=0.88,
                        detection="detected",
                    )
            if file_level_command:
                add_step(
                    file_level_command,
                    scope="file",
                    runner="cargo",
                    target=relative_path,
                    confidence=0.8,
                    detection="detected",
                )
            continue

    if not tests:
        if has_javascript_validation:
            include_python_fallback = include_python_fallback
        else:
            include_python_fallback = include_python_fallback or has_python_validation
        include_rust_fallback = include_rust_fallback or detected.has_rust
        for runner in (*detected.js_runners, *detected.ts_runners):
            remember_runner(runner)

    if (
        not include_python_fallback
        and not include_rust_fallback
        and not requested_javascript_runners
    ):
        include_python_fallback = not has_javascript_validation and (
            has_python_validation and not detected.has_rust
        )
        include_rust_fallback = detected.has_rust
        for runner in (*detected.js_runners, *detected.ts_runners):
            remember_runner(runner)

    if include_python_fallback:
        add_step(
            "uv run pytest -q" if uv_prefix else "pytest -q",
            scope="repo",
            runner="pytest",
            confidence=0.55,
            detection=detected.python_detection,
        )
    if detected.js_script_command and (requested_javascript_runners or detected.has_javascript):
        add_step(
            detected.js_script_command,
            scope="repo",
            runner="javascript",
            confidence=0.65,
            detection="detected",
        )
    else:
        for runner in requested_javascript_runners:
            add_step(
                _repo_map._javascript_runner_fallback_command(runner),
                scope="repo",
                runner=runner,
                confidence=0.5,
                detection="heuristic",
            )
        if (
            detected.has_javascript
            and not requested_javascript_runners
            and detected.js_fallback_command
        ):
            add_step(
                detected.js_fallback_command,
                scope="repo",
                runner="javascript",
                confidence=0.45,
                detection="heuristic",
            )
    if include_rust_fallback:
        add_step(
            "cargo test",
            scope="repo",
            runner="cargo",
            confidence=0.55,
            detection=(
                "detected" if _repo_map._is_file_with_receipt(root / "Cargo.toml") else "heuristic"
            ),
        )

    return plan


def _validation_runner_languages(step: dict[str, Any]) -> set[str]:
    runner = str(step.get("runner", "") or "").strip().lower()
    command = str(step.get("command", "") or "").strip().lower()
    if runner in {"pytest", "python"} or re.search(r"(^|\s)pytest(\s|$)", command):
        return {"python"}
    if runner == "cargo" or command.startswith("cargo "):
        return {"rust"}
    if runner in {"vitest", "jest", "mocha", "node:test", "javascript"}:
        return {"javascript", "typescript"}
    if any(token in command for token in ("vitest", "jest", "mocha", "node --test")):
        return {"javascript", "typescript"}
    if command.startswith(("npm ", "pnpm ", "yarn ", "npx ")):
        return {"javascript", "typescript"}
    return set()


def _validation_step_matches_primary_language(
    step: dict[str, Any],
    primary_language: str | None,
) -> bool:
    if primary_language is None:
        return True
    languages = _repo_map._validation_runner_languages(step)
    if not languages:
        return True
    return primary_language in languages


def _align_validation_plan_for_primary_language(
    validation_plan: list[dict[str, Any]],
    primary_file: str | _repo_map.Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    primary_language = _repo_map._target_language_for_path(primary_file)
    aligned: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []
    issues: list[str] = []
    for raw_step in validation_plan:
        step = dict(raw_step)
        if _repo_map._validation_step_matches_primary_language(step, primary_language):
            aligned.append(step)
            continue
        filtered.append(step)
        runner = str(step.get("runner", "") or "unknown")
        command = str(step.get("command", "") or "")
        issues.append(
            f"filtered {runner} validation for {primary_language} primary target: {command}"
        )

    if not validation_plan:
        status = "no-validation"
    elif filtered:
        status = "mismatch-filtered"
    else:
        status = "aligned"
    alignment = {
        "status": status,
        "primary_target_language": primary_language,
        "kept_count": len(aligned),
        "filtered_count": len(filtered),
        "issues": issues,
    }
    return aligned, alignment


@_inert.collects_omissions
def _validation_plan_and_alignment_for_tests(
    tests: list[str],
    *,
    repo_root: str | _repo_map.Path,
    primary_test: str | None = None,
    primary_symbol: dict[str, Any] | None = None,
    primary_file: str | _repo_map.Path | None = None,
    query: str | None = None,
    precomputed_file_paths: list[str | _repo_map.Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: _repo_map._DeadlineBreakFlag | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """#642 gate nit-1 fast-follow: this is the SECOND validation-plan chain the gate named as
    still-unbounded for ``tg context-render``/``tg edit-plan``/``tg context`` (repo_map.py ~11987,
    reached via ``_build_edit_plan_seed``) -- ``deadline_monotonic``/``deadline_hit`` are optional
    (default ``None``, backward compatible with every existing caller) and thread straight through
    ``_raw_validation_plan_for_tests`` to bound the same per-entry ``Path.resolve()`` cost
    ``_precomputed_validation_files_for_root`` already bounds for the FIRST (validation-test
    discovery) chain.
    """
    raw_plan = _repo_map._raw_validation_plan_for_tests(
        tests,
        repo_root=repo_root,
        primary_test=primary_test,
        primary_symbol=primary_symbol,
        query=query,
        precomputed_file_paths=precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    resolved_primary_file = (
        str(primary_symbol.get("file"))
        if isinstance(primary_symbol, dict) and primary_symbol.get("file")
        else (str(primary_file) if primary_file is not None and str(primary_file) else None)
    )
    raw_plan, _ = _inert.split_omissions(raw_plan)
    raw_plan = _repo_map._ensure_primary_language_validation_fallback(
        raw_plan,
        repo_root=repo_root,
        primary_file=resolved_primary_file,
        precomputed_file_paths=precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    return _repo_map._align_validation_plan_for_primary_language(raw_plan, resolved_primary_file)


def _validation_plan_for_tests(
    tests: list[str],
    *,
    repo_root: str | _repo_map.Path,
    primary_test: str | None = None,
    primary_symbol: dict[str, Any] | None = None,
    primary_file: str | _repo_map.Path | None = None,
    query: str | None = None,
    precomputed_file_paths: list[str | _repo_map.Path] | None = None,
) -> list[dict[str, Any]]:
    plan, _alignment = _repo_map._validation_plan_and_alignment_for_tests(
        tests,
        repo_root=repo_root,
        primary_test=primary_test,
        primary_symbol=primary_symbol,
        primary_file=primary_file,
        query=query,
        precomputed_file_paths=precomputed_file_paths,
    )
    return plan


_detect_validation_runners.cache_clear = _cached_detect_validation_runners.cache_clear  # type: ignore[attr-defined]
