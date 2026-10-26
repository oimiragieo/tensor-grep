import json
import subprocess
from importlib import metadata, util

import pytest

from tensor_grep.cli import managed_runtime_candidate as candidate


def _failed_probe(command, **_kwargs):
    raise subprocess.CalledProcessError(1, command, stderr="probe failed")


def _failed_install(command, detail):
    raise subprocess.CalledProcessError(2, command, stderr=detail)


def _candidate_evidence(python, *, version="0.33.0", cuda="12.8", extension=None):
    site = python.parent.parent / "Lib" / "site-packages"
    return {
        "version": version,
        "module": str(site / "tensor_grep" / "__init__.py"),
        "dist": str(site),
        "extension": str(extension or site / "tensor_grep" / "rust_core.pyd"),
        "cuda": cuda,
    }


def test_CandidatePackageSpec_WhenOptionalFeaturesAreInstalled_PreservesAllExtras(
    monkeypatch,
):
    monkeypatch.setattr(util, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(metadata.PackageNotFoundError()),
    )
    monkeypatch.setattr(
        "shutil.which", lambda name: "C:/tools/ast-grep.exe" if name == "ast-grep" else None
    )

    package_spec = candidate._managed_candidate_package_spec("tensor-grep==0.33.0")

    assert package_spec == "tensor-grep[gpu-win,nlp,ast,semantic,rerank,scan]==0.33.0"


def test_CandidateVerification_WhenOriginsAreInsideEnvironment_AcceptsPinnedVersion(
    monkeypatch, tmp_path
):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    evidence = _candidate_evidence(python)
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(evidence), ""
        ),
    )

    candidate._verify_managed_candidate_python(python, "0.33.0", expected_cuda="12.8")


def test_CandidateVerification_WhenExtensionOriginEscapesEnvironment_RejectsCandidate(
    monkeypatch, tmp_path
):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    site = python.parent.parent / "Lib" / "site-packages"
    evidence = _candidate_evidence(
        python, cuda=None, extension=site / ".." / ".." / ".." / "attacker" / "rust_core.pyd"
    )
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(evidence), ""
        ),
    )

    with pytest.raises(RuntimeError, match="extension origin is outside"):
        candidate._verify_managed_candidate_python(python, "0.33.0")


def test_CandidateVerification_WhenVersionDiffersFromRequestedVersion_RejectsCandidate(
    monkeypatch, tmp_path
):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    evidence = _candidate_evidence(python, version="0.32.9")
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(evidence), ""
        ),
    )

    with pytest.raises(RuntimeError, match="version mismatch"):
        candidate._verify_managed_candidate_python(python, "0.33.0")


def test_CandidateVerification_WhenCudaProfileDiffersFromPinnedProfile_RejectsCandidate(
    monkeypatch, tmp_path
):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    evidence = _candidate_evidence(python, cuda="12.7")
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(evidence), ""
        ),
    )

    with pytest.raises(RuntimeError, match="CUDA profile mismatch"):
        candidate._verify_managed_candidate_python(python, "0.33.0", expected_cuda="12.8")


def test_CandidateVerification_WhenProbeReturnsMalformedJson_RejectsCandidate(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "{bad", ""),
    )

    with pytest.raises(RuntimeError, match="invalid evidence"):
        candidate._verify_managed_candidate_python(tmp_path / "python.exe", "0.33.0")


def test_CandidateVerification_WhenProbeProcessFails_ReportsVerificationFailure(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        _failed_probe,
    )

    with pytest.raises(RuntimeError, match="candidate Python verification failed"):
        candidate._verify_managed_candidate_python(tmp_path / "python.exe", "0.33.0")


def test_CandidateInstall_WhenRequestedCudaProfileCannotBeRecreated_RefusesBeforeInstall(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        candidate, "_managed_candidate_package_spec", lambda _spec: "tensor-grep[gpu-win]"
    )
    monkeypatch.setattr(candidate, "_managed_cuda_profile", lambda _executable: "12.7")
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("unsupported CUDA must refuse before install"),
    )

    with pytest.raises(RuntimeError, match=r"only the installer-pinned CUDA 12\.8 profile"):
        candidate._run_managed_candidate_install(tmp_path / "python.exe", "tensor-grep[gpu-win]")


def test_CudaProfile_WhenTorchHasNoCudaBuild_ReturnsNone(monkeypatch):
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "\n", ""),
    )

    assert candidate._managed_cuda_profile("python.exe") is None


def test_CandidateInstall_WhenUvSucceeds_UsesPinnedCandidatePythonAndPackageSpec(
    monkeypatch, tmp_path
):
    python = tmp_path / "python.exe"
    commands = []
    monkeypatch.setattr(candidate, "_managed_candidate_package_spec", lambda spec: spec)
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: (
            commands.append(command),
            subprocess.CompletedProcess(command, 0, "", ""),
        )[1],
    )

    install_method = candidate._run_managed_candidate_install(python, "tensor-grep==0.33.0")

    assert install_method == "uv"
    assert commands == [
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--upgrade-package",
            "tensor-grep",
            "tensor-grep==0.33.0",
        ]
    ]


def test_CandidateInstall_WhenCudaProfileMatches_InstallsPinnedCudaAndPackage(
    monkeypatch, tmp_path
):
    python = tmp_path / "python.exe"
    commands = []
    monkeypatch.setattr(
        candidate, "_managed_candidate_package_spec", lambda _spec: "tensor-grep[gpu-win]"
    )
    monkeypatch.setattr(candidate, "_managed_cuda_profile", lambda _executable: "12.8")
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: (
            commands.append(command),
            subprocess.CompletedProcess(command, 0, "", ""),
        )[1],
    )

    install_method = candidate._run_managed_candidate_install(python, "tensor-grep[gpu-win]")

    assert install_method == "uv"
    assert commands[0] == [
        "uv",
        "pip",
        "install",
        "--python",
        str(python),
        "torch",
        "torchvision",
        "torchaudio",
        "--index-url",
        "https://download.pytorch.org/whl/cu128",
    ]
    assert commands[1][-1] == "tensor-grep[gpu-win]"


def test_CandidateInstall_WhenUvInstallFails_UsesPipWithBoundedPackageArguments(
    monkeypatch, tmp_path
):
    python = tmp_path / "python.exe"
    commands = []
    outcomes = iter([
        lambda command: _failed_install(command, "uv failed"),
        lambda command: subprocess.CompletedProcess(command, 0, "", ""),
    ])
    monkeypatch.setattr(candidate, "_managed_candidate_package_spec", lambda spec: spec)
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: (commands.append(command), next(outcomes)(command))[1],
    )

    install_method = candidate._run_managed_candidate_install(python, "tensor-grep==0.33.0")

    assert install_method == "pip"
    assert commands[0][:2] == ["uv", "pip"]
    assert commands[1] == [
        str(python),
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "tensor-grep==0.33.0",
    ]


def test_CandidateInstall_WhenUvAndPipBothFail_ReportsBothErrors(monkeypatch, tmp_path):
    outcomes = iter([
        lambda command: _failed_install(command, "uv failed"),
        lambda command: _failed_install(command, "pip failed"),
    ])
    monkeypatch.setattr(candidate, "_managed_candidate_package_spec", lambda spec: spec)
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: next(outcomes)(command),
    )

    with pytest.raises(RuntimeError, match="uv: uv failed; pip: pip failed"):
        candidate._run_managed_candidate_install(tmp_path / "python.exe", "tensor-grep==0.33.0")
