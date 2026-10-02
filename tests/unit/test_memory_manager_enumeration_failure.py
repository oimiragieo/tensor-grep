"""Fail-closed contract for MemoryManager when device-ID enumeration RAISES.

A detector whose enumeration raised at runtime tells us nothing about which IDs are real, so a
contiguous ``range(get_device_count())`` is a guess. An explicit ``get_device_ids([1])`` must not
validate against a guess (real IDs may be non-contiguous, e.g. [3, 5]); it must return [] so the
explicit-GPU pipeline raises a configuration error. A LEGACY detector that merely LACKS the ID APIs
keeps the contiguous-fallback compatibility path -- decided by capability, never by exception type:
an AttributeError raised from inside a method that exists is a real failure and also fails closed.
"""

from __future__ import annotations

from tensor_grep.core.hardware.memory_manager import MemoryManager


class _RaisingEnumerationDetector:
    def has_gpu(self) -> bool:
        return True

    def enumerate_device_ids(self) -> list[int]:
        raise RuntimeError("NVML enumeration blew up")

    def get_device_count(self) -> int:
        return 2


class _LegacyDetector:
    """No enumerate_device_ids / get_device_ids / list_devices at all."""

    def has_gpu(self) -> bool:
        return True

    def get_device_count(self) -> int:
        return 2


class _NonContiguousDetector:
    def has_gpu(self) -> bool:
        return True

    def enumerate_device_ids(self) -> list[int]:
        return [3, 5]

    def get_device_count(self) -> int:
        return 2


def _manager_with(detector: object) -> MemoryManager:
    manager = MemoryManager()
    manager.detector = detector  # type: ignore[assignment]
    return manager


def test_explicit_ids_fail_closed_when_enumeration_raised() -> None:
    manager = _manager_with(_RaisingEnumerationDetector())
    assert manager.get_device_ids([1]) == []


def test_no_preferred_ids_keeps_contiguous_fallback_when_enumeration_raised() -> None:
    manager = _manager_with(_RaisingEnumerationDetector())
    assert manager.get_device_ids() == [0, 1]


def test_explicit_ids_fail_closed_survives_cached_fallback() -> None:
    manager = _manager_with(_RaisingEnumerationDetector())
    assert manager.get_device_ids() == [0, 1]  # populates the cache from the fallback
    assert manager.get_device_ids([1]) == []  # cached path must still refuse explicit IDs


def test_legacy_detector_without_id_apis_keeps_contiguous_compat() -> None:
    manager = _manager_with(_LegacyDetector())
    assert manager.get_device_ids([1]) == [1]


def test_successful_noncontiguous_enumeration_control() -> None:
    manager = _manager_with(_NonContiguousDetector())
    assert manager.get_device_ids([1]) == []
    assert manager.get_device_ids([5]) == [5]


class _AttributeErrorInsideEnumerationDetector:
    """The method EXISTS but its body fails with an AttributeError (a bug, not a legacy detector)."""

    def has_gpu(self) -> bool:
        return True

    def enumerate_device_ids(self) -> list[int]:
        raise AttributeError("'NoneType' object has no attribute 'nvmlDeviceGetCount'")

    def get_device_count(self) -> int:
        return 2


def test_attribute_error_inside_a_present_enumeration_method_still_fails_closed() -> None:
    # An AttributeError raised from WITHIN enumerate_device_ids is a runtime failure, not proof
    # the detector lacks the API. Telling the two apart by exception type would fabricate IDs here.
    manager = _manager_with(_AttributeErrorInsideEnumerationDetector())
    assert manager.get_device_ids([1]) == []
    assert manager.get_device_ids() == [0, 1]
