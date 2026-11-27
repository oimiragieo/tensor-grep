from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest


def _policy_command(*args: object) -> str:
    argv = [str(arg) for arg in args]
    if sys.platform == "win32":
        return subprocess.list2cmdline(argv)
    return " ".join(shlex.quote(arg) for arg in argv)


def _write_fake_executable(directory: Path, name: str, echo: str) -> Path:
    """Create a minimal, genuinely-executable stub named ``name`` in ``directory``.

    Cross-platform so the shadow-executable tests (audit #35) are deterministic on
    any box, per the test-path-resolved-binary-trap lesson: never resolve a real
    system binary in a security test. Windows resolves commands via PATHEXT (a
    bare ``name`` needs an extension like ``.cmd``); POSIX resolves by the exact
    filename plus the executable permission bit.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        target = directory / f"{name}.cmd"
        target.write_text(f"@echo {echo}\n", encoding="utf-8")
    else:
        target = directory / name
        target.write_text(f"#!/bin/sh\necho {echo}\n", encoding="utf-8")
        target.chmod(0o755)
    return target


def test_abspath_beneath_or_equal_matches_root_and_nested_descendants(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import _abspath_beneath_or_equal

    root = tmp_path / "repo"
    assert _abspath_beneath_or_equal(root, root) is True
    assert _abspath_beneath_or_equal(root / "tool.exe", root) is True
    assert _abspath_beneath_or_equal(root / "nested" / "dir" / "tool.exe", root) is True


def test_abspath_beneath_or_equal_rejects_unrelated_and_sibling_prefix_paths(
    tmp_path: Path,
) -> None:
    """A sibling directory that merely shares a string PREFIX with the root (e.g.
    `repo-other` vs `repo`) must NOT be treated as beneath it -- a naive
    ``str.startswith(root)`` without a path-separator boundary would wrongly match this."""
    from tensor_grep.cli.apply_policy import _abspath_beneath_or_equal

    root = tmp_path / "repo"
    assert _abspath_beneath_or_equal(tmp_path / "unrelated" / "tool.exe", root) is False
    assert _abspath_beneath_or_equal(tmp_path / "repo-other" / "tool.exe", root) is False
    assert _abspath_beneath_or_equal(tmp_path / "repository" / "tool.exe", root) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows case-insensitive path semantics")
def test_abspath_beneath_or_equal_is_case_insensitive_on_windows(tmp_path: Path) -> None:
    from tensor_grep.cli.apply_policy import _abspath_beneath_or_equal

    root = tmp_path / "repo"
    upper = Path(str(tmp_path).upper()) / "REPO" / "NESTED" / "tool.EXE"
    assert _abspath_beneath_or_equal(upper, root) is True


def test_search_path_without_cwd_strips_entries_nested_under_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    """Widen (H2 follow-up, codex audit): _search_path_without_cwd previously stripped only
    PATH entries that resolved EXACTLY to cwd. A PATH entry NESTED under cwd (e.g. the
    extremely common `<repo>/node_modules/.bin` or `<repo>/.venv/Scripts`) must also be
    stripped -- an untrusted repo's own dependency-manager shim directory should not be
    handed to shutil.which() as a searchable, quasi-trusted PATH entry."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "repo"
    repo.mkdir()
    nested_bin = repo / "node_modules" / ".bin"
    nested_bin.mkdir(parents=True)
    venv_scripts = repo / ".venv" / "Scripts"
    venv_scripts.mkdir(parents=True)
    unrelated = tmp_path / "trusted-bin"
    unrelated.mkdir()

    monkeypatch.chdir(repo)
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(nested_bin), str(venv_scripts), str(unrelated), str(repo)]),
    )

    filtered = apply_policy._search_path_without_cwd()
    filtered_entries = filtered.split(os.pathsep) if filtered else []

    assert str(nested_bin) not in filtered_entries
    assert str(venv_scripts) not in filtered_entries
    assert str(repo) not in filtered_entries  # pre-existing exact-match behavior, still holds
    assert str(unrelated) in filtered_entries


def test_search_path_without_cwd_keeps_sibling_prefix_directory(
    tmp_path: Path, monkeypatch
) -> None:
    """A PATH entry that merely shares a string prefix with cwd (e.g. `repo-tools` next to
    `repo`) must NOT be stripped -- mirrors the separator-boundary check on the executable
    guard, applied here to the PATH-filtering defense-in-depth layer."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "repo"
    repo.mkdir()
    sibling_prefix = tmp_path / "repo-tools"
    sibling_prefix.mkdir()

    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", str(sibling_prefix))

    filtered = apply_policy._search_path_without_cwd()
    filtered_entries = filtered.split(os.pathsep) if filtered else []
    assert str(sibling_prefix) in filtered_entries


# --- #126: canonicalize the exec parent before the apply_policy confinement decision ---
#
# commit e10c91d (H2, #509) fixed the beneath-or-equal depth bug but explicitly deferred this:
# "Guard logic unchanged; the parent-canonicalization hardening (8.3/junction/\?\ edges) is
# intentionally NOT applied here -- it is a tracked fast-follow." resolved_path in
# _run_policy_command is built via os.path.abspath (a purely LEXICAL normalization, deliberately
# NOT Path.resolve() -- see _abspath_beneath_or_equal's docstring and the #453 regression test
# above) so that a repo-local shadow which is itself a symlink is judged by where it lexically
# sits, never by where it points. But that same lexical-only comparison can be BYPASSED in the
# opposite direction: an 8.3 short name (PROGRA~1 vs "Program Files"), an NTFS junction alias, or
# an explicit \\?\ / \\?\UNC\ extended-length prefix can all spell a location INSIDE the untrusted
# repo differently from the repo root's own canonical string, so the plain startswith-style check
# silently returns False (not beneath) for a path that IS beneath -- fail OPEN.


def test_strip_extended_length_prefix_removes_prefix() -> None:
    r"""Pure string-level unit test (platform-agnostic): the \\?\ extended-length prefix is
    preserved verbatim by both os.path.abspath and Path.resolve() (verified empirically --
    neither adds nor strips it), so it must be stripped explicitly before a normcase/normpath
    confinement comparison."""
    from tensor_grep.cli.apply_policy import _strip_extended_length_prefix

    assert _strip_extended_length_prefix("\\\\?\\C:\\repo\\evil.cmd") == "C:\\repo\\evil.cmd"


def test_strip_extended_length_prefix_removes_unc_prefix() -> None:
    from tensor_grep.cli.apply_policy import _strip_extended_length_prefix

    assert (
        _strip_extended_length_prefix("\\\\?\\UNC\\server\\share\\evil.cmd")
        == "\\\\server\\share\\evil.cmd"
    )


def test_strip_extended_length_prefix_is_a_noop_for_normal_paths() -> None:
    from tensor_grep.cli.apply_policy import _strip_extended_length_prefix

    assert _strip_extended_length_prefix("C:\\repo\\evil.cmd") == "C:\\repo\\evil.cmd"
    assert _strip_extended_length_prefix("/home/user/repo/evil") == "/home/user/repo/evil"


@pytest.mark.skipif(
    sys.platform != "win32", reason="\\\\?\\ extended-length prefix is Windows-only"
)
def test_abspath_beneath_or_equal_matches_extended_length_prefixed_spelling(
    tmp_path: Path,
) -> None:
    r"""A \\?\-prefixed spelling of a path INSIDE root must still compare beneath root -- the
    exact fail-open #126 closes: before the fix these two spellings of the identical location
    compared as unrelated strings."""
    from tensor_grep.cli.apply_policy import _abspath_beneath_or_equal

    root = tmp_path / "repo"
    prefixed = Path("\\\\?\\" + str(root) + "\\evil.cmd")
    assert _abspath_beneath_or_equal(prefixed, root) is True


@pytest.mark.skipif(
    sys.platform != "win32", reason="\\\\?\\ extended-length prefix is Windows-only"
)
def test_abspath_beneath_or_equal_extended_length_prefix_no_false_positive(
    tmp_path: Path,
) -> None:
    r"""Stripping the \\?\ prefix must not make an outside-root path look beneath root."""
    from tensor_grep.cli.apply_policy import _abspath_beneath_or_equal

    root = tmp_path / "repo"
    prefixed_unrelated = Path("\\\\?\\" + str(tmp_path / "unrelated") + "\\evil.cmd")
    assert _abspath_beneath_or_equal(prefixed_unrelated, root) is False


def test_canonicalize_exec_parent_fails_closed_on_nonexistent_parent(tmp_path: Path) -> None:
    """Canonicalization failure (a parent directory that doesn't resolve) must return None,
    never a best-effort guess -- callers fail closed (deny) on None."""
    from tensor_grep.cli.apply_policy import _canonicalize_exec_parent

    missing = tmp_path / "does-not-exist-xyz" / "tool.cmd"
    assert _canonicalize_exec_parent(missing) is None


def test_canonicalize_exec_parent_does_not_dereference_the_leaf_symlink(tmp_path: Path) -> None:
    """#453 preservation: _canonicalize_exec_parent must resolve ONLY the executable's parent
    directory, never the leaf itself. If the leaf is a symlink whose target is OUTSIDE the repo,
    canonicalizing it must NOT follow the link there -- that is exactly the #453 RCE (a
    repo-local shadow that is a symlink escaping confinement by resolving outside the repo)."""
    from tensor_grep.cli.apply_policy import _canonicalize_exec_parent

    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    payload = _write_fake_executable(outside, "evil", "pwned")
    shadow = repo / "ruff.cmd"
    try:
        shadow.symlink_to(payload)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this box")

    canonical = _canonicalize_exec_parent(shadow)

    assert canonical is not None
    assert canonical.parent == repo.resolve()
    assert canonical.name == "ruff.cmd"


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 short names are a Windows-only concept")
def test_canonicalize_exec_parent_expands_real_8dot3_short_name() -> None:
    """Uses the OS-provided `C:\\PROGRA~1` short name for `C:\\Program Files`, present on every
    Windows install and readable without admin/write access -- unlike forcing FRESH 8.3
    generation on a throwaway test directory, which needs `fsutil 8dot3name set <path> 0` (an
    elevated volume handle; verified 'Error 5: Access is denied' without admin on this dev box).
    """
    from tensor_grep.cli.apply_policy import _canonicalize_exec_parent

    short_form_parent = Path(r"C:\PROGRA~1")
    if not short_form_parent.is_dir():
        pytest.skip("C:\\PROGRA~1 8.3 alias is not available on this box")

    canonical = _canonicalize_exec_parent(short_form_parent / "tool.cmd")

    assert canonical is not None
    assert "PROGRA~1" not in str(canonical)
    assert canonical.parent == Path(r"C:\Program Files").resolve()
    assert canonical.name == "tool.cmd"


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are a Windows-only concept")
def test_canonicalize_exec_parent_resolves_real_junction(tmp_path: Path) -> None:
    """`mklink /J` (a directory junction) needs no admin privilege, unlike a symlink -- this
    reproduces the real reparse-point mechanism end-to-end."""
    from tensor_grep.cli.apply_policy import _canonicalize_exec_parent

    real_target = tmp_path / "real-target"
    real_target.mkdir()
    junction = tmp_path / "junction-alias"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(real_target)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"junction creation not permitted on this box: {proc.stderr}")

    canonical = _canonicalize_exec_parent(junction / "tool.cmd")

    assert canonical is not None
    assert canonical.parent == real_target.resolve()
    assert canonical.name == "tool.cmd"
    assert "junction-alias" not in str(canonical)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows path-namespace semantics (8.3 short-name / NTFS junction / "
    "extended-length prefix / UNC admin-share). On POSIX these argv[0] strings are literal "
    "filenames (backslash is a valid char, no drive letter), so the shadow is still DENIED "
    "fail-closed -- shutil.which returns None for the non-existent literal path -- but via a "
    "different code path (not-found-on-PATH) than this Windows-specific assertion checks.",
)
def test_run_policy_command_rejects_extended_length_prefix_shadow_inside_repo(
    tmp_path: Path, monkeypatch
) -> None:
    r"""#126 bidirectional oracle (\\?\ edge), fully real: the policy's own command string
    spells argv[0] with an explicit \\?\ extended-length prefix pointing at a real repo-local
    shadow. shutil.which() treats an already-path-shaped argv[0] as-is (no PATH search, no
    canonicalization -- verified empirically), so the prefix survives all the way to the
    confinement check. Pre-fix this compares as an unrelated string (fail open, spawned);
    post-fix _abspath_beneath_or_equal strips the prefix before comparing (denied)."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    shadow = _write_fake_executable(repo, "evil", "shadow-pwned")

    extended_prefixed = "\\\\?\\" + str(shadow.resolve())
    command = _policy_command(extended_prefixed, "--check")

    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("test", command, repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls, "the \\?\\-prefixed repo-local shadow must never be spawned"
    assert "refusing a repo-local executable shadow" in str(result["detail"])


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only NTFS junction (mklink /J via cmd.exe, absent on POSIX).",
)
def test_run_policy_command_rejects_junction_alias_shadow_inside_repo(
    tmp_path: Path, monkeypatch
) -> None:
    """#126 bidirectional oracle (junction edge), fully real: a repo-local shadow is resolved
    via an NTFS junction alias that lives OUTSIDE the repo but points AT it, so its lexical
    string never starts with the repo root's string even though it names the same on-disk
    file. Pre-fix: fail open (spawned). Post-fix: _canonicalize_exec_parent resolves through
    the junction and the OR-guard denies it."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    shadow = _write_fake_executable(repo, "pytest", "shadow-pwned")

    alias = tmp_path / "alias-outside-repo"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(alias), str(repo)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"junction creation not permitted on this box: {proc.stderr}")

    aliased_shadow = str(alias / shadow.name)
    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: aliased_shadow if cmd == "pytest" else None,
    )
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("test", "pytest --check", repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls, "the junction-aliased repo-local shadow must never be spawned"
    assert "refusing a repo-local executable shadow" in str(result["detail"])


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only NTFS junction (mklink /J via cmd.exe, absent on POSIX).",
)
def test_run_policy_command_allows_junction_alias_that_resolves_outside_repo(
    tmp_path: Path, monkeypatch
) -> None:
    """No regression: a LEGITIMATE trusted binary reached via a junction alias, where both the
    alias and its real target are genuinely outside the untrusted repo, must still be allowed
    to run -- canonicalization must not manufacture a false positive."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()

    real_trusted = tmp_path / "real-trusted-bin"
    real_tool = _write_fake_executable(real_trusted, "ruff", "real-tool")
    trusted_alias = tmp_path / "trusted-alias"
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(trusted_alias), str(real_trusted)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"junction creation not permitted on this box: {proc.stderr}")

    aliased_tool = str(trusted_alias / real_tool.name)
    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: aliased_tool if cmd == "ruff" else None,
    )
    captured: dict[str, object] = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        import subprocess as _sp

        return _sp.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(apply_policy.subprocess, "run", _fake_run)

    result = apply_policy._run_policy_command("lint", "ruff check .", repo, timeout=10)

    assert result["passed"] is True
    assert captured.get("argv") is not None


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only 8.3 short-name scenario (the ~1 short alias + the Path.resolve() "
    "8.3-expansion behavior this simulates are Windows path-namespace concepts).",
)
def test_run_policy_command_rejects_8dot3_alias_shadow_inside_repo(
    tmp_path: Path, monkeypatch
) -> None:
    """#126 bidirectional oracle (8.3 edge). A fully real reproduction needs FRESH NTFS 8.3
    short-name generation forced on a throwaway test directory (`fsutil 8dot3name set <path>
    0`), which needs an elevated volume handle -- verified 'Error 5: Access is denied' without
    admin on this dev box, and 8dot3 generation is disabled by default for new directories on
    modern Windows anyway. The real mechanism (Path.resolve(strict=True) expanding an 8.3 alias)
    is proven directly against the OS's own always-present `C:\\PROGRA~1` short name in
    test_canonicalize_exec_parent_expands_real_8dot3_short_name. This test proves the
    _run_policy_command WIRING side: shutil.which() returns an 8.3-shaped short alias (the exact
    string shape GetShortPathNameW produces: a truncated stem + `~1`) of a real repo-local
    shadow, and _canonicalize_exec_parent is monkeypatched to return exactly what a real
    Path.resolve(strict=True) call resolves an 8.3 alias to (its own proven behavior) -- so the
    only new thing under test here is that _run_policy_command's OR-guard denies when the
    CANONICAL form (not the raw lexical form) lands inside the repo."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo-with-a-long-name"
    shadow = _write_fake_executable(repo, "pytest", "shadow-pwned")

    short_alias = str(tmp_path / "UNTRUS~1" / "pytest.cmd")
    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: short_alias if cmd == "pytest" else None,
    )
    monkeypatch.setattr(
        apply_policy,
        "_canonicalize_exec_parent",
        lambda executable_path: shadow.resolve(),
    )
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("test", "pytest --check", repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls, "the 8.3-aliased repo-local shadow must never be spawned"
    assert "refusing a repo-local executable shadow" in str(result["detail"])


def test_run_policy_command_denies_when_exec_parent_canonicalization_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """Fail closed: when _canonicalize_exec_parent cannot resolve the executable's parent for
    any reason, _run_policy_command must deny, never fall back to allowing the command through
    on the raw (un-canonicalized) comparison alone."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()
    trusted = tmp_path / "trusted-bin"
    real_tool = _write_fake_executable(trusted, "ruff", "real-tool")

    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: str(real_tool) if cmd == "ruff" else None,
    )
    monkeypatch.setattr(apply_policy, "_canonicalize_exec_parent", lambda executable_path: None)
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("lint", "ruff check .", repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls
    assert "could not canonicalize" in str(result["detail"])


# --- #126 review re-gate: the 4th same-class edge -- UNC / network-share smuggling ---
#
# The gate reproduced a fourth bypass END-TO-END against the real _run_policy_command: a UNC
# loopback admin-share spelling of the identical in-repo shadow gets SPAWNED. argv[0] spellings of
# <untrusted-repo>\evil.cmd like \\127.0.0.1\C$\...\untrusted-repo\evil.cmd, \\localhost\C$\...,
# and \\?\UNC\127.0.0.1\C$\... all pass the guard and reach subprocess.run because Path.resolve()
# does NOT map the \\host\C$\... admin-share namespace back to C:\..., so _canonicalize_exec_parent
# returns a still-UNC parent and neither the raw-stripped nor the canonical beneath-compare starts
# with the repo-root string. A UNC-spelled executable can never be confined to a LOCAL drive-letter
# repo root by a string comparison, so _run_policy_command now refuses any UNC executable path
# outright (fail closed). Verified empirically: a legitimate local tool always resolves to a
# drive-letter path (C:\..., and \\?\C:\...->C:\... after the ext-length strip), never a UNC prefix.


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only UNC / admin-share (C$) path namespace. On POSIX a \\\\host\\C$\\ "
    "string is a literal relative filename (no network/admin-share, no drive letter), so the "
    "shadow is still DENIED fail-closed but via not-found/could-not-canonicalize, not this "
    "Windows-specific UNC-refusal assertion. The UNC guard itself is inert on POSIX by design "
    "(os.path.abspath never yields a \\\\-leading path there).",
)
@pytest.mark.parametrize(
    "unc_prefix",
    [
        "\\\\127.0.0.1\\C$\\",  # loopback IP admin share
        "\\\\localhost\\C$\\",  # loopback hostname admin share
        "\\\\?\\UNC\\127.0.0.1\\C$\\",  # extended-length UNC form of the same
    ],
)
def test_run_policy_command_rejects_unc_admin_share_shadow_inside_repo(
    tmp_path: Path, monkeypatch, unc_prefix: str
) -> None:
    r"""#126 bidirectional oracle (UNC / admin-share edge). A repo-local shadow is reached via a
    UNC loopback admin-share (C$) spelling of the identical on-disk file. Pre-fix: fail open
    (Path.resolve() leaves the parent UNC, so no beneath-compare matches -> spawned). Post-fix:
    _run_policy_command refuses any UNC-spelled executable outright (it can never be confined to
    a local drive-letter repo root).

    Platform-agnostic on purpose: the refusal is a pure string check on argv[0]'s shape (does it
    resolve to a \\... UNC path), which behaves identically on any OS -- no live C$ share or admin
    token is needed to exercise the guard, only to exploit the underlying bypass. shutil.which()
    returns an already-path-shaped argv[0] unchanged (no PATH search, no canonicalization), so the
    UNC spelling reaches the guard verbatim."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    shadow = _write_fake_executable(repo, "pytest", "shadow-pwned")

    # Build the UNC spelling of the SAME on-disk shadow. Only the drive-letter tail is reused; the
    # UNC prefix (loopback admin share) is what Path.resolve() cannot fold back to a local path.
    drive_tail = str(shadow.resolve())[3:]  # strip "C:\" -> "Users\...\untrusted-repo\pytest.cmd"
    unc_shadow = unc_prefix + drive_tail
    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: unc_shadow if cmd == "pytest" else None,
    )
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("test", "pytest --check", repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls, "a UNC-spelled repo-local shadow must never be spawned"
    assert "UNC/network-share" in str(result["detail"])


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only UNC / network-share path namespace (see the admin-share test above).",
)
def test_run_policy_command_rejects_unc_share_even_when_outside_repo(
    tmp_path: Path, monkeypatch
) -> None:
    r"""A UNC path is refused REGARDLESS of what it points to -- even a genuinely off-box
    \\fileserver\share\tool.exe. A network-share executable can't be confined to (or reasoned
    about against) the local repo root by a string comparison at all, so it fails closed. This
    also documents the (rare, acceptable) behavior change: a policy that deliberately invoked a
    tool off a mapped UNC share would now be refused; the security posture (never run an
    unconfinable network binary against an untrusted checkout) wins over that niche convenience."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()
    unc_tool = "\\\\fileserver\\tools\\ruff.exe"
    monkeypatch.setattr(
        apply_policy.shutil,
        "which",
        lambda cmd, path=None: unc_tool if cmd == "ruff" else None,
    )
    spawn_calls: list[list[str]] = []
    monkeypatch.setattr(
        apply_policy.subprocess,
        "run",
        lambda argv, **kwargs: spawn_calls.append(list(argv)),
    )

    result = apply_policy._run_policy_command("lint", "ruff check .", repo, timeout=10)

    assert result["passed"] is False
    assert not spawn_calls
    assert "UNC/network-share" in str(result["detail"])


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only: exercises the \\\\?\\C:\\ extended-length local spelling that only "
    "resolves on Windows; on POSIX that literal string does not name the real tool.",
)
def test_run_policy_command_allows_local_drive_letter_tool_not_flagged_as_unc(
    tmp_path: Path, monkeypatch
) -> None:
    r"""No regression from the UNC guard: an ordinary local drive-letter tool (C:\... or its
    \\?\C:\... extended-length form, which strips to C:\...) is NOT a UNC path and still runs."""
    from tensor_grep.cli import apply_policy

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()
    trusted = tmp_path / "trusted-bin"
    real_tool = _write_fake_executable(trusted, "ruff", "real-tool")

    # Exercise BOTH the plain and the \\?\-extended local spelling; both must be allowed.
    for which_return in (str(real_tool), "\\\\?\\" + str(real_tool.resolve())):
        monkeypatch.setattr(
            apply_policy.shutil,
            "which",
            lambda cmd, path=None, _r=which_return: _r if cmd == "ruff" else None,
        )
        captured: dict[str, object] = {}

        def _fake_run(argv, _cap=captured, **kwargs):
            _cap["argv"] = list(argv)
            import subprocess as _sp

            return _sp.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(apply_policy.subprocess, "run", _fake_run)

        result = apply_policy._run_policy_command("lint", "ruff check .", repo, timeout=10)

        assert result["passed"] is True, f"local tool spelled {which_return!r} must run"
        assert captured.get("argv") is not None
        assert "UNC" not in str(result["detail"])


def _identity_path_value(target: Path, working_root: Path) -> Path:
    """Pass the target straight through -- the ordinary case."""
    del working_root
    return target


def _dot_slash_embedded_path_value(target: Path, working_root: Path) -> str:
    """Re-spell the SAME absolute path with an embedded ``.`` component (``..././name``)
    rather than the canonical spelling. ``Path.resolve()`` normalizes this away before
    ``_policy_file_arg`` ever computes a relative path, so this proves the ``./`` prefixing
    is idempotent under an input that is already ``./``-shaped -- it must not become
    ``././-again.ini`` (a double prefix) nor lose its prefix entirely."""
    relative = target.relative_to(working_root)
    return f"{working_root}{os.sep}.{os.sep}{relative}"


# Each entry: (relative path under working_root, a factory building the `path_value` argument
# from (target, working_root), whether `_policy_file_arg` must add a `./` prefix). Covers the
# adversarial shape family the audit found missing (2026-08-01 backlog campaign, PR #883 audit
# F1): `-cevil.ini`-style single dash, `--`-leading, exactly `-`, exactly `--`, an `-rf`-shaped
# flag lookalike, a dash-leading name containing a space (quoting-dialect trap), and an
# already-`./`-prefixed input (idempotency) -- plus two CONTROLS that must pass through
# unmodified: an ordinary name, and a name with an INTERIOR (non-leading) dash, which must NOT
# be mistaken for a dash-leading relative path.
_POLICY_FILE_ARG_SHAPES = [
    pytest.param("-cevil.ini", _identity_path_value, True, id="dash-leading-simple"),
    pytest.param("--evil.ini", _identity_path_value, True, id="dash-dash-leading"),
    pytest.param("-", _identity_path_value, True, id="bare-dash"),
    pytest.param("--", _identity_path_value, True, id="double-dash"),
    pytest.param("-rf", _identity_path_value, True, id="dash-rf-flag-lookalike"),
    pytest.param("- evil.ini", _identity_path_value, True, id="dash-leading-with-space"),
    pytest.param("-again.ini", _dot_slash_embedded_path_value, True, id="already-dot-slash-input"),
    pytest.param("normal.ini", _identity_path_value, False, id="control-normal-unchanged"),
    pytest.param("sub/-dash.py", _identity_path_value, False, id="control-interior-dash-unchanged"),
]


@pytest.mark.parametrize("relpath, path_value_factory, expect_prefixed", _POLICY_FILE_ARG_SHAPES)
def test_policy_file_arg_neutralizes_a_dash_leading_relative_path(
    tmp_path: Path,
    relpath: str,
    path_value_factory,
    expect_prefixed: bool,
) -> None:
    """CWE-88, adversarial gate finding (2026-08-01 backlog campaign PR-D, strengthened per the
    PR #883 audit's F1): a repo-controlled file whose relative path starts with ``-`` must not
    come back from ``_policy_file_arg`` as a bare dash-leading string. Substituted into a
    ``$file``/``{file}`` policy command template, a bare ``-cevil.ini`` (or similar) is parsed by
    the downstream tool as ONE OR MORE FLAGS, not a path -- neither ``shlex.quote`` nor
    ``subprocess.list2cmdline`` escape a leading dash (they only quote for
    whitespace/shell-metacharacters), so quoting alone does not neutralize this.

    This asserts TWO independent properties, not one: (1) SHAPE -- the returned token does not
    start with ``-``; and (2) IDENTITY -- the token, resolved against ``working_root``, still
    names the EXACT SAME file that was passed in. Shape alone is satisfiable by a fix that
    returns any non-dash string (including a completely wrong file); identity alone (without the
    parametrized shape family) is satisfiable by a fix that only special-cases the one fixture
    value it was written against. The audit proved both gaps by mutation:

      - ``return f"./{relative}" if relative == "-cevil.ini" else relative`` (handles ONLY the
        exact fixture) still passed the pre-existing single-case test -- killed here by the
        ``--evil.ini``/``-``/``--``/``-rf``/space/already-``./`` parametrizations, each of which
        the mutant leaves dash-leading and unprefixed.
      - ``return "totally_wrong_file.py"`` (a fixed, wrong file) still passed the pre-existing
        shape-only assertion -- killed here by the IDENTITY assertion below, which fails for
        every case (including the controls) because the resolved token never matches the edited
        file.

    Pre-fix baseline (the whole feature reverted): ``_policy_file_arg`` returns the relative path
    completely unmodified, so every ``expect_prefixed=True`` case fails on the shape assertion.
    """
    from tensor_grep.cli.apply_policy import _policy_file_arg

    working_root = tmp_path
    target = working_root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")

    path_value = path_value_factory(target, working_root)
    file_arg = _policy_file_arg(path_value, working_root=working_root)

    assert file_arg is not None
    assert not file_arg.startswith("-"), (
        f"{file_arg!r} starts with '-' and will be parsed as a flag when substituted "
        "into a $file/{file} policy command template"
    )

    # IDENTITY: the returned token, resolved against working_root, must still name the exact
    # file that was passed in -- not a different file, and not a fixed wrong constant.
    resolved_back = (working_root / file_arg).resolve()
    assert resolved_back == target.resolve(), (
        f"{file_arg!r} resolves to {resolved_back}, not the edited file {target.resolve()}"
    )

    expected_relative = target.resolve().relative_to(working_root.resolve()).as_posix()
    if expect_prefixed:
        assert file_arg.startswith("./"), f"{file_arg!r} should carry exactly one './' prefix"
    else:
        # CONTROL: a non-dash-leading relative path must pass through completely UNCHANGED --
        # no prefix added, no rewrite at all.
        assert file_arg == expected_relative, (
            f"control path {expected_relative!r} was rewritten to {file_arg!r}; "
            "only dash-leading relative paths should ever be modified"
        )


def _expand_one_policy_command_instance(
    *,
    placeholder: str,
    target: Path,
    path_value_factory,
    working_root: Path,
) -> str:
    """Shared setup for both the cross-platform and Windows-only end-to-end tests below: build
    the rewrite payload, expand the ``$file``/``{file}`` template, and return the ONE expanded
    command string. Kept in one place so the two tests cannot silently diverge in how they
    construct the thing they tokenize."""
    from tensor_grep.cli.apply_policy import _policy_command_instances

    path_value = path_value_factory(target, working_root)
    rewrite_payload = {"edits": [{"file": str(path_value)}]}
    instances, expansion_error = _policy_command_instances(
        "lint",
        f"ruff check {placeholder}",
        rewrite_payload=rewrite_payload,
        target_path=target,
        working_root=working_root,
    )
    assert expansion_error is None
    assert len(instances) == 1
    _file_arg, expanded_command = instances[0]
    return expanded_command


def _assert_file_token_is_the_edited_file(
    file_token: str, *, tokenizer: str, working_root: Path, target: Path
) -> None:
    """The two properties every tokenizer arm must prove: SHAPE (not flag-looking) and IDENTITY
    (resolves back to the exact edited file, not a different or fixed-wrong one)."""
    assert not file_token.startswith("-"), (
        f"{tokenizer} tokenizer parsed the edited file as {file_token!r}, "
        "which a downstream tool reads as a flag, not a path"
    )
    resolved_back = (working_root / file_token).resolve()
    assert resolved_back == target.resolve(), (
        f"{tokenizer} token {file_token!r} resolves to {resolved_back}, "
        f"not the edited file {target.resolve()}"
    )


@pytest.mark.parametrize("relpath, path_value_factory, expect_prefixed", _POLICY_FILE_ARG_SHAPES)
@pytest.mark.parametrize("placeholder", ["$file", "{file}"])
def test_policy_command_instances_never_produces_a_flag_looking_file_token(
    tmp_path: Path,
    placeholder: str,
    relpath: str,
    path_value_factory,
    expect_prefixed: bool,
) -> None:
    """End-to-end version of the direct test above: the fully-expanded, then argv-split command
    must contain the edited file as an unambiguous PATH token, never a bare flag-shaped string --
    for BOTH template placeholder spellings the policy-command contract supports (``$file`` and
    ``{file}``). Runs on every platform: it exercises the POSIX ``shlex.split`` tokenizer plus
    ``_parse_policy_command`` (the real CLI-boundary parser, which branches internally to
    whichever tokenizer is native to the running OS). It deliberately does NOT call
    ``_split_windows_command`` directly -- that function reaches ``ctypes.windll``, which does
    not exist off Windows, so a prior revision of this test that called it unconditionally
    hard-failed the entire non-Windows CI matrix (AttributeError: module 'ctypes' has no
    attribute 'windll'). See the Windows-only sibling test below for a DIRECT check of that
    tokenizer -- it is the only call site of ``_split_windows_command`` outside
    ``apply_policy.py`` itself, and it is ``skipif``-guarded to never execute off Windows.

    Same shape family and same two properties (shape + identity) as the direct test above -- see
    that docstring for the mutation-kill rationale, which applies identically here since both
    tests exercise ``_policy_file_arg`` on the same inputs, just through a different call path
    (full command expansion + real argv tokenization instead of the bare function).

    Pre-fix baseline (the whole feature reverted): FAILS because ``_policy_command_instances``
    embeds the untouched dash-leading relative path.
    """
    import shlex

    from tensor_grep.cli.apply_policy import _parse_policy_command

    working_root = tmp_path
    target = working_root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")

    expanded_command = _expand_one_policy_command_instance(
        placeholder=placeholder,
        target=target,
        path_value_factory=path_value_factory,
        working_root=working_root,
    )

    posix_argv = shlex.split(expanded_command, posix=True)
    # The real CLI-boundary parser: branches internally to `_split_windows_command` on Windows,
    # POSIX `shlex.split` elsewhere (`apply_policy.py::_parse_policy_command`) -- so on a Windows
    # runner this arm independently exercises the Windows tokenizer too, without this test file
    # ever calling `ctypes.windll` itself off-Windows.
    native_argv = _parse_policy_command(expanded_command)

    for argv, tokenizer in ((posix_argv, "posix shlex"), (native_argv, "native (this OS)")):
        file_token = argv[-1]
        _assert_file_token_is_the_edited_file(
            file_token, tokenizer=tokenizer, working_root=working_root, target=target
        )


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="_split_windows_command calls ctypes.windll, which only exists on Windows",
)
@pytest.mark.parametrize("relpath, path_value_factory, expect_prefixed", _POLICY_FILE_ARG_SHAPES)
@pytest.mark.parametrize("placeholder", ["$file", "{file}"])
def test_policy_command_instances_windows_tokenizer_never_produces_a_flag_looking_file_token(
    tmp_path: Path,
    placeholder: str,
    relpath: str,
    path_value_factory,
    expect_prefixed: bool,
) -> None:
    """Windows-only DIRECT check of ``_split_windows_command`` (``CommandLineToArgvW`` via
    ``ctypes.windll``) -- the sibling of the cross-platform test above, which exercises the same
    tokenizer only indirectly (through ``_parse_policy_command``'s OS-branch) when it happens to
    run on a Windows CI runner. This test calls the Windows tokenizer directly on every platform
    it actually runs on, and is ``skipif``-guarded to never execute (and never import/call
    ``ctypes.windll``) off Windows."""
    del expect_prefixed  # shape/identity are asserted unconditionally below, not branched on it

    from tensor_grep.cli.apply_policy import _split_windows_command

    working_root = tmp_path
    target = working_root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")

    expanded_command = _expand_one_policy_command_instance(
        placeholder=placeholder,
        target=target,
        path_value_factory=path_value_factory,
        working_root=working_root,
    )

    windows_argv = _split_windows_command(expanded_command)
    _assert_file_token_is_the_edited_file(
        windows_argv[-1], tokenizer="windows", working_root=working_root, target=target
    )
