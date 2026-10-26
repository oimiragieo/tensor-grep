import ctypes
import os
from ctypes import wintypes

import pytest

from tensor_grep.cli import native_frontdoor
from tensor_grep.cli import session_daemon_winsec as winsec

pytestmark = pytest.mark.skipif(os.name != "nt", reason="activation events are Windows-only")


def test_ActivationReadyEvent_WhenCreatedAndSignalled_ExposesInheritedSignal():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetHandleInformation.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    handle = native_frontdoor._create_activation_ready_event()

    try:
        flags = wintypes.DWORD()
        assert kernel.GetHandleInformation(handle, ctypes.byref(flags))
        assert flags.value & 1
        assert kernel.WaitForSingleObject(handle, 0) == 258

        native_frontdoor._signal_activation_ready(handle)

        assert kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        winsec.close_handle(handle)
