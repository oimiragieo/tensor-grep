#!/usr/bin/env python3
"""Behaviorless Round-60 verifier stub for Task 2A Windows/native nodes.

Consumes primitive ArtifactSource paths and delegates to
tensor_grep.cli.native_ci_receipt.verify_native_ci_receipt, which must
independently derive live tuple/JUnit/Rust/digests (never caller claims).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from tensor_grep.cli.native_ci_receipt import (  # noqa: E402
    ArtifactSource,
    load_receipt,
    verify_native_ci_receipt,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--current-run-dir", type=Path, required=True)
    parser.add_argument("--junit", type=Path, default=None)
    parser.add_argument("--rust-list", type=Path, default=None)
    parser.add_argument("--expected-attribution", default="source-tree")
    args = parser.parse_args(argv)

    source = ArtifactSource(
        current_run_dir=args.current_run_dir,
        manifest_path=args.manifest,
        junit_path=args.junit,
        rust_list_path=args.rust_list,
        environ=dict(os.environ),
        expected_attribution=args.expected_attribution,
    )
    if not args.receipt.is_file():
        verdict = {
            "ok": False,
            "reason": "receipt_missing",
            "note": (
                "verify path exercised; real clearance still requires a live "
                "Windows CI run with emitted NativeCiReceipt artifacts"
            ),
        }
        print(json.dumps(verdict, sort_keys=True))
        return 2
    try:
        receipt = load_receipt(args.receipt)
    except (OSError, ValueError, NotImplementedError) as exc:
        verdict = {"ok": False, "reason": "receipt_unreadable", "detail": str(exc)}
        print(json.dumps(verdict, sort_keys=True))
        return 2
    verdict = verify_native_ci_receipt(receipt, artifact_source=source)
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
