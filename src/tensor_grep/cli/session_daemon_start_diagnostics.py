"""Bounded diagnostics for a session daemon child that failed to become ready."""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.session_daemon_trust import diagnose_user_secret

DAEMON_SECRET_REASON_DETAILS = {
    "secret_untrusted_present": "the per-user daemon secret exists but is not trusted",
    "secret_extra_links": "the per-user daemon secret has unexpected hard links",
    "secret_dir_unavailable": "the per-user state directory is unavailable",
    "secret_dir_untrusted_owner_or_acl": (
        "the per-user state directory grants write access to another account; "
        "tg refuses to create the daemon secret there"
    ),
    "secret_ancestor_untrusted": "an ancestor of the per-user state directory is not trusted",
    "secret_io_error": "the per-user daemon secret could not be checked due to an I/O error",
    "secret_unavailable": (
        "the per-user daemon secret could not be loaded or created, for example because "
        "another tg process held its lock"
    ),
}


def daemon_start_reason_detail(reason_code: str, secret_dir: Path) -> str:
    reason = DAEMON_SECRET_REASON_DETAILS.get(reason_code, "the daemon secret could not be used")
    return f"{reason} (directory: {secret_dir})"


class DaemonStartError(RuntimeError):
    def __init__(self, root: Path, detail: str, reason_code: str) -> None:
        self.detail = detail
        self.reason_code = reason_code
        super().__init__(f"session daemon did not start for {root}: {detail}")


def daemon_start_failure(root: Path, captured: bytes) -> DaemonStartError:
    raw = captured[:4096]
    stderr = raw.decode("utf-8", errors="replace")
    detail = next((line.strip() for line in reversed(stderr.splitlines()) if line.strip()), "")
    secret_reason = diagnose_user_secret()
    child_reason = next(
        (code for code in DAEMON_SECRET_REASON_DETAILS if f"({code})" in detail),
        None,
    )
    if not detail:
        detail = "daemon startup timed out"
    reason_code = secret_reason or child_reason or "daemon_start_failed"
    if secret_reason:
        detail = f"{detail} [{secret_reason}]"
    return DaemonStartError(root, detail, reason_code)
