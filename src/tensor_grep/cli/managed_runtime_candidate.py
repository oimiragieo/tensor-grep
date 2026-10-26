"""Windows managed-runtime package candidate discovery and verification."""

from __future__ import annotations

import json
import sys
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


def _run_managed_candidate_install(candidate_python: Path, package_spec: str) -> str:
    package_spec = _managed_candidate_package_spec(package_spec)
    if "[" in package_spec and "gpu-win" in package_spec:
        cuda = _managed_cuda_profile(sys.executable)
        if cuda != "12.8":
            raise RuntimeError(
                "managed CUDA upgrade refused: only the installer-pinned CUDA 12.8 profile "
                "can be reproduced safely"
            )
        _self.subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(candidate_python),
                "torch",
                "torchvision",
                "torchaudio",
                "--index-url",
                "https://download.pytorch.org/whl/cu128",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    attempts = [
        (
            "uv",
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(candidate_python),
                "--upgrade-package",
                "tensor-grep",
                package_spec,
            ],
        ),
        (
            "pip",
            [
                str(candidate_python),
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                package_spec,
            ],
        ),
    ]
    errors: list[str] = []
    for label, command in attempts:
        try:
            _self.subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
            return label
        except FileNotFoundError as exc:
            errors.append(f"{label}: {exc}")
        except _self.subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            errors.append(f"{label}: {detail}")
    raise RuntimeError("managed candidate package install failed: " + "; ".join(errors))


def _managed_cuda_profile(executable: str) -> str | None:
    try:
        result = _self.subprocess.run(
            [str(executable), "-I", "-c", "import torch; print(torch.version.cuda or '')"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=15,
        )
    except Exception as exc:
        raise RuntimeError(f"managed CUDA profile could not be verified: {exc}") from exc
    lines = (result.stdout or "").strip().splitlines()
    return lines[-1] if lines else None


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
