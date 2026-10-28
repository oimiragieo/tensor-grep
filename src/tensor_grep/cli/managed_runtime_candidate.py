"""Windows managed-runtime package candidate discovery and verification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tensor_grep.cli._main_binding import _self

_MANAGED_VERSION_PROBE = (
    "import importlib.metadata as m, importlib.util as u, json, pathlib, tensor_grep; "
    "import tensor_grep.cli.main, tensor_grep.cli.native_frontdoor; "
    "import tensor_grep.rust_core as r; d=m.distribution('tensor-grep'); "
    "cuda=None; torch_path=None; torch_present=u.find_spec('torch') is not None; "
    "torch_auxiliaries={name:(__import__(name),str(pathlib.Path(spec.origin).resolve()))[1] for name in "
    "('torchvision','torchaudio') if (spec:=u.find_spec(name)) is not None and spec.origin}; "
    "exec('import torch; cuda=torch.version.cuda; torch_path=str(pathlib.Path(torch.__file__).resolve())' "
    "if torch_present else 'pass'); "
    "print(json.dumps({'version':m.version('tensor-grep'),"
    "'module':str(pathlib.Path(tensor_grep.__file__).resolve()),"
    "'dist':str(pathlib.Path(d.locate_file('')).resolve()),"
    "'extension':str(pathlib.Path(r.__file__).resolve()),'cuda':cuda,'torch':torch_present,"
    "'torch_path':torch_path,'torch_auxiliaries':torch_auxiliaries}))"
)

_MANAGED_PROFILE_PROBE = (
    "import importlib.metadata as m, importlib.util as u, json, pathlib, shutil; "
    "d=m.distribution('tensor-grep'); module=u.find_spec('tensor_grep'); "
    "ts=u.find_spec('torch'); cuda=None; torch_path=None; "
    "torch_present=ts is not None; "
    "exec('import torch; cuda=torch.version.cuda; torch_path=str(pathlib.Path(torch.__file__).resolve())' "
    "if torch_present else 'pass'); "
    "aux={name:str(pathlib.Path(spec.origin).resolve()) for name in ('torchvision','torchaudio') "
    "if (spec:=u.find_spec(name)) is not None and spec.origin}; "
    "features=[]; "
    "[(features.append(extra)) for extra,mods in "
    "(('gpu-win',('torch',)),('nlp',('transformers','tritonclient')),"
    "('ast',('tree_sitter',)),('semantic',('model2vec',)),('rerank',('onnxruntime',))) "
    "if any(u.find_spec(mod) is not None for mod in mods)]; "
    "scan=any((dist.metadata.get('Name') or '').casefold()=='ast-grep-cli' for dist in m.distributions()) "
    "or any(shutil.which(name) for name in ('ast-grep','ast-grep.exe')); "
    "features.extend(['scan'] if scan else []); "
    "print(json.dumps({'version':m.version('tensor-grep'),"
    "'module':str(pathlib.Path(module.origin).resolve()) if module and module.origin else None,"
    "'dist':str(pathlib.Path(d.locate_file('')).resolve()),"
    "'torch':torch_present,'cuda':cuda,"
    "'torch_path':torch_path,'torch_auxiliaries':aux,'extras':features}))"
)


@dataclass(frozen=True)
class ManagedCandidateProfile:
    version: str
    extras: tuple[str, ...]
    cuda: str | None
    torch_auxiliaries: tuple[str, ...]


def _windows_profile_directory() -> Path:
    """Resolve the Windows profile through its Known Folder API."""
    import ctypes
    from ctypes import wintypes

    windll = ctypes.windll  # type: ignore[attr-defined]

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    folder = GUID(
        0x5E6C858F,
        0x0E22,
        0x4760,
        (ctypes.c_ubyte * 8)(0x9A, 0xFE, 0xEA, 0x33, 0x17, 0xB6, 0x71, 0x73),
    )
    result = ctypes.c_wchar_p()
    if (
        windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(result))
        != 0
        or not result.value
    ):
        raise RuntimeError("managed Windows profile authority could not be resolved")
    try:
        return Path(result.value)
    finally:
        windll.ole32.CoTaskMemFree(result)


def _managed_candidate_package_spec(
    package_spec: str, *, detected_extras: tuple[str, ...] = ()
) -> str:
    """Add optional extras discovered from the managed source interpreter."""
    extras = list(detected_extras)
    if not extras or "[" in package_spec:
        return package_spec
    return package_spec.replace("tensor-grep", f"tensor-grep[{','.join(extras)}]", 1)


def _managed_candidate_profile(executable: Path | str) -> ManagedCandidateProfile:
    """Read the installed managed interpreter's package and torch profile in isolation."""
    try:
        result = _self.subprocess.run(
            [str(executable), "-I", "-c", _MANAGED_PROFILE_PROBE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=60,
        )
    except Exception as exc:
        raise RuntimeError(f"managed source Python profile could not be verified: {exc}") from exc
    try:
        evidence = json.loads((result.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("managed source Python profile returned invalid evidence") from exc
    if not isinstance(evidence, dict):
        raise RuntimeError("managed source Python profile returned invalid evidence")
    expected_root = Path(executable).parent.parent.resolve()
    for key in ("module", "dist"):
        value = evidence.get(key)
        if not isinstance(value, str) or not Path(value).resolve().is_relative_to(expected_root):
            raise RuntimeError(f"managed source Python {key} origin is outside its environment")
    version = evidence.get("version")
    extras = evidence.get("extras")
    cuda = evidence.get("cuda")
    if (
        not isinstance(version, str)
        or not isinstance(extras, list)
        or any(not isinstance(extra, str) for extra in extras)
        or len(extras) != len(set(extras))
        or not (cuda is None or isinstance(cuda, str))
    ):
        raise RuntimeError("managed source Python profile returned invalid evidence")
    if evidence.get("torch") is True:
        torch_path = evidence.get("torch_path")
        if not isinstance(torch_path, str) or not Path(torch_path).resolve().is_relative_to(
            expected_root
        ):
            raise RuntimeError("managed source Python torch origin is outside its environment")
    elif evidence.get("torch") is not False or cuda is not None:
        raise RuntimeError("managed source Python torch profile is invalid")
    auxiliaries = evidence.get("torch_auxiliaries")
    if (
        not isinstance(auxiliaries, dict)
        or set(auxiliaries) - {"torchvision", "torchaudio"}
        or any(
            not isinstance(name, str)
            or not isinstance(path, str)
            or not Path(path).resolve().is_relative_to(expected_root)
            for name, path in auxiliaries.items()
        )
        or set(extras) - {"gpu-win", "nlp", "ast", "semantic", "rerank", "scan"}
        or type(evidence.get("torch")) is not bool
        or (evidence.get("torch") is True and "gpu-win" not in extras)
        or (evidence.get("torch") is False and cuda is not None)
    ):
        raise RuntimeError("managed source Python torch auxiliary profile is invalid")
    return ManagedCandidateProfile(version, tuple(extras), cuda, tuple(sorted(auxiliaries)))


def _run_managed_candidate_install(
    candidate_python: Path,
    package_spec: str,
    *,
    source_cuda: str | None,
    source_torch_auxiliaries: tuple[str, ...] = (),
    source_extras: tuple[str, ...] | None = None,
) -> str:
    package_spec = (
        _managed_candidate_package_spec(package_spec)
        if source_extras is None
        else _managed_candidate_package_spec(package_spec, detected_extras=source_extras)
    )
    if "[" in package_spec and "gpu-win" in package_spec:
        if source_cuda not in (None, "12.8"):
            raise RuntimeError(
                "managed CUDA upgrade refused: only the installer-pinned CUDA 12.8 profile "
                "can be reproduced safely"
            )
        if source_cuda == "12.8":
            _install_managed_torch(
                candidate_python,
                "https://download.pytorch.org/whl/cu128",
                source_torch_auxiliaries,
            )
        elif source_cuda is None:
            _install_managed_torch(
                candidate_python,
                "https://download.pytorch.org/whl/cpu",
                source_torch_auxiliaries,
                pip_fallback=True,
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
                "--link-mode",
                "copy",
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


def _install_managed_torch(
    candidate_python: Path,
    index_url: str,
    auxiliaries: tuple[str, ...],
    *,
    pip_fallback: bool = False,
) -> None:
    packages = ["torch", *auxiliaries]
    uv = [
        "uv",
        "pip",
        "install",
        "--python",
        str(candidate_python),
        *packages,
        "--link-mode",
        "copy",
        "--index-url",
        index_url,
    ]
    pip = [str(candidate_python), "-m", "pip", "install", *packages, "--index-url", index_url]
    if not pip_fallback:
        _self.subprocess.run(
            uv, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
        )
        return
    errors: list[str] = []
    for label, command in (("uv", uv), ("pip", pip)):
        try:
            _self.subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
            return
        except FileNotFoundError as exc:
            errors.append(f"{label}: {exc}")
        except _self.subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            errors.append(f"{label}: {detail}")
    raise RuntimeError("managed CPU torch install failed: " + "; ".join(errors))


def _managed_cuda_profile(executable: str) -> str | None:
    try:
        result = _self.subprocess.run(
            [str(executable), "-I", "-c", "import torch; print(torch.version.cuda or '')"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=60,
        )
    except Exception as exc:
        raise RuntimeError(f"managed CUDA profile could not be verified: {exc}") from exc
    lines = (result.stdout or "").strip().splitlines()
    return lines[-1] if lines else None


def _managed_torch_auxiliaries() -> tuple[str, ...]:
    from importlib.util import find_spec

    return tuple(
        module for module in ("torchvision", "torchaudio") if find_spec(module) is not None
    )


def _verify_managed_candidate_python(
    candidate_python: Path,
    expected_version: str,
    *,
    expected_cuda: str | None = None,
    expected_torch_auxiliaries: tuple[str, ...] = (),
) -> None:
    try:
        result = _self.subprocess.run(
            [str(candidate_python), "-I", "-c", _MANAGED_VERSION_PROBE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=60,
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
    if expected_cuda is not None and evidence.get("torch") is not True:
        profile = "CPU" if expected_cuda == "cpu" else expected_cuda
        raise RuntimeError(f"candidate Python {profile} torch profile mismatch: torch is missing")
    if expected_cuda is not None:
        torch_path = evidence.get("torch_path")
        if not isinstance(torch_path, str) or not Path(torch_path).resolve().is_relative_to(
            expected_root
        ):
            raise RuntimeError("candidate Python torch origin is outside the staged environment")
        auxiliary_origins = evidence.get("torch_auxiliaries")
        if not isinstance(auxiliary_origins, dict) or set(auxiliary_origins) != set(
            expected_torch_auxiliaries
        ):
            raise RuntimeError("candidate Python torch auxiliary profile mismatch")
        if any(
            not isinstance(path, str) or not Path(path).resolve().is_relative_to(expected_root)
            for path in auxiliary_origins.values()
        ):
            raise RuntimeError(
                "candidate Python torch auxiliary origin is outside the staged environment"
            )
    if expected_cuda == "cpu" and evidence.get("cuda") is not None:
        raise RuntimeError("candidate Python CPU torch profile mismatch: CUDA build found")
    if (
        expected_cuda is not None
        and expected_cuda != "cpu"
        and evidence.get("cuda") != expected_cuda
    ):
        raise RuntimeError(
            f"candidate Python CUDA profile mismatch: expected {expected_cuda}, "
            f"got {evidence.get('cuda') or 'no CUDA build'}"
        )
