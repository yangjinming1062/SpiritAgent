import ctypes
import logging
import threading
from typing import Any

from .constants import IS_WINDOWS

logger = logging.getLogger(__name__)

_runner_job_handle: Any = None
_job_lock = threading.Lock()

if IS_WINDOWS:
    from ctypes import wintypes

    # use_last_error=True 配合 ctypes.get_last_error() 是可靠的错误通道；裸 windll + GetLastError() FFI 调用可能被中间 ctypes 逻辑重置 LastError。
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]

    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]

    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryLimit", ctypes.c_size_t),
            ("PeakJobMemoryLimit", ctypes.c_size_t),
        ]

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9


def init_runner_job_object() -> bool:
    """初始化 Runner 进程树的 Windows Job Object，确保子进程/孙进程随 Runner 退出被强制回收。POSIX 上为空操作。"""
    global _runner_job_handle
    if not IS_WINDOWS:
        return True

    with _job_lock:
        if _runner_job_handle is not None and _runner_job_handle != 0:
            return True

        try:
            h_job = kernel32.CreateJobObjectW(None, None)
            if not h_job or h_job == wintypes.HANDLE(-1).value or h_job == -1:
                logger.warning("CreateJobObjectW failed with error code %d", ctypes.get_last_error())
                return False

            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            set_res = kernel32.SetInformationJobObject(
                h_job,
                _JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not set_res:
                logger.warning("SetInformationJobObject failed with error code %d", ctypes.get_last_error())
                kernel32.CloseHandle(h_job)
                return False

            cur_proc = kernel32.GetCurrentProcess()
            assign_res = kernel32.AssignProcessToJobObject(h_job, cur_proc)
            if not assign_res:
                logger.warning(
                    "AssignProcessToJobObject(GetCurrentProcess()) failed with error code %d",
                    ctypes.get_last_error(),
                )
                kernel32.CloseHandle(h_job)
                return False

            _runner_job_handle = h_job
            logger.info(
                "Windows Job Object initialized with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (process tree bound to Runner)",
            )
            return True
        except Exception as exc:
            logger.warning("Failed to initialize Windows Job Object: %s", exc)
            return False
