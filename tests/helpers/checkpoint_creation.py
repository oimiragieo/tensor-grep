"""Pytest marker for tests that need real Python checkpoint creation."""

from __future__ import annotations

import pytest

from tensor_grep.io.checkpoint_directory import require_supported


def _checkpoint_creation_supported() -> bool:
    try:
        require_supported()
    except OSError:
        return False
    return True


def requires_checkpoint_creation(test):
    test = pytest.mark.checkpoint_creation(test)
    return pytest.mark.skipif(
        not _checkpoint_creation_supported(),
        reason="Python checkpoint creation requires Windows CreateDirectory2W support",
    )(test)
