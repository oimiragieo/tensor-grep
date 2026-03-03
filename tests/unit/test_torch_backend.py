import types
from unittest.mock import patch

from tensor_grep.core.config import SearchConfig


class _FakeScalar:
    def __init__(self, value: bool):
        self._value = value

    def item(self):
        return self._value


class _FakeAny:
    def __init__(self, values: list[bool]):
        self._values = values

    def any(self):
        return _FakeScalar(any(self._values))


class _FakeCompare:
    def __init__(self, windows: list[list[int]], pattern: list[int]):
        self._windows = windows
        self._pattern = pattern

    def all(self, dim=1):
        return _FakeAny([window == self._pattern for window in self._windows])


class _FakeWindows:
    def __init__(self, windows: list[list[int]]):
        self._windows = windows

    def __eq__(self, other):
        return _FakeCompare(self._windows, other.data)


class _FakeTensor:
    def __init__(self, data: list[int]):
        self.data = data

    def unfold(self, dim: int, size: int, step: int):
        windows: list[list[int]] = []
        for i in range(0, max(len(self.data) - size + 1, 0), step):
            windows.append(self.data[i : i + size])
        return _FakeWindows(windows)


class _FakeTorch(types.ModuleType):
    uint8 = "uint8"

    def __init__(self):
        super().__init__("torch")
        self.device_calls: list[str] = []

    def device(self, value: str):
        self.device_calls.append(value)
        return value

    def tensor(self, values, dtype=None, device=None):
        return _FakeTensor(list(values))


def test_torch_backend_uses_gpu_literal_matching(tmp_path):
    from tensor_grep.backends.torch_backend import TorchBackend

    path = tmp_path / "torch.log"
    path.write_text("INFO\nERROR timeout\nWARN\n", encoding="utf-8")

    backend = TorchBackend()
    with (
        patch.object(TorchBackend, "is_available", return_value=True),
        patch.dict("sys.modules", {"torch": _FakeTorch()}),
    ):
        result = backend.search(str(path), "ERROR", SearchConfig(fixed_strings=True))

    assert result.total_matches == 1
    assert result.matches[0].line_number == 2


def test_torch_backend_regex_falls_back_to_cpu(tmp_path):
    from tensor_grep.backends.torch_backend import TorchBackend

    path = tmp_path / "torch_regex.log"
    path.write_text("ERROR timeout\n", encoding="utf-8")

    backend = TorchBackend()
    sentinel = object()

    with (
        patch.object(TorchBackend, "is_available", return_value=True),
        patch("tensor_grep.backends.cpu_backend.CPUBackend.search", return_value=sentinel) as cpu,
    ):
        result = backend.search(str(path), r"ERROR.*timeout", SearchConfig(fixed_strings=False))

    assert result is sentinel
    cpu.assert_called_once()


def test_torch_backend_should_distribute_device_selection_when_ids_provided(tmp_path):
    from tensor_grep.backends.torch_backend import TorchBackend

    path = tmp_path / "torch_multi.log"
    path.write_text("ERROR 1\nERROR 2\nERROR 3\nERROR 4\n", encoding="utf-8")

    fake_torch = _FakeTorch()
    backend = TorchBackend(device_ids=[3, 7])
    with (
        patch.object(TorchBackend, "is_available", return_value=True),
        patch.dict("sys.modules", {"torch": fake_torch}),
    ):
        result = backend.search(str(path), "ERROR", SearchConfig(fixed_strings=True))

    assert result.total_matches == 4
    # Pattern tensor + 4 line tensors are mapped across both configured devices.
    assert "cuda:3" in fake_torch.device_calls
    assert "cuda:7" in fake_torch.device_calls
