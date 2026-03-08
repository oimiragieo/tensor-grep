import types
from typing import ClassVar
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
        self.tensor_device_calls: list[str | None] = []

    def device(self, value: str):
        self.device_calls.append(value)
        return value

    def tensor(self, values, dtype=None, device=None):
        self.tensor_device_calls.append(device)
        return _FakeTensor(list(values))


class _FakeFuture:
    def __init__(self, result):
        self._result = result

    def result(self):
        return self._result


class _FakeExecutor:
    submitted_devices: ClassVar[list[str]] = []

    def __init__(self, *args, **kwargs):
        _ = (args, kwargs)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False

    def submit(self, fn, **kwargs):
        _FakeExecutor.submitted_devices.append(str(kwargs["device"]))
        return _FakeFuture(fn(**kwargs))


def test_torch_backend_uses_gpu_literal_matching(tmp_path):
    from tensor_grep.backends.torch_backend import TorchBackend

    path = tmp_path / "torch.log"
    path.write_text("INFO\nERROR timeout\nWARN\n", encoding="utf-8")

    backend = TorchBackend(device_ids=[0])
    with (
        patch.object(TorchBackend, "is_available", return_value=True),
        patch.dict("sys.modules", {"torch": _FakeTorch()}),
    ):
        result = backend.search(str(path), "ERROR", SearchConfig(fixed_strings=True))

    assert result.total_matches == 1
    assert result.matches[0].line_number == 2
    assert result.routing_backend == "TorchBackend"
    assert result.routing_reason == "torch_single_gpu"
    assert result.routing_gpu_device_ids == [0]
    assert result.routing_gpu_chunk_plan_mb == []
    assert result.routing_distributed is False
    assert result.routing_worker_count == 1


def test_torch_backend_regex_falls_back_to_cpu(tmp_path):
    from tensor_grep.backends.torch_backend import TorchBackend

    path = tmp_path / "torch_regex.log"
    path.write_text("ERROR timeout\n", encoding="utf-8")

    backend = TorchBackend()
    with patch.object(TorchBackend, "is_available", return_value=True):
        result = backend.search(str(path), r"ERROR.*timeout", SearchConfig(fixed_strings=False))

    assert result.total_matches == 1
    assert result.routing_backend == "CPUBackend"
    assert result.routing_reason == "torch_regex_cpu_fallback"
    assert result.routing_gpu_device_ids == []
    assert result.routing_gpu_chunk_plan_mb == []
    assert result.routing_distributed is False
    assert result.routing_worker_count == 1


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
    # Last four tensor allocations correspond to per-device shard execution.
    assert fake_torch.tensor_device_calls[-4:] == ["cuda:3", "cuda:3", "cuda:7", "cuda:7"]


def test_torch_backend_should_fanout_work_to_executor_when_multi_gpu(tmp_path):
    from tensor_grep.backends.torch_backend import TorchBackend

    path = tmp_path / "torch_fanout.log"
    path.write_text("ERROR A\nERROR B\nERROR C\nERROR D\n", encoding="utf-8")

    fake_torch = _FakeTorch()
    _FakeExecutor.submitted_devices = []
    backend = TorchBackend(device_ids=[3, 7])
    with (
        patch.object(TorchBackend, "is_available", return_value=True),
        patch.dict("sys.modules", {"torch": fake_torch}),
        patch("tensor_grep.backends.torch_backend.ThreadPoolExecutor", _FakeExecutor),
    ):
        result = backend.search(str(path), "ERROR", SearchConfig(fixed_strings=True))

    assert result.total_matches == 4
    assert _FakeExecutor.submitted_devices == ["cuda:3", "cuda:7"]
    assert result.routing_backend == "TorchBackend"
    assert result.routing_reason == "torch_multi_gpu_fanout"
    assert result.routing_gpu_device_ids == [3, 7]
    assert result.routing_gpu_chunk_plan_mb == []
    assert result.routing_distributed is True
    assert result.routing_worker_count == 2


def test_torch_backend_should_prefer_enumerate_device_ids_when_available(tmp_path):
    from tensor_grep.backends.torch_backend import TorchBackend

    path = tmp_path / "torch_enumerate.log"
    path.write_text("ERROR A\nERROR B\n", encoding="utf-8")

    fake_torch = _FakeTorch()
    backend = TorchBackend(device_ids=None)

    class _DetectorWithStableApi:
        def enumerate_device_ids(self):
            return [7, 3]

        def get_device_ids(self):
            raise AssertionError(
                "get_device_ids should not be called when enumerate_device_ids exists"
            )

    backend.device_detector = _DetectorWithStableApi()
    with (
        patch.object(TorchBackend, "is_available", return_value=True),
        patch.dict("sys.modules", {"torch": fake_torch}),
    ):
        result = backend.search(str(path), "ERROR", SearchConfig(fixed_strings=True))

    assert result.total_matches == 2
    assert "cuda:7" in fake_torch.device_calls
    assert "cuda:3" in fake_torch.device_calls


def test_torch_backend_should_weight_multi_gpu_shards_by_chunk_plan(tmp_path):
    from tensor_grep.backends.torch_backend import TorchBackend

    path = tmp_path / "torch_weighted.log"
    path.write_text("ERROR 1\nERROR 2\nERROR 3\nERROR 4\n", encoding="utf-8")

    fake_torch = _FakeTorch()
    backend = TorchBackend(device_ids=[3, 7], chunk_sizes_mb=[3, 1])
    with (
        patch.object(TorchBackend, "is_available", return_value=True),
        patch.dict("sys.modules", {"torch": fake_torch}),
        patch("tensor_grep.backends.torch_backend.ThreadPoolExecutor", _FakeExecutor),
    ):
        result = backend.search(str(path), "ERROR", SearchConfig(fixed_strings=True))

    assert result.total_matches == 4
    # Weighted shards (3:1) route 3 lines to cuda:3 and 1 line to cuda:7.
    assert fake_torch.tensor_device_calls[-4:] == ["cuda:3", "cuda:3", "cuda:3", "cuda:7"]
    assert result.routing_backend == "TorchBackend"
    assert result.routing_reason == "torch_multi_gpu_fanout"
    assert result.routing_gpu_device_ids == [3, 7]
    assert result.routing_gpu_chunk_plan_mb == [(3, 3), (7, 1)]
    assert result.routing_distributed is True
    assert result.routing_worker_count == 2


def test_torch_backend_should_deduplicate_duplicate_device_ids_before_fanout(tmp_path):
    from tensor_grep.backends.torch_backend import TorchBackend

    path = tmp_path / "torch_duplicate_device_ids.log"
    path.write_text("ERROR A\nERROR B\nERROR C\n", encoding="utf-8")

    fake_torch = _FakeTorch()
    _FakeExecutor.submitted_devices = []
    backend = TorchBackend(device_ids=[3, 3], chunk_sizes_mb=[1, 4])
    with (
        patch.object(TorchBackend, "is_available", return_value=True),
        patch.dict("sys.modules", {"torch": fake_torch}),
        patch("tensor_grep.backends.torch_backend.ThreadPoolExecutor", _FakeExecutor),
    ):
        result = backend.search(str(path), "ERROR", SearchConfig(fixed_strings=True))

    assert result.total_matches == 3
    assert _FakeExecutor.submitted_devices == []
    assert result.routing_reason == "torch_single_gpu"
    assert result.routing_gpu_device_ids == [3]
    assert result.routing_gpu_chunk_plan_mb == [(3, 4)]
    assert result.routing_distributed is False
    assert result.routing_worker_count == 1


def test_torch_backend_is_available_should_skip_detector_probe_when_device_ids_provided():
    from tensor_grep.backends.torch_backend import TorchBackend

    class _DetectorProbeGuard:
        def get_device_count(self):
            raise AssertionError("detector probe should not run when explicit device ids are set")

    fake_torch = _FakeTorch()
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: True)

    backend = TorchBackend(device_ids=[7, 3])
    backend.device_detector = _DetectorProbeGuard()

    with (
        patch("importlib.util.find_spec", return_value=object()),
        patch.dict("sys.modules", {"torch": fake_torch}),
    ):
        assert backend.is_available() is True


def test_torch_backend_is_available_should_prefer_enumerated_device_ids():
    from tensor_grep.backends.torch_backend import TorchBackend

    class _DetectorWithStableIds:
        def enumerate_device_ids(self):
            return [7, 3]

        def get_device_count(self):
            raise AssertionError(
                "get_device_count should not be used when enumerate_device_ids is available"
            )

    fake_torch = _FakeTorch()
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: True)

    backend = TorchBackend(device_ids=None)
    backend.device_detector = _DetectorWithStableIds()

    with (
        patch("importlib.util.find_spec", return_value=object()),
        patch.dict("sys.modules", {"torch": fake_torch}),
    ):
        assert backend.is_available() is True


def test_torch_backend_is_available_should_return_false_when_enumerated_ids_empty():
    from tensor_grep.backends.torch_backend import TorchBackend

    class _DetectorWithNoRoutableIds:
        def enumerate_device_ids(self):
            return []

        def get_device_count(self):
            raise AssertionError(
                "count probing should not run when enumerate_device_ids resolves the route set"
            )

    fake_torch = _FakeTorch()
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: True)

    backend = TorchBackend(device_ids=None)
    backend.device_detector = _DetectorWithNoRoutableIds()

    with (
        patch("importlib.util.find_spec", return_value=object()),
        patch.dict("sys.modules", {"torch": fake_torch}),
    ):
        assert backend.is_available() is False


def test_torch_backend_is_available_should_fallback_to_get_device_ids_when_enumeration_raises():
    from tensor_grep.backends.torch_backend import TorchBackend

    class _DetectorWithEnumerationFailure:
        def enumerate_device_ids(self):
            raise RuntimeError("enumeration failed")

        def get_device_ids(self):
            return [5, 2]

        def get_device_count(self):
            raise AssertionError(
                "raw count probing should not run when get_device_ids can recover the route set"
            )

    fake_torch = _FakeTorch()
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: True)

    backend = TorchBackend(device_ids=None)
    backend.device_detector = _DetectorWithEnumerationFailure()

    with (
        patch("importlib.util.find_spec", return_value=object()),
        patch.dict("sys.modules", {"torch": fake_torch}),
    ):
        assert backend.is_available() is True


def test_torch_backend_is_available_should_return_false_without_concrete_device_ids():
    from tensor_grep.backends.torch_backend import TorchBackend

    class _CountOnlyDetector:
        def get_device_count(self):
            return 2

    fake_torch = _FakeTorch()
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: True)

    backend = TorchBackend(device_ids=None)
    backend.device_detector = _CountOnlyDetector()

    with (
        patch("importlib.util.find_spec", return_value=object()),
        patch.dict("sys.modules", {"torch": fake_torch}),
    ):
        assert backend.is_available() is False


def test_torch_backend_search_should_fail_without_concrete_device_ids(tmp_path):
    from tensor_grep.backends.torch_backend import TorchBackend

    path = tmp_path / "torch_no_ids.log"
    path.write_text("ERROR A\n", encoding="utf-8")

    class _CountOnlyDetector:
        def get_device_count(self):
            return 2

    fake_torch = _FakeTorch()
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: True)

    backend = TorchBackend(device_ids=None)
    backend.device_detector = _CountOnlyDetector()

    with (
        patch.object(TorchBackend, "is_available", return_value=True),
        patch.dict("sys.modules", {"torch": fake_torch}),
    ):
        try:
            backend.search(str(path), "ERROR", SearchConfig(fixed_strings=True))
        except RuntimeError as exc:
            assert "concrete CUDA device IDs" in str(exc)
        else:
            raise AssertionError(
                "search should fail when no concrete CUDA device IDs are available"
            )


def test_torch_backend_search_should_fallback_to_get_device_ids_when_enumeration_raises(tmp_path):
    from tensor_grep.backends.torch_backend import TorchBackend

    path = tmp_path / "torch_enumeration_fallback.log"
    path.write_text("ERROR A\nERROR B\n", encoding="utf-8")

    class _DetectorWithEnumerationFailure:
        def enumerate_device_ids(self):
            raise RuntimeError("enumeration failed")

        def get_device_ids(self):
            return [5, 2]

    fake_torch = _FakeTorch()
    backend = TorchBackend(device_ids=None)
    backend.device_detector = _DetectorWithEnumerationFailure()

    with (
        patch.object(TorchBackend, "is_available", return_value=True),
        patch.dict("sys.modules", {"torch": fake_torch}),
    ):
        result = backend.search(str(path), "ERROR", SearchConfig(fixed_strings=True))

    assert result.total_matches == 2
    assert result.routing_gpu_device_ids == [5, 2]
    assert "cuda:5" in fake_torch.device_calls
    assert "cuda:2" in fake_torch.device_calls
