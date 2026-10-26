"""Windows managed-runtime package candidate discovery and verification."""

from __future__ import annotations

import json
from pathlib import Path

from tensor_grep.cli._main_binding import _self

_MANAGED_VERSION_PROBE = (
    "import importlib.metadata as m, importlib.util as u, json, pathlib, tensor_grep; "
    "import tensor_grep.cli.main, tensor_grep.cli.native_frontdoor; "
    "import tensor_grep.rust_core as r; d=m.distribution('tensor-grep'); "
    "cuda=None; "
    "exec('import torch; cuda=torch.version.cuda' if u.find_spec('torch') else 'pass'); "
    "print(json.dumps({'version':m.version('tensor-grep'),"
    "'module':str(pathlib.Path(tensor_grep.__file__).resolve()),"
    "'dist':str(pathlib.Path(d.locate_file('')).resolve()),"
    "'extension':str(pathlib.Path(r.__file__).resolve()),'cuda':cuda}))"
)


def _managed_candidate_package_spec(package_spec: str) -> str:
    """Preserve optional features detected in the currently running managed environment."""
    import shutil
    from importlib import metadata
    from importlib.util import find_spec

    extras: list[str] = []
    for extra, modules in (
        ("gpu-win", ("torch",)),
        ("nlp", ("transformers", "tritonclient")),
        ("ast", ("tree_sitter",)),
        ("semantic", ("model2vec",)),
        ("rerank", ("onnxruntime",)),
    ):
        for module in modules:
            try:
                present = find_spec(module) is not None
            except (ImportError, ValueError):
                present = False
            if present:
                extras.append(extra)
                break
    try:
        metadata.version("ast-grep-cli")
    except metadata.PackageNotFoundError:
        scan_present = any(shutil.which(name) for name in ("ast-grep", "ast-grep.exe"))
    else:
        scan_present = True
    if scan_present:
        extras.append("scan")
    if not extras or "[" in package_spec:
        return package_spec
    return package_spec.replace("tensor-grep", f"tensor-grep[{','.join(extras)}]", 1)


def _verify_managed_candidate_python(
    candidate_python: Path, expected_version: str, *, expected_cuda: str | None = None
) -> None:
    try:
        result = _self.subprocess.run(
            [str(candidate_python), "-I", "-c", _MANAGED_VERSION_PROBE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        )
    except Exception as exc:
        raise RuntimeError(f"candidate Python verification failed: {exc}") from exc
    try:
        evidence = json.loads((result.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("candidate Python verification returned invalid evidence") from exc
    expected_root = candidate_python.parent.parent.resolve()
    if not isinstance(evidence, dict) or evidence.get("version") != expected_version:
        actual = evidence.get("version") if isinstance(evidence, dict) else "no version reported"
        raise RuntimeError(
            f"candidate Python version mismatch: expected {expected_version}, got {actual}"
        )
    for key in ("module", "dist", "extension"):
        value = evidence.get(key)
        if not isinstance(value, str) or not Path(value).resolve().is_relative_to(expected_root):
            raise RuntimeError(f"candidate Python {key} origin is outside the staged environment")
    if expected_cuda is not None and evidence.get("cuda") != expected_cuda:
        raise RuntimeError(
            f"candidate Python CUDA profile mismatch: expected {expected_cuda}, "
            f"got {evidence.get('cuda') or 'no CUDA build'}"
        )
