import json
import subprocess
import sys
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
        "torch": True,
        "torch_path": str(site / "torch" / "__init__.py"),
        "torch_auxiliaries": {},
    }


@pytest.mark.parametrize(
    ("installed", "expected"),
    [
        (True, "tensor-grep[gpu-win,nlp,ast,semantic,rerank,scan]==0.33.0"),
        (False, "tensor-grep==0.33.0"),
    ],
    ids=["optional-features", "torch-absent"],
)
def test_CandidatePackageSpec_PreservesDetectedOptionalFeatures(monkeypatch, installed, expected):
    monkeypatch.setattr(util, "find_spec", lambda _name: object() if installed else None)
    monkeypatch.setattr(
        metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(metadata.PackageNotFoundError()),
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "C:/tools/ast-grep.exe" if installed and name == "ast-grep" else None,
    )

    package_spec = candidate._managed_candidate_package_spec("tensor-grep==0.33.0")

    assert package_spec == expected


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


@pytest.mark.parametrize("torch_present", [False, True], ids=["missing", "present"])
def test_CandidateVerification_WhenCpuTorchProfileDiffersFromCandidate(
    monkeypatch, tmp_path, torch_present
):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    evidence = _candidate_evidence(python, cuda=None)
    evidence["torch"] = torch_present
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(evidence), ""
        ),
    )

    if torch_present:
        candidate._verify_managed_candidate_python(python, "0.33.0", expected_cuda="cpu")
    else:
        with pytest.raises(RuntimeError, match="CPU torch profile mismatch"):
            candidate._verify_managed_candidate_python(python, "0.33.0", expected_cuda="cpu")


@pytest.mark.parametrize("auxiliary", [False, True], ids=["torch", "torchvision"])
def test_CandidateVerification_WhenTorchOriginEscapesEnvironment_RejectsCandidate(
    monkeypatch, tmp_path, auxiliary
):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    evidence = _candidate_evidence(python)
    expected_auxiliaries = ("torchvision",) if auxiliary else ()
    if auxiliary:
        evidence["torch_auxiliaries"] = {
            "torchvision": str(tmp_path / "attacker" / "torchvision.py")
        }
    else:
        evidence["torch_path"] = str(tmp_path / "attacker" / "torch.py")
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(evidence), ""
        ),
    )

    with pytest.raises(RuntimeError, match="origin is outside"):
        candidate._verify_managed_candidate_python(
            python,
            "0.33.0",
            expected_cuda="12.8",
            expected_torch_auxiliaries=expected_auxiliaries,
        )


def test_CudaProfile_WhenTorchImportExceedsBound_RefusesWithTimeout(monkeypatch):
    def run(command, **kwargs):
        assert kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(candidate._self.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="CUDA profile could not be verified") as caught:
        candidate._managed_cuda_profile("python.exe")

    assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)


def test_CandidateVerification_WhenTorchImportExceedsBound_RejectsCandidate(monkeypatch, tmp_path):
    def run(command, **kwargs):
        assert kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(candidate._self.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="candidate Python verification failed") as caught:
        candidate._verify_managed_candidate_python(tmp_path / "python.exe", "0.33.0")

    assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)


def test_ManagedVersionProbe_WhenTorchAuxiliaryImportBreaks_RejectsWithValidControl(tmp_path):
    runner = r"""
import importlib.abc as abc
import importlib.machinery as machinery
import importlib.metadata as m
import importlib.util
import json
import pathlib, sys, types
root = pathlib.Path(sys.argv[1])
broken = sys.argv[2] == 'broken'
modules = ('tensor_grep', 'tensor_grep.cli', 'tensor_grep.cli.main',
           'tensor_grep.cli.native_frontdoor', 'tensor_grep.rust_core')
for name in modules:
    module = types.ModuleType(name)
    module.__file__ = str(root / (name.replace('.', '/') + '.py'))
    module.__path__ = []
    sys.modules[name] = module
    if '.' in name:
        parent, child = name.rsplit('.', 1)
        setattr(sys.modules[parent], child, module)
torch = types.ModuleType('torch')
torch.__file__ = str(root / 'site/torch.py')
torch.__spec__ = machinery.ModuleSpec('torch', None)
torch.version = types.SimpleNamespace(cuda=None)
sys.modules['torch'] = torch
class Loader(abc.MetaPathFinder, abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name == 'torchvision':
            return machinery.ModuleSpec(name, self, origin=str(root / 'site/torchvision.py'))
        return None
    def exec_module(self, module):
        if broken:
            raise ImportError('ABI mismatch')
sys.meta_path = [Loader()]
m.version = lambda _: '0.33.0'
m.distribution = lambda _: types.SimpleNamespace(locate_file=lambda _: root / 'site')
exec(sys.argv[3])
"""
    probe = candidate._MANAGED_VERSION_PROBE
    valid, broken = [
        subprocess.run(
            [sys.executable, "-c", runner, str(tmp_path), mode, probe],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for mode in ("valid", "broken")
    ]

    assert valid.returncode == 0 and json.loads(valid.stdout)["torch_auxiliaries"]
    assert broken.returncode != 0 and "ABI mismatch" in broken.stderr


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
    monkeypatch.setattr(util, "find_spec", lambda name: object() if name == "torch" else None)
    monkeypatch.setattr(util, "find_spec", lambda name: object() if name == "torch" else None)
    monkeypatch.setattr(candidate, "_managed_cuda_profile", lambda _executable: "12.7")
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("unsupported CUDA must refuse before install"),
    )

    with pytest.raises(RuntimeError, match=r"only the installer-pinned CUDA 12\.8 profile"):
        candidate._run_managed_candidate_install(
            tmp_path / "python.exe", "tensor-grep[gpu-win]", source_cuda="12.7"
        )


def test_CudaProfile_WhenTorchHasNoCudaBuild_ReturnsNone(monkeypatch):
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "\n", ""),
    )

    assert candidate._managed_cuda_profile("python.exe") is None


def test_CudaProfile_WhenProbeFails_RaisesVerificationError(monkeypatch):
    def fail_probe(*_args, **_kwargs):
        raise OSError("CUDA probe unavailable")

    monkeypatch.setattr(candidate._self.subprocess, "run", fail_probe)

    with pytest.raises(
        RuntimeError, match="CUDA profile could not be verified: CUDA probe unavailable"
    ) as caught:
        candidate._managed_cuda_profile("python.exe")

    assert isinstance(caught.value.__cause__, OSError)


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

    install_method = candidate._run_managed_candidate_install(
        python, "tensor-grep==0.33.0", source_cuda=None
    )

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

    install_method = candidate._run_managed_candidate_install(
        python, "tensor-grep[gpu-win]", source_cuda="12.8"
    )

    assert install_method == "uv"
    assert commands[0] == [
        "uv",
        "pip",
        "install",
        "--python",
        str(python),
        "torch",
        "--index-url",
        "https://download.pytorch.org/whl/cu128",
    ]
    assert commands[1][-1] == "tensor-grep[gpu-win]"


@pytest.mark.parametrize(
    ("auxiliaries", "expected_packages"),
    [((), ["torch"]), (("torchvision",), ["torch", "torchvision"])],
    ids=["torch-only", "keeps-installed-auxiliary"],
)
def test_CandidateInstall_WhenCpuTorchProfileMatches_PreservesExistingPackages(
    monkeypatch, tmp_path, auxiliaries, expected_packages
):
    python = tmp_path / "python.exe"
    commands = []
    monkeypatch.setattr(
        candidate, "_managed_candidate_package_spec", lambda _spec: "tensor-grep[gpu-win]"
    )
    monkeypatch.setattr(
        candidate._self.subprocess,
        "run",
        lambda command, **_kwargs: (
            commands.append(command),
            subprocess.CompletedProcess(command, 0, "", ""),
        )[1],
    )

    candidate._run_managed_candidate_install(
        python,
        "tensor-grep[gpu-win]",
        source_cuda=None,
        source_torch_auxiliaries=auxiliaries,
    )

    assert len(commands) == 2
    assert commands[0][5 : 5 + len(expected_packages)] == expected_packages
    assert commands[0][-2:] == ["--index-url", "https://download.pytorch.org/whl/cpu"]
    assert commands[1][-1] == "tensor-grep[gpu-win]"


def test_CpuTorchInstall_WhenUvUnavailable_UsesCandidatePip(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        if command[0] == "uv":
            raise FileNotFoundError("uv unavailable")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(candidate._self.subprocess, "run", run)

    candidate._install_managed_torch(
        python, "https://download.pytorch.org/whl/cpu", (), pip_fallback=True
    )

    assert commands[1] == [
        str(python),
        "-m",
        "pip",
        "install",
        "torch",
        "--index-url",
        "https://download.pytorch.org/whl/cpu",
    ]


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

    install_method = candidate._run_managed_candidate_install(
        python, "tensor-grep==0.33.0", source_cuda=None
    )

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
        candidate._run_managed_candidate_install(
            tmp_path / "python.exe", "tensor-grep==0.33.0", source_cuda=None
        )
