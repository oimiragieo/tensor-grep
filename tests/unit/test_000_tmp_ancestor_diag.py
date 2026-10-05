"""TEMPORARY CI diagnostic (to be deleted): dump the real ancestor layout on the runner."""

import os
import stat
import sys
from pathlib import Path

from tensor_grep.cli import session_daemon_trust as trust


def test_000_dump_ancestor_layout() -> None:
    target = Path.home() / ".local" / "state" / "tensor-grep"
    lines = [
        f"DIAG platform={sys.platform} home={Path.home()} euid={getattr(os, 'geteuid', lambda: -1)()}"
    ]
    lines.append(f"DIAG refusal={trust._ancestors_refusal(target)!r}")
    for anc in (target, *target.parents):
        try:
            st = os.lstat(anc)
            lines.append(
                f"DIAG {anc} uid={st.st_uid} mode={stat.filemode(st.st_mode)} {st.st_mode & 0o7777:o}"
            )
        except OSError as exc:
            lines.append(f"DIAG {anc} MISSING {exc}")
    if sys.platform == "win32":
        user = trust._win_current_user_sid()
        lines.append(f"DIAG user={user} token_owner={trust._win_token_owner_sid()}")
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    raise AssertionError("\n".join(lines))  # surfaces in the failed-test log
