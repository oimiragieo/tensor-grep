from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tensor_grep.cli.checkpoint_store import undo_checkpoint


@dataclass(frozen=True)
class RulesetScanPolicy:
    enabled: bool
    pack: str | None
    language: str | None
    baseline: str | None = None


@dataclass(frozen=True)
class ApplyPolicy:
    path: str
    version: int
    lint_cmd: str | None
    test_cmd: str | None
    ruleset_scan: RulesetScanPolicy | None
    on_failure: str
    timeout: int


class PolicyValidationError(ValueError):
    def __init__(self, message: str, *, details: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.details = details


class PolicyCommandsNotAllowedError(PolicyValidationError):
    """A policy file defines ``lint_cmd``/``test_cmd`` (a shell-exec sink) but the
    caller has not opted into running validation commands.

    Audit HIGH (RCE): the MCP ``TG_MCP_ALLOW_VALIDATION_COMMANDS`` gate previously
    lived only in the ``tg_rewrite_apply`` wrapper and checked the *direct* lint_cmd/
    test_cmd arguments, so a policy JSON file that carried those commands slipped
    past it and reached ``subprocess`` in the default (gate-off) posture. Enforcement
    now lives here at the module boundary so every caller — not just the MCP wrapper
    — fails closed. Trusted callers (the local CLI apply path) opt in explicitly.
    """

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            details=[{"field": "lint_cmd/test_cmd", "message": message}],
        )


CommandRunner = Callable[[str, str, Path, int], dict[str, object]]
ScanRunner = Callable[[RulesetScanPolicy, Path, Path], dict[str, object]]

_FAILURE_ACTIONS = {"rollback", "warn", "fail"}


def _policy_validation_error(*details: dict[str, str]) -> PolicyValidationError:
    return PolicyValidationError("Invalid apply policy.", details=list(details))


def _resolved_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _coerce_optional_string(
    value: object,
    *,
    field: str,
    allow_empty: bool = False,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _policy_validation_error({"field": field, "message": "must be a string or null"})
    if not value.strip() and not allow_empty:
        raise _policy_validation_error({"field": field, "message": "must not be empty"})
    return value


def _coerce_positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise _policy_validation_error({"field": field, "message": "must be a positive integer"})
    return value


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _policy_validation_error({
            "field": "$",
            "message": f"must be valid JSON: {exc.msg}",
        }) from exc
    if not isinstance(payload, dict):
        raise _policy_validation_error({"field": "$", "message": "must be a JSON object"})
    return payload


def _validate_ruleset_scan(
    value: object,
    *,
    policy_dir: Path,
) -> RulesetScanPolicy | None:
    from tensor_grep.cli.rule_packs import resolve_rule_pack

    if value is None:
        return None
    if not isinstance(value, dict):
        raise _policy_validation_error({
            "field": "ruleset_scan",
            "message": "must be an object or null",
        })

    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        raise _policy_validation_error({
            "field": "ruleset_scan.enabled",
            "message": "must be a boolean",
        })

    pack_value = value.get("pack")
    language_value = value.get("language")
    baseline_value = value.get("baseline")

    pack = None
    language = None
    if enabled or pack_value is not None:
        pack = _coerce_optional_string(pack_value, field="ruleset_scan.pack")
    if enabled or language_value is not None:
        language = _coerce_optional_string(language_value, field="ruleset_scan.language")

    baseline = _coerce_optional_string(
        baseline_value,
        field="ruleset_scan.baseline",
        allow_empty=False,
    )
    if baseline is not None:
        baseline_path = Path(baseline)
        if not baseline_path.is_absolute():
            baseline_path = (policy_dir / baseline_path).resolve()
        else:
            baseline_path = baseline_path.resolve()
        # Confine the baseline to the policy directory (round-7 fresh-eyes). Without this, an
        # absolute path, or a `..`-escaping relative path, bypasses the policy_dir anchor and
        # _load_json_object below reads an arbitrary JSON file -- a disclosure primitive when the
        # policy file itself is untrusted (e.g. committed in a cloned repo an agent applies).
        try:
            baseline_path.relative_to(policy_dir.resolve())
        except ValueError:
            raise _policy_validation_error({
                "field": "ruleset_scan.baseline",
                "message": (
                    "baseline path must be within the policy directory "
                    f"({policy_dir}): {baseline_path}"
                ),
            }) from None
        if not baseline_path.exists():
            raise _policy_validation_error({
                "field": "ruleset_scan.baseline",
                "message": f"baseline path does not exist: {baseline_path}",
            })
        _load_json_object(baseline_path)
        baseline = str(baseline_path)

    if enabled:
        if pack is None:
            raise _policy_validation_error({
                "field": "ruleset_scan.pack",
                "message": "must be provided when enabled",
            })
        if language is None:
            raise _policy_validation_error({
                "field": "ruleset_scan.language",
                "message": "must be provided when enabled",
            })
        try:
            resolve_rule_pack(pack, language)
        except ValueError as exc:
            raise _policy_validation_error({
                "field": "ruleset_scan.pack",
                "message": str(exc),
            }) from exc

    return RulesetScanPolicy(
        enabled=enabled,
        pack=pack,
        language=language,
        baseline=baseline,
    )


def load_apply_policy(
    policy_path: str,
    *,
    legacy_lint_cmd: str | None = None,
    legacy_test_cmd: str | None = None,
    allow_validation_commands: bool = True,
) -> ApplyPolicy:
    """Load and validate an apply policy JSON file.

    ``allow_validation_commands`` gates the shell-exec sink: when False, a policy
    that defines ``lint_cmd``/``test_cmd`` fails closed with
    :class:`PolicyCommandsNotAllowedError` before any command can run. It defaults
    to True for the trusted local CLI path (a user who typed ``tg run --policy`` is
    trusted). Untrusted callers — the MCP surface — MUST pass the operator's opt-in
    (``_mcp_validation_commands_allowed()``); ``execute_rewrite_apply_json`` defaults
    this to False so a forgetful future caller also fails closed.
    """
    path = _resolved_path(policy_path)
    if not path.exists():
        raise FileNotFoundError(f"Policy file not found: {path}")

    payload = _load_json_object(path)
    required_fields = ["version", "lint_cmd", "test_cmd", "ruleset_scan", "on_failure"]
    missing_fields = [
        {"field": field, "message": "is required"}
        for field in required_fields
        if field not in payload
    ]
    if missing_fields:
        raise PolicyValidationError("Invalid apply policy.", details=missing_fields)

    version = payload.get("version")
    if version != 1:
        raise _policy_validation_error({"field": "version", "message": "must equal 1"})

    lint_cmd = _coerce_optional_string(payload.get("lint_cmd"), field="lint_cmd") or legacy_lint_cmd
    test_cmd = _coerce_optional_string(payload.get("test_cmd"), field="test_cmd") or legacy_test_cmd

    # Audit HIGH (RCE): fail closed before any command can run. A policy that only
    # performs a (safe) ruleset scan / rollback has lint_cmd == test_cmd == None and
    # is intentionally NOT blocked here — only the shell-exec sink is gated.
    if not allow_validation_commands and (lint_cmd is not None or test_cmd is not None):
        raise PolicyCommandsNotAllowedError(
            "This apply policy defines lint_cmd/test_cmd, which execute a shell "
            "command. That capability is disabled for this caller by default. On the "
            "MCP surface, set TG_MCP_ALLOW_VALIDATION_COMMANDS=1 in the server "
            "environment to opt in (the agent-safe edit loop does not require it)."
        )

    on_failure = payload.get("on_failure")
    if not isinstance(on_failure, str) or on_failure not in _FAILURE_ACTIONS:
        raise _policy_validation_error({
            "field": "on_failure",
            "message": "must be one of rollback, warn, or fail",
        })

    timeout = (
        120
        if "timeout" not in payload
        else _coerce_positive_int(payload.get("timeout"), field="timeout")
    )
    ruleset_scan = _validate_ruleset_scan(payload.get("ruleset_scan"), policy_dir=path.parent)

    return ApplyPolicy(
        path=str(path),
        version=1,
        lint_cmd=lint_cmd,
        test_cmd=test_cmd,
        ruleset_scan=ruleset_scan,
        on_failure=on_failure,
        timeout=timeout,
    )


def _policy_root(path: str | Path, payload: dict[str, object]) -> Path:
    checkpoint_payload = payload.get("checkpoint")
    if isinstance(checkpoint_payload, dict):
        root = checkpoint_payload.get("root")
        if isinstance(root, str) and root.strip():
            return _resolved_path(root)
    resolved = _resolved_path(path)
    return resolved if resolved.is_dir() else resolved.parent


def _command_result(
    *,
    passed: bool,
    detail: str,
    exit_code: int | None = None,
    timed_out: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {"passed": passed, "detail": detail}
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if timed_out:
        payload["timed_out"] = True
    return payload


def _summarize_command_output(name: str, stdout: str, stderr: str, exit_code: int) -> str:
    summary = stderr.strip() or stdout.strip()
    if summary:
        return summary
    return f"{name} command failed with exit code {exit_code}."


def _split_windows_command(command: str) -> list[str]:
    import ctypes
    from ctypes import wintypes

    argc = ctypes.c_int()
    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    argv = shell32.CommandLineToArgvW(command, ctypes.byref(argc))
    if not argv:
        raise ValueError("unable to parse command line")
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)  # type: ignore[attr-defined]


def _unquoted_shell_operator(command: str) -> str | None:
    in_single_quote = False
    in_double_quote = False
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and not in_single_quote:
            escaped = True
            index += 1
            continue
        if char == "'" and not in_double_quote and os.name != "nt":
            in_single_quote = not in_single_quote
            index += 1
            continue
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            index += 1
            continue
        if not in_single_quote and not in_double_quote:
            two_char = command[index : index + 2]
            if two_char in {"&&", "||"}:
                return two_char
            if char in {"|", ";", "&", "<", ">"}:
                return char
        index += 1
    return None


def _parse_policy_command(command: str) -> list[str]:
    stripped = command.strip()
    if not stripped:
        raise ValueError("command must not be empty")
    shell_operator = _unquoted_shell_operator(stripped)
    if shell_operator is not None:
        raise ValueError(
            f"shell control operator {shell_operator!r} is not supported; "
            "provide a single argv-style command"
        )
    if os.name == "nt":
        return _split_windows_command(stripped)
    return shlex.split(stripped, posix=True)


def _policy_quote_arg(value: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


def _file_placeholder_present(command: str) -> bool:
    return "$file" in command or "{file}" in command


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _strip_extended_length_prefix(path_str: str) -> str:
    r"""Strip a Windows ``\\?\`` / ``\\?\UNC\`` extended-length prefix for a STRING
    comparison (#126).

    The prefix opts a path OUT of Windows' normal path processing (component
    normalization, 8.3 lookup, ``MAX_PATH`` checks) and is preserved verbatim by both
    ``os.path.abspath`` and ``Path.resolve()`` -- verified empirically on this box:
    neither adds nor removes it. An untrusted policy's ``lint_cmd``/``test_cmd`` can
    spell its ``argv[0]`` with this prefix explicitly; since ``shutil.which()`` returns
    an already-path-shaped ``argv[0]`` unchanged (a path containing a separator skips
    the PATH search and is returned as-is after an access check), the prefix survives
    all the way to the confinement comparison in ``_abspath_beneath_or_equal``. Left
    unstripped, a prefixed spelling of a location and an unprefixed spelling of the
    identical location never string-match, so the beneath-or-equal check silently
    returns False (not beneath) for a path that IS beneath -- fail OPEN. Stripping it
    here, ahead of ``normcase``/``normpath``, closes that gap for both operands
    uniformly. A no-op on POSIX and on any path that never had the prefix.
    """
    if path_str.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path_str[len("\\\\?\\UNC\\") :]
    if path_str.startswith("\\\\?\\"):
        return path_str[len("\\\\?\\") :]
    return path_str


def _abspath_beneath_or_equal(path: Path, root: Path) -> bool:
    r"""True if ``path`` is ``root`` itself or nested anywhere beneath it (any depth).

    Deliberately independent of ``_path_is_under()``/``Path.relative_to()``: the
    shadow-executable guard in ``_run_policy_command`` must run this check on an
    ``os.path.abspath``'d path that has NOT been passed through ``Path.resolve()`` --
    resolve() follows symlinks, which is exactly how the audit #35 / #453 symlink
    shadow (`repo/tool.cmd -> repo/sub/evil.cmd`) escaped an earlier version of this
    guard (its resolved target's parent no longer lexically starts with the repo
    root). Callers must pass the same abspath-only ``path`` value used for spawning.

    Comparison uses ``os.path.normcase(os.path.normpath(...))`` on both sides (not
    ``Path`` equality/``relative_to``) so a Windows case or separator mismatch between
    the PATH-resolved directory and the untrusted root can't produce a false
    "outside root" negative. The beneath check requires a ``os.sep`` boundary after
    the root prefix, so a sibling directory that merely shares a string prefix with
    the root (e.g. ``repo-other`` next to ``repo``) is never wrongly treated as
    nested inside it. Each side is also run through ``_strip_extended_length_prefix``
    first (#126) so a ``\\?\``-prefixed spelling of a path matches an unprefixed
    spelling of the identical location.
    """
    normalized_path = os.path.normcase(os.path.normpath(_strip_extended_length_prefix(str(path))))
    normalized_root = os.path.normcase(os.path.normpath(_strip_extended_length_prefix(str(root))))
    if normalized_path == normalized_root:
        return True
    return normalized_path.startswith(normalized_root + os.sep)


def _canonicalize_exec_parent(executable_path: Path) -> Path | None:
    r"""Canonicalize ``executable_path``'s PARENT directory chain for the repo-confinement
    comparison (#126, H2 fast-follow -- commit e10c91d explicitly deferred this: "the
    parent-canonicalization hardening (8.3/junction/\?\ edges) is intentionally NOT
    applied here -- it is a tracked fast-follow").

    ``_run_policy_command`` intentionally confines on ``os.path.abspath`` rather than
    ``Path.resolve()`` (see ``_abspath_beneath_or_equal``'s docstring): the #453 defense
    requires a repo-local shadow that is ITSELF a symlink/junction to be judged by where
    it lexically sits, never by where it points, or a symlink escaping the repo to an
    arbitrary outside target would slip the guard. But that same lexical-only comparison
    is bypassable in the OTHER direction: an 8.3 short name (``PROGRA~1`` vs
    ``Program Files``), an NTFS junction alias, or an explicit ``\\?\``-prefixed spelling
    can all name a location INSIDE the untrusted repo while spelling it differently from
    the repo root's own canonical string -- ``_abspath_beneath_or_equal`` then silently
    returns False (not beneath) for a path that IS beneath. Fail open.

    The fix resolves ONLY ``executable_path.parent`` -- never the full path, which would
    dereference the leaf and reopen #453 -- via ``Path.resolve(strict=True)``. Verified
    empirically on Windows: this single call both expands 8.3 short-name components at
    every level of the parent chain (``GetFinalPathNameByHandleW`` returns the
    filesystem's own canonical long name directly; a second ``GetLongPathNameW`` pass is
    not needed) and fully traverses NTFS junctions/symlinks in the parent chain, in one
    step. ``strict=True`` is intentional and mirrors this module's existing
    resolve-or-None convention (``_policy_file_arg``, above): the parent MUST exist by
    the time this runs (``resolved_executable`` was just confirmed to exist by
    ``shutil.which``), so a failure here means a TOCTOU race, a permission error, or
    other OS-level unresolvability -- not a legitimate case worth weakening the guard
    for.

    Returns ``None`` when canonicalization fails for any reason. Callers MUST fail
    closed (deny) on ``None`` -- mirroring the fail-closed contract already established
    at every other confinement chokepoint in this module and in
    ``mcp_server.py::_confine_mcp_path``. Never treat ``None`` as "not beneath root".
    """
    try:
        resolved_parent = executable_path.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved_parent / executable_path.name


def _policy_file_arg(path_value: object, *, working_root: Path) -> str | None:
    if not isinstance(path_value, (str, os.PathLike)):
        return None
    raw_path = Path(path_value).expanduser()
    try:
        resolved = raw_path.resolve()
    except (OSError, RuntimeError):
        return None
    try:
        return resolved.relative_to(working_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _edited_file_args(
    rewrite_payload: dict[str, object],
    *,
    target_path: Path,
    working_root: Path,
) -> list[str]:
    candidates: list[object] = []
    edits = rewrite_payload.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            candidates.append(edit.get("file") or edit.get("path"))
    if not candidates and target_path.is_file() and _path_is_under(target_path, working_root):
        candidates.append(target_path)

    file_args: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        file_arg = _policy_file_arg(candidate, working_root=working_root)
        if file_arg is None or file_arg in seen:
            continue
        seen.add(file_arg)
        file_args.append(file_arg)
    return file_args


def _policy_command_instances(
    name: str,
    command: str,
    *,
    rewrite_payload: dict[str, object],
    target_path: Path,
    working_root: Path,
) -> tuple[list[tuple[str | None, str]], dict[str, object] | None]:
    if not _file_placeholder_present(command):
        return [(None, command)], None

    edited_files = _edited_file_args(
        rewrite_payload,
        target_path=target_path,
        working_root=working_root,
    )
    if not edited_files:
        return [], _command_result(
            passed=False,
            detail=f"{name} command uses a file placeholder but requires at least one edited file.",
        )

    instances: list[tuple[str | None, str]] = []
    for file_arg in edited_files:
        quoted_file = _policy_quote_arg(file_arg)
        instances.append((
            file_arg,
            command.replace("$file", quoted_file).replace("{file}", quoted_file),
        ))
    return instances, None


def _search_path_without_cwd() -> str:
    """Build the ``path=`` string passed to ``shutil.which()``, with cwd-equivalent AND
    cwd-nested PATH entries stripped.

    Defense-in-depth only -- see the load-bearing guard in ``_run_policy_command``.
    On Python < 3.12 + Windows, ``shutil.which()`` unconditionally re-inserts the
    current working directory into its search regardless of the ``path=`` argument
    supplied here (``if curdir not in path: path.insert(0, curdir)`` runs no matter
    what; the ``NeedCurrentDirectoryForExePathW``-gated skip only landed in 3.12 --
    cpython#91558). So stripping "." from our own PATH cannot, by itself, stop that
    implicit prepend. It still closes a narrower, distinct vector: an untrusted
    entry planted directly in the *environment* PATH string itself (an explicit
    ``.`` / empty / cwd-resolving, OR cwd-nested, segment), rather than the implicit
    Windows search.

    codex audit H2 follow-up: originally only an entry resolving EXACTLY to cwd was
    stripped. An untrusted repo's own dependency-manager shim directory --
    ``<repo>/node_modules/.bin``, ``<repo>/.venv/Scripts``, and similar -- is an
    extremely common PATH entry (many build/lint tools prepend it) and is just as
    untrusted as cwd itself, so it must be stripped too. ``_path_is_under()`` covers
    both cases (it treats an exact match as trivially "under" itself as well as any
    depth of descendant), so the two checks collapse into one.
    """
    raw_path = os.environ.get("PATH", "")
    if not raw_path:
        return raw_path
    try:
        cwd = Path.cwd()
    except OSError:
        cwd = None
    filtered: list[str] = []
    for entry in raw_path.split(os.pathsep):
        stripped = entry.strip()
        if not stripped or stripped == os.curdir:
            continue
        if cwd is not None:
            try:
                if _path_is_under(Path(entry), cwd):
                    continue
            except (OSError, RuntimeError, ValueError):
                pass
        filtered.append(entry)
    return os.pathsep.join(filtered)


def _run_policy_command(name: str, command: str, cwd: Path, timeout: int) -> dict[str, object]:
    try:
        argv = _parse_policy_command(command)
    except ValueError as exc:
        return _command_result(
            passed=False,
            detail=f"{name} command could not be parsed: {exc}.",
        )

    # CWE-427: resolve argv[0] against PATH to an absolute path BEFORE spawning. subprocess.run
    # with a relative argv[0] and cwd=<target repo> lets a shadow executable planted in the
    # untrusted repo (which Windows CreateProcess searches) pre-empt the real tool. Passing an
    # absolute PATH-resolved binary removes the cwd search; fail closed if the tool is not on PATH.
    #
    # audit #35 (CWE-427 refinement, Windows cwd shadow-exe): the resolve-to-absolute above is
    # INSUFFICIENT by itself on Windows. shutil.which() there searches the current working
    # directory regardless of what `path=` we pass, because WinAPI NeedCurrentDirectoryForExePath
    # returns True BY DEFAULT -- disabled only by the `NoDefaultCurrentDirectoryInExePath` env var
    # (the cpython#91558 / 3.12 change only made which() *consult* that API; the default cwd search
    # is live on 3.11/3.12/3.13/3.14). So this is a WINDOWS-ENV condition, NOT a Python-version one
    # -- do not weaken the guard below for any Python version. A bare "strip os.curdir from our own
    # PATH" is not load-bearing: which() re-adds cwd itself. Two things:
    #   1) _search_path_without_cwd() passes an explicit `path=` with empty/"."/cwd-resolving
    #      entries stripped, closing the narrower case where the *environment* PATH string
    #      itself carries such an entry; and
    #   2) reject the resolution outright if it lands inside the untrusted target root (`cwd`) --
    #      the load-bearing guard. It compares os.path.abspath, NOT Path.resolve: resolve() FOLLOWS
    #      symlinks, which let a `repo/tool.cmd -> repo/sub/evil.cmd` symlink land its parent
    #      OUTSIDE cwd and slip a parent==cwd check (caught by the adversarial security gate).
    #
    # codex audit H2 (CWE-427 further refinement): "lands inside cwd" means BENEATH cwd at any
    # depth, not merely "cwd is its immediate parent". An earlier version of this guard checked
    # `resolved_path.parent == untrusted_root`, which only catches a shadow planted directly at
    # the repo root -- a shadow resolved to `<repo>/nested/dir/tool.exe` has
    # `.parent == <repo>/nested/dir`, never equal to `<repo>`, so it slipped through to
    # subprocess.run. _abspath_beneath_or_equal() checks the full resolved_path (not just its
    # parent) against untrusted_root using relative-containment, so any depth is caught; it keeps
    # operating on the same abspath-only (non-symlink-following) resolved_path as before, so the
    # #453 symlink-escape fix above still holds.
    resolved_executable = shutil.which(argv[0], path=_search_path_without_cwd()) if argv else None
    if resolved_executable is None:
        return _command_result(
            passed=False,
            detail=f"{name} command executable {(argv[0] if argv else command)!r} was not found on PATH.",
        )
    resolved_path = Path(os.path.abspath(resolved_executable))

    # #126 (Opus re-gate, 4th same-class edge -- UNC / network-share smuggling): a UNC spelling of
    # the executable -- \\host\share\..., the loopback admin-share \\127.0.0.1\C$\...\<repo>\evil.cmd
    # / \\localhost\C$\..., or the \\?\UNC\... extended form -- names the SAME on-disk in-repo shadow
    # through the network/admin-share namespace, which Path.resolve() does NOT map back to its C:\
    # drive-letter form (verified empirically). So _canonicalize_exec_parent returns a still-UNC
    # parent and NEITHER the raw-stripped nor the canonical beneath-compare starts with the local
    # repo-root string -- the OR-guard below would pass and the shadow would spawn (reproduced live
    # end-to-end by the adversarial gate on a box where the C$ admin share is reachable). A
    # UNC-spelled executable can never be confined to a LOCAL drive-letter repo root by a string
    # comparison, and a legitimate local tool always resolves to a drive-letter path (C:\..., and
    # \\?\C:\... -> C:\... after the ext-length strip -- NOT a UNC prefix), so refuse any UNC
    # executable path outright. Fail closed, before the beneath-guard.
    stripped_exec = _strip_extended_length_prefix(str(resolved_path))
    if stripped_exec.startswith("\\\\"):
        return _command_result(
            passed=False,
            detail=(
                f"{name} command executable {argv[0]!r}: refusing a UNC/network-share "
                f"executable path {resolved_path} (cannot be confined to the local repo root)."
            ),
        )

    try:
        untrusted_root = cwd.resolve()
    except OSError:
        untrusted_root = cwd

    # #126 (Windows canonicalization fail-open, H2 fast-follow -- e10c91d deferred this):
    # resolved_path above is deliberately lexical-only (os.path.abspath, no symlink-follow --
    # see _abspath_beneath_or_equal's docstring), which an 8.3 short name / NTFS junction
    # alias / \?\-prefixed spelling of an in-repo location can evade. _canonicalize_exec_parent
    # closes that gap without reopening #453 (it never dereferences the leaf). Checked WITH,
    # never INSTEAD OF, the raw lexical guard below: the two conditions cover disjoint gaps --
    # raw catches "spelled in-repo", canonical catches "resolves in-repo but spelled
    # differently" -- so ORing them only ever denies a superset of what either denies alone;
    # a legitimate outside-root binary fails both and is never affected. Canonicalization
    # failure fails closed (denied), matching every other confinement chokepoint in this
    # module.
    canonical_exec_parent = _canonicalize_exec_parent(resolved_path)
    if canonical_exec_parent is None:
        return _command_result(
            passed=False,
            detail=(
                f"{name} command executable {argv[0]!r}: could not canonicalize "
                f"{resolved_path} for the repo-confinement check; refusing to run it."
            ),
        )
    if _abspath_beneath_or_equal(resolved_path, untrusted_root) or _abspath_beneath_or_equal(
        canonical_exec_parent, untrusted_root
    ):
        return _command_result(
            passed=False,
            detail=(
                f"{name} command executable {argv[0]!r}: refusing a repo-local executable "
                f"shadow: {resolved_path} (resolves inside the untrusted target repository "
                f"{untrusted_root})."
            ),
        )
    argv = [str(resolved_path), *argv[1:]]

    try:
        completed = subprocess.run(
            argv,
            shell=False,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _command_result(
            passed=False,
            detail=f"Command timed out after {timeout}s.",
            timed_out=True,
        )
    except OSError as exc:
        return _command_result(
            passed=False,
            detail=f"{name} command failed to start: {exc}",
        )

    if completed.returncode == 0:
        return _command_result(passed=True, detail=f"{name} command succeeded.", exit_code=0)

    return _command_result(
        passed=False,
        detail=_summarize_command_output(
            name,
            completed.stdout or "",
            completed.stderr or "",
            completed.returncode,
        ),
        exit_code=completed.returncode,
    )


def _run_ruleset_scan_policy(
    policy: RulesetScanPolicy,
    target_path: Path,
    _working_root: Path,
) -> dict[str, object]:
    from tensor_grep.cli.main import _run_ast_scan_payload
    from tensor_grep.cli.rule_packs import resolve_rule_pack

    if not policy.enabled:
        return _command_result(passed=True, detail="Ruleset scan disabled.")

    assert policy.pack is not None
    assert policy.language is not None
    ruleset_meta, rules = resolve_rule_pack(policy.pack, policy.language)
    scan_root = target_path if target_path.is_dir() else target_path.parent
    payload = _run_ast_scan_payload(
        {
            "config_path": f"builtin:{ruleset_meta['name']}",
            "root_dir": scan_root,
            "rule_dirs": [],
            "test_dirs": [],
            "language": ruleset_meta["language"],
        },
        rules,
        routing_reason="builtin-ruleset-scan",
        ruleset_name=ruleset_meta["name"],
        baseline_path=policy.baseline,
    )
    raw_backends = payload.get("backends")
    backend_items = raw_backends if isinstance(raw_backends, list) else []
    backend_names = [item for item in backend_items if isinstance(item, str) and item.strip()]
    if backend_names and not {
        "AstBackend",
        "AstGrepWrapperBackend",
    }.intersection(backend_names):
        result = _command_result(
            passed=False,
            detail=(
                "Ruleset scan requires an AST backend; "
                f"resolved {', '.join(sorted(backend_names))}."
            ),
        )
        result["new_findings"] = 0
        result["ruleset"] = ruleset_meta["name"]
        result["language"] = ruleset_meta["language"]
        if policy.baseline is not None:
            result["baseline_path"] = policy.baseline
        return result
    baseline_summary = payload.get("baseline")
    if isinstance(baseline_summary, dict):
        new_findings = int(baseline_summary.get("new_findings", 0))
    else:
        findings = payload.get("findings")
        finding_rows = findings if isinstance(findings, list) else []
        new_findings = sum(
            1
            for finding in finding_rows
            if isinstance(finding, dict) and finding.get("status") == "new"
        )
    passed = new_findings == 0
    detail = (
        f"No new findings for ruleset {ruleset_meta['name']}."
        if passed
        else f"{new_findings} new finding(s) detected by ruleset {ruleset_meta['name']}."
    )
    result = _command_result(passed=passed, detail=detail)
    result["new_findings"] = new_findings
    result["ruleset"] = ruleset_meta["name"]
    result["language"] = ruleset_meta["language"]
    if policy.baseline is not None:
        result["baseline_path"] = policy.baseline
    return result


def _check_row(name: str, result: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": name,
        "passed": bool(result["passed"]),
        "detail": str(result["detail"]),
    }
    if "exit_code" in result:
        payload["exit_code"] = result["exit_code"]
    if result.get("timed_out"):
        payload["timed_out"] = True
    return payload


def _rollback_summary(
    *,
    payload: dict[str, object],
    working_root: Path,
) -> dict[str, object]:
    checkpoint_payload = payload.get("checkpoint")
    checkpoint_id = None
    if isinstance(checkpoint_payload, dict):
        raw_checkpoint_id = checkpoint_payload.get("checkpoint_id")
        if isinstance(raw_checkpoint_id, str) and raw_checkpoint_id.strip():
            checkpoint_id = raw_checkpoint_id
    if checkpoint_id is None:
        return {"performed": False}

    undone = undo_checkpoint(checkpoint_id, str(working_root))
    return {
        "performed": True,
        "checkpoint_id": checkpoint_id,
        "restored_files": undone.restored_files,
        "removed_paths": undone.removed_paths,
        "mode": undone.mode,
        "root": undone.root,
    }


def evaluate_apply_policy(
    rewrite_payload: dict[str, object],
    policy: ApplyPolicy,
    *,
    path: str,
    run_command_fn: CommandRunner | None = None,
    scan_runner_fn: ScanRunner | None = None,
) -> tuple[dict[str, object], int]:
    payload = dict(rewrite_payload)
    working_root = _policy_root(path, payload)
    target_path = _resolved_path(path)
    run_command = run_command_fn or _run_policy_command
    scan_runner = scan_runner_fn or _run_ruleset_scan_policy

    checks: list[dict[str, object]] = []
    failures = False

    def run_and_record(name: str, result: dict[str, object], *, top_level_key: str) -> bool:
        nonlocal failures
        payload[top_level_key] = dict(result)
        checks.append(_check_row(name, result))
        failed = not bool(result["passed"])
        failures = failures or failed
        return failed

    def run_command_group(name: str, command: str, *, top_level_key: str) -> bool:
        instances, expansion_error = _policy_command_instances(
            name,
            command,
            rewrite_payload=payload,
            target_path=target_path,
            working_root=working_root,
        )
        if expansion_error is not None:
            return run_and_record(name, expansion_error, top_level_key=top_level_key)
        if len(instances) == 1 and instances[0][0] is None:
            return run_and_record(
                name,
                run_command(name, instances[0][1], working_root, policy.timeout),
                top_level_key=top_level_key,
            )

        per_file_results: list[dict[str, object]] = []
        for file_arg, expanded_command in instances:
            result = dict(run_command(name, expanded_command, working_root, policy.timeout))
            result["file"] = file_arg
            result["command"] = expanded_command
            per_file_results.append(result)
            if not bool(result.get("passed")) and policy.on_failure in {"rollback", "fail"}:
                break

        failed_results = [result for result in per_file_results if not bool(result.get("passed"))]
        aggregate: dict[str, object] = {
            "passed": not failed_results,
            "detail": (
                f"{name} command succeeded for {len(per_file_results)} file(s)."
                if not failed_results
                else f"{len(failed_results)} of {len(per_file_results)} {name} command(s) failed."
            ),
            "file_count": len(instances),
            "attempted_count": len(per_file_results),
            "failed_count": len(failed_results),
            "results": per_file_results,
        }
        for result in failed_results:
            if "exit_code" in result:
                aggregate["exit_code"] = result["exit_code"]
                break
        if any(result.get("timed_out") for result in per_file_results):
            aggregate["timed_out"] = True
        return run_and_record(name, aggregate, top_level_key=top_level_key)

    if policy.lint_cmd is not None:
        if run_command_group(
            "lint",
            policy.lint_cmd,
            top_level_key="lint_result",
        ) and policy.on_failure in {"rollback", "fail"}:
            pass
        else:
            if policy.test_cmd is not None:
                if run_command_group(
                    "test",
                    policy.test_cmd,
                    top_level_key="test_result",
                ) and policy.on_failure in {"rollback", "fail"}:
                    pass
                else:
                    if policy.ruleset_scan is not None and policy.ruleset_scan.enabled:
                        run_and_record(
                            "scan",
                            scan_runner(policy.ruleset_scan, target_path, working_root),
                            top_level_key="scan_result",
                        )
            elif policy.ruleset_scan is not None and policy.ruleset_scan.enabled:
                run_and_record(
                    "scan",
                    scan_runner(policy.ruleset_scan, target_path, working_root),
                    top_level_key="scan_result",
                )
    else:
        if policy.test_cmd is not None:
            if run_command_group(
                "test",
                policy.test_cmd,
                top_level_key="test_result",
            ) and policy.on_failure in {"rollback", "fail"}:
                pass
            else:
                if policy.ruleset_scan is not None and policy.ruleset_scan.enabled:
                    run_and_record(
                        "scan",
                        scan_runner(policy.ruleset_scan, target_path, working_root),
                        top_level_key="scan_result",
                    )
        elif policy.ruleset_scan is not None and policy.ruleset_scan.enabled:
            run_and_record(
                "scan",
                scan_runner(policy.ruleset_scan, target_path, working_root),
                top_level_key="scan_result",
            )

    if failures:
        if policy.on_failure == "warn":
            action_taken = "warn"
            exit_code = 0
        elif policy.on_failure == "rollback":
            rollback_summary = _rollback_summary(payload=payload, working_root=working_root)
            payload["rollback"] = rollback_summary
            # H8: only report "rollback" when a checkpoint actually restored the working
            # tree. _rollback_summary returns {"performed": False} when there's no usable
            # checkpoint_id -- reporting "rollback" in that case would tell an agent the
            # failed edit was reverted when it is still on disk (a phantom-rollback receipt).
            action_taken = (
                "rollback" if rollback_summary.get("performed") else "rollback_unavailable"
            )
            exit_code = 1
        else:
            action_taken = "fail"
            exit_code = 1
    else:
        action_taken = "none"
        exit_code = 0
        if policy.on_failure == "rollback" and "checkpoint" in payload:
            checkpoint_payload = payload.get("checkpoint")
            checkpoint_id = None
            if isinstance(checkpoint_payload, dict):
                raw_checkpoint_id = checkpoint_payload.get("checkpoint_id")
                if isinstance(raw_checkpoint_id, str) and raw_checkpoint_id.strip():
                    checkpoint_id = raw_checkpoint_id
            if checkpoint_id is not None:
                payload["rollback"] = {"performed": False, "checkpoint_id": checkpoint_id}

    payload["policy_result"] = {
        "policy_path": policy.path,
        "checks": checks,
        "all_passed": not failures,
        "action_taken": action_taken,
    }
    return payload, exit_code
