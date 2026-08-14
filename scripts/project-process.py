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
JOB_OBJECT_QUERY = 0x0004
STARTF_USESTDHANDLES = 0x00000100
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002


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


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


kernel32.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenJobObjectW.restype = wintypes.HANDLE
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
kernel32.IsProcessInJob.restype = wintypes.BOOL
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
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
kernel32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
kernel32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]


def _valid_record(record: object) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("name"), str) and bool(record["name"].strip())
        and isinstance(record.get("pid"), int)
        and isinstance(record.get("creation_filetime"), int)
        and isinstance(record.get("executable"), str) and bool(record["executable"])
    )


def _load_session_state(path: Path) -> dict[str, object]:
    state = json.loads(path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(state, dict) or state.get("schema") != 1
        or not isinstance(state.get("session_id"), str) or not state["session_id"]
        or not isinstance(state.get("job_name"), str) or not state["job_name"]
        or not _valid_record(state.get("supervisor"))
        or not isinstance(state.get("owners"), list)
        or not isinstance(state.get("components"), list)
        or any(not _valid_record(item) for item in state["owners"])
        or any(not _valid_record(item) for item in state["components"])
    ):
        raise ValueError("Project session state is invalid")
    return state


def _open_job(state_path: Path) -> int:
    state = _load_session_state(state_path)
    job = kernel32.OpenJobObjectW(JOB_OBJECT_ASSIGN_PROCESS | JOB_OBJECT_QUERY, False, str(state["job_name"]))
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    return job


def _ensure_in_job(job: int, process: int) -> None:
    assigned = wintypes.BOOL()
    if not kernel32.IsProcessInJob(process, job, ctypes.byref(assigned)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not assigned.value and not kernel32.AssignProcessToJobObject(job, process):
        raise ctypes.WinError(ctypes.get_last_error())
    assigned = wintypes.BOOL()
    if not kernel32.IsProcessInJob(process, job, ctypes.byref(assigned)) or not assigned.value:
        raise RuntimeError("Process did not enter the exact project Job Object")


def _environment_block(environment: dict[str, str]) -> ctypes.Array[ctypes.c_wchar]:
    return ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(environment.items())) + "\0\0")


def _working_directory(request: dict[str, object]) -> str | None:
    value = request.get("working_directory")
    return str(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Start one project process after Job Object attachment")
    parser.add_argument("--request", required=True)
    parser.add_argument("--ready", required=True)
    parser.add_argument("--go", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    request_path = Path(args.request).resolve()
    ready_path = Path(args.ready).resolve()
    go_path = Path(args.go).resolve()
    result_path = Path(args.result).resolve()
    release_path = Path(args.release).resolve()
    state_path = Path(args.state).resolve()

    job = _open_job(state_path)
    try:
        _ensure_in_job(job, kernel32.GetCurrentProcess())
    finally:
        kernel32.CloseHandle(job)
        job = None
    _atomic_write(ready_path, {"pid": os.getpid()})

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
    stdin_handle = None
    attribute_buffer = None
    attribute_list = None
    process_info = PROCESS_INFORMATION()
    created = False
    resumed = False
    try:
        if request.get("stdout"):
            stdout_handle = Path(request["stdout"]).open("ab", buffering=0)
            os.set_handle_inheritable(msvcrt.get_osfhandle(stdout_handle.fileno()), True)
        if request.get("stderr"):
            stderr_handle = Path(request["stderr"]).open("ab", buffering=0)
            os.set_handle_inheritable(msvcrt.get_osfhandle(stderr_handle.fileno()), True)
        startup = STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        inherit_handles = bool(stdout_handle or stderr_handle)
        if inherit_handles:
            stdin_handle = open(os.devnull, "rb", buffering=0)
            if stdout_handle is None:
                stdout_handle = open(os.devnull, "ab", buffering=0)
            if stderr_handle is None:
                stderr_handle = open(os.devnull, "ab", buffering=0)
            handles = [
                msvcrt.get_osfhandle(stdin_handle.fileno()),
                msvcrt.get_osfhandle(stdout_handle.fileno()),
                msvcrt.get_osfhandle(stderr_handle.fileno()),
            ]
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            startup.StartupInfo.dwFlags |= STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = handles
            attribute_size = ctypes.c_size_t()
            kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attribute_size))
            attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
            attribute_list = ctypes.cast(attribute_buffer, ctypes.c_void_p)
            if not kernel32.InitializeProcThreadAttributeList(attribute_list, 1, 0, ctypes.byref(attribute_size)):
                raise ctypes.WinError(ctypes.get_last_error())
            startup.lpAttributeList = attribute_list
            handle_array = (wintypes.HANDLE * len(handles))(*handles)
            if not kernel32.UpdateProcThreadAttribute(
                attribute_list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                ctypes.cast(handle_array, ctypes.c_void_p), ctypes.sizeof(handle_array), None, None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        env_block = _environment_block(environment)
        creationflags = CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT
        if inherit_handles:
            creationflags |= EXTENDED_STARTUPINFO_PRESENT
        creationflags |= CREATE_NO_WINDOW if request.get("hidden", True) else CREATE_NEW_CONSOLE
        working_directory = _working_directory(request)
        if not kernel32.CreateProcessW(
            str(request["file_path"]), command_line, None, None, inherit_handles, creationflags,
            env_block, working_directory, ctypes.cast(ctypes.byref(startup), ctypes.POINTER(STARTUPINFOW)), ctypes.byref(process_info),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        created = True
        job = _open_job(state_path)
        try:
            _ensure_in_job(job, process_info.hProcess)
        finally:
            kernel32.CloseHandle(job)
            job = None
        _atomic_write(result_path, {"pid": int(process_info.dwProcessId)})
        deadline = time.monotonic() + 30
        while not release_path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Project process bootstrap was not released")
            time.sleep(0.05)
        if kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        resumed = True
        return 0
    except Exception:
        if created and not resumed:
            kernel32.TerminateProcess(process_info.hProcess, 1)
        raise
    finally:
        if process_info.hThread:
            kernel32.CloseHandle(process_info.hThread)
        if process_info.hProcess:
            kernel32.CloseHandle(process_info.hProcess)
        if attribute_list:
            kernel32.DeleteProcThreadAttributeList(attribute_list)
        if stdin_handle is not None:
            stdin_handle.close()
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
