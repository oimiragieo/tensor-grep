"""Historical entry point for release-assets validation unit tests.

The suite was split (W4-g) into themed siblings under ``tests/unit/``:

- ``test_release_assets_validation_docs_and_version_locks.py``
- ``test_release_assets_validation_security_and_audit.py``
- ``test_release_assets_validation_ci_release_gates.py``
- ``test_release_assets_validation_package_managers.py``
- ``test_release_assets_validation_release_workflow_jobs.py``
- ``test_release_assets_validation_publish_and_proof.py``

Shared helpers live in ``test_release_assets_validation_shared.py``.

This module re-exports those helpers for importers that still load
``test_release_assets_validation.py`` by path. It deliberately defines no ``test_*``
functions so pytest does not double-collect.

Note: ``from module import *`` skips ``_``-prefixed names, so this file
loads the shared module by path and copies its public namespace explicitly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SHARED_PATH = Path(__file__).with_name("test_release_assets_validation_shared.py")
_SPEC = importlib.util.spec_from_file_location(
    "_tg_release_assets_validation_shared_shim", _SHARED_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SHARED = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SHARED)

globals().update({key: value for key, value in vars(_SHARED).items() if not key.startswith("__")})
