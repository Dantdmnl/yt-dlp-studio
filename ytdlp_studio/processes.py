"""Hidden subprocesses and bounded process-tree cancellation."""

import os
import signal
import subprocess


def hide_child_windows() -> None:
    """Hide helpers in GUI/background hosts, including dependency shell calls."""
    if os.name != "nt" or getattr(subprocess.Popen, "_studio_hidden", False):
        return
    import inspect
    original = subprocess.Popen._execute_child
    signature = inspect.signature(original)

    def execute(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs)
        info = bound.arguments.get("startupinfo") or subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
        flags = bound.arguments.get("creationflags", 0)
        bound.arguments["creationflags"] = (flags & ~(subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS)) | subprocess.CREATE_NO_WINDOW
        bound.arguments["startupinfo"] = info
        return original(*bound.args, **bound.kwargs)

    # Existing asyncio/yt-dlp subclasses inherit this too, even if imported earlier.
    subprocess.Popen._execute_child = execute
    def system(command):
        if isinstance(command, (str, bytes)) and not command.strip():
            return 0
        # os.system bypasses Popen; route Windows shell calls through the same guard.
        return subprocess.call(os.fsdecode(command), shell=True)

    os.system = system
    subprocess.Popen._studio_hidden = True


def own_process_tree(process):
    """Windows Job Objects retain ownership even when an intermediate parent exits."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                    ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class Limits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", ctypes.c_uint64 * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        return None
    limits = Limits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    try:
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
            kernel.CloseHandle(handle)
            return None
    except (AttributeError, TypeError, ValueError):
        kernel.CloseHandle(handle)
        return None

    class OwnedTree:
        def __init__(self):
            self.handle = handle

        def close(self):
            if self.handle:
                kernel.CloseHandle(self.handle)
                self.handle = None

    return OwnedTree()


def window_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def terminate_tree(process: subprocess.Popen, tree=None) -> None:
    if tree is not None:
        tree.close()
        process.wait(timeout=5)
        return
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
