"""Windows process identity checks used by deferred managed-runtime activation."""

import os
import re
import sys
from pathlib import Path
from typing import Any


def _canonical_windows_path(path: str | Path) -> str:
    resolved = os.path.realpath(os.fspath(path))
    # Rust's canonical paths include the prefix absent from OS process-image queries.
    if resolved.startswith("\\\\?\\UNC\\"):
        resolved = "\\\\" + resolved[8:]
    elif resolved.startswith("\\\\?\\"):
        resolved = resolved[4:]
    return os.path.normcase(resolved)


def _native_parent_matches_stable(
    stable_path: Path, actual_image: str, advertised_image: str | None
) -> bool:
    actual_matches = _canonical_windows_path(actual_image) == _canonical_windows_path(stable_path)
    advertised_matches = advertised_image is None or _canonical_windows_path(
        advertised_image
    ) == _canonical_windows_path(actual_image)
    if advertised_image is not None and not advertised_matches:
        raise RuntimeError("native launcher environment does not match the actual parent image")
    return actual_matches


def _native_image_is_managed_generation(stable_path: Path, actual_image: str) -> bool:
    try:
        relative = Path(_canonical_windows_path(actual_image)).relative_to(
            Path(_canonical_windows_path(stable_path.parent.parent / "runtimes"))
        )
    except ValueError:
        return False
    return (
        len(relative.parts) == 3
        and re.fullmatch(r"[0-9a-f]{32}", relative.parts[0]) is not None
        and relative.parts[1:] == ("bin", "tg.exe")
    )


def _windows_process_parent_pid(kernel: Any, process_id: int) -> int:
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise RuntimeError("cannot snapshot Windows process ancestry for deferred activation")
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32ProcessID == process_id:
                return int(entry.th32ParentProcessID)
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    raise RuntimeError("managed generation was absent from the Windows process snapshot")


def _windows_parent_pid_for_handle(kernel: Any, process_id: int, handle: int) -> int:
    """Bind a Toolhelp ancestry lookup to the process object held by ``handle``."""
    from ctypes import wintypes

    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    if kernel.WaitForSingleObject(handle, 0) != 0x00000102:
        raise RuntimeError("launcher process exited during ancestry verification")
    parent_pid = _windows_process_parent_pid(kernel, process_id)
    if kernel.WaitForSingleObject(handle, 0) != 0x00000102:
        raise RuntimeError("launcher process exited during ancestry verification")
    return parent_pid


def _windows_process_creation_time(kernel: Any, handle: int) -> int:
    import ctypes
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    creation, exit_time, kernel_time, user_time = (FileTime() for _ in range(4))
    if not kernel.GetProcessTimes(
        handle,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        raise RuntimeError("cannot verify Windows launcher process creation times")
    return int((creation.high << 32) | creation.low)


def _open_native_parent_handle(stable_path: Path) -> int | None:
    """Return an inheritable wait handle for the stable tg.exe above this Python process."""
    import ctypes
    from ctypes import wintypes

    if not sys.platform.startswith("win"):
        return None
    from tensor_grep.cli import session_daemon_winsec as winsec

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    direct_parent_pid = os.getppid()
    direct_parent = kernel.OpenProcess(0x00100000 | 0x1000, False, direct_parent_pid)
    if not direct_parent:
        raise RuntimeError("cannot pin the running native launcher process for deferred activation")

    intermediate_handle = None
    stable_handle = None
    try:
        current_created = _windows_process_creation_time(kernel, kernel.GetCurrentProcess())
        direct_parent_created = _windows_process_creation_time(kernel, direct_parent)
        if direct_parent_created >= current_created:
            raise RuntimeError("native launcher PID was reused before Python startup")
        kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        image = ctypes.create_unicode_buffer(32768)
        image_size = wintypes.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(direct_parent, 0, image, ctypes.byref(image_size)):
            raise RuntimeError("cannot verify the running native launcher's image")
        advertised = os.environ.get("TG_NATIVE_TG_BINARY")
        sidecar = os.environ.get("TG_SIDECAR_PYTHON")

        if (
            sidecar is not None
            and _canonical_windows_path(sidecar) == _canonical_windows_path(image.value)
        ) or Path(image.value).name.casefold() == "uv.exe":
            intermediate_created = direct_parent_created
            direct_parent_pid = _windows_parent_pid_for_handle(
                kernel, direct_parent_pid, direct_parent
            )
            intermediate_handle = kernel.OpenProcess(0x00100000 | 0x1000, False, direct_parent_pid)
            if not intermediate_handle:
                raise RuntimeError("cannot pin the native launcher above the Python launcher")
            if _windows_process_creation_time(kernel, intermediate_handle) >= intermediate_created:
                raise RuntimeError("native launcher PID was reused before Python startup")
            winsec.close_handle(direct_parent)
            direct_parent = intermediate_handle
            intermediate_handle = None
            image_size = wintypes.DWORD(len(image))
            if not kernel.QueryFullProcessImageNameW(
                direct_parent, 0, image, ctypes.byref(image_size)
            ):
                raise RuntimeError("cannot verify the native launcher above the Python launcher")

        def pin_stable_ancestor(
            launcher_pid: int, launcher_handle: int, generation_created: int
        ) -> None:
            nonlocal stable_handle

            stable_pid = _windows_parent_pid_for_handle(kernel, launcher_pid, launcher_handle)
            stable_handle = kernel.OpenProcess(0x00100000 | 0x1000, False, stable_pid)
            if not stable_handle:
                raise RuntimeError(
                    "cannot pin the stable launcher ancestor for deferred activation"
                )
            stable_image = ctypes.create_unicode_buffer(32768)
            stable_image_size = wintypes.DWORD(len(stable_image))
            if not kernel.QueryFullProcessImageNameW(
                stable_handle, 0, stable_image, ctypes.byref(stable_image_size)
            ) or not _native_parent_matches_stable(stable_path, stable_image.value, None):
                raise RuntimeError("managed generation parent is not the stable launcher")
            # The snapshot records a PID, so reject a newer process that reused it.
            if _windows_process_creation_time(kernel, stable_handle) >= generation_created:
                raise RuntimeError(
                    "stable launcher PID was reused after managed generation startup"
                )

        if _native_image_is_managed_generation(stable_path, image.value):
            if advertised is not None and _canonical_windows_path(advertised) not in {
                _canonical_windows_path(image.value),
                _canonical_windows_path(stable_path),
            }:
                raise RuntimeError(
                    "native launcher environment does not match the actual parent image"
                )
            generation_created = _windows_process_creation_time(kernel, direct_parent)
            pin_stable_ancestor(direct_parent_pid, direct_parent, generation_created)
        elif _native_parent_matches_stable(stable_path, image.value, advertised):
            stable_handle = direct_parent
            direct_parent = None
        else:
            return None

        if stable_handle is None:
            raise RuntimeError("cannot pin the native launcher process for deferred activation")
        kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        kernel.SetHandleInformation.restype = wintypes.BOOL
        if not kernel.SetHandleInformation(stable_handle, 1, 1):
            raise RuntimeError(
                "cannot pass the pinned native launcher handle to the activation helper"
            )
        result = int(stable_handle)
        stable_handle = None
        return result
    finally:
        if stable_handle is not None:
            winsec.close_handle(stable_handle)
        if intermediate_handle is not None:
            winsec.close_handle(intermediate_handle)
        if direct_parent is not None:
            winsec.close_handle(direct_parent)
