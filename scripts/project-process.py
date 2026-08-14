from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import msvcrt
import os
from pathlib import Path
import subprocess
import time


def _atomic_write(path: Path, value: dict[str, object]) -> None:
    temporary = Path(f"{path}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_ASSIGN_PROCESS = 0x0001
STARTF_USESTDHANDLES = 0x00000100
STD_INPUT_HANDLE = -10


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
    ]


kernel32.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenJobObjectW.restype = wintypes.HANDLE
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
    wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW),
    ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = wintypes.BOOL
kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
kernel32.ResumeThread.restype = wintypes.DWORD
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
kernel32.GetStdHandle.restype = wintypes.HANDLE


def _environment_block(environment: dict[str, str]) -> ctypes.Array[ctypes.c_wchar]:
    return ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(environment.items())) + "\0\0")


def main() -> int:
    parser = argparse.ArgumentParser(description="Start one project process after Job Object attachment")
    parser.add_argument("--request", required=True)
    parser.add_argument("--go", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--done", required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    request_path = Path(args.request).resolve()
    go_path = Path(args.go).resolve()
    result_path = Path(args.result).resolve()
    release_path = Path(args.release).resolve()
    done_path = Path(args.done).resolve()
    state_path = Path(args.state).resolve()

    deadline = time.monotonic() + 20
    while not go_path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Project process was not attached to the Job Object before launch")
        time.sleep(0.05)

    request = json.loads(request_path.read_text(encoding="utf-8-sig"))
    command = [str(request["file_path"]), *[str(item) for item in request.get("arguments", [])]]
    environment = os.environ.copy()
    environment.update({str(key): str(value) for key, value in request.get("environment", {}).items()})
    stdout_handle = None
    stderr_handle = None
    process_info = PROCESS_INFORMATION()
    job = None
    created = False
    try:
        if request.get("stdout"):
            stdout_handle = Path(request["stdout"]).open("ab", buffering=0)
            os.set_handle_inheritable(msvcrt.get_osfhandle(stdout_handle.fileno()), True)
        if request.get("stderr"):
            stderr_handle = Path(request["stderr"]).open("ab", buffering=0)
            os.set_handle_inheritable(msvcrt.get_osfhandle(stderr_handle.fileno()), True)
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        job = kernel32.OpenJobObjectW(JOB_OBJECT_ASSIGN_PROCESS, False, str(state["job_name"]))
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        startup = STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        inherit_handles = bool(stdout_handle or stderr_handle)
        if inherit_handles:
            startup.dwFlags |= STARTF_USESTDHANDLES
            startup.hStdInput = kernel32.GetStdHandle(STD_INPUT_HANDLE)
            startup.hStdOutput = msvcrt.get_osfhandle(stdout_handle.fileno()) if stdout_handle else 0
            startup.hStdError = msvcrt.get_osfhandle(stderr_handle.fileno()) if stderr_handle else 0
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        env_block = _environment_block(environment)
        creationflags = CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT
        creationflags |= CREATE_NO_WINDOW if request.get("hidden", True) else CREATE_NEW_CONSOLE
        if not kernel32.CreateProcessW(
            str(request["file_path"]), command_line, None, None, inherit_handles, creationflags,
            env_block, str(request.get("working_directory")), ctypes.byref(startup), ctypes.byref(process_info),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        created = True
        if not kernel32.AssignProcessToJobObject(job, process_info.hProcess):
            raise ctypes.WinError(ctypes.get_last_error())
        _atomic_write(result_path, {"pid": int(process_info.dwProcessId)})
        deadline = time.monotonic() + 30
        while not release_path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Project process bootstrap was not released")
            time.sleep(0.05)
        if kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        _atomic_write(result_path, {"pid": int(process_info.dwProcessId), "resumed": True})
        deadline = time.monotonic() + 30
        while not done_path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Project process bootstrap completion was not acknowledged")
            time.sleep(0.05)
        return 0
    except Exception:
        if created:
            kernel32.TerminateProcess(process_info.hProcess, 1)
        raise
    finally:
        if process_info.hThread:
            kernel32.CloseHandle(process_info.hThread)
        if process_info.hProcess:
            kernel32.CloseHandle(process_info.hProcess)
        if job:
            kernel32.CloseHandle(job)
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
