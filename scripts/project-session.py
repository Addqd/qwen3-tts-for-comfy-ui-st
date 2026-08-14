from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4


if os.name != "nt":
    raise RuntimeError("Project session supervision is supported only on Windows")


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
JOB_OBJECT_ASSIGN_PROCESS = 0x0001


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenJobObjectW.restype = wintypes.HANDLE
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateJobObject.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
kernel32.GetProcessTimes.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def _win_error(message: str) -> OSError:
    return OSError(ctypes.get_last_error(), message)


def _open_process(pid: int, assign: bool = False) -> int:
    access = SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
    if assign:
        access |= PROCESS_SET_QUOTA | PROCESS_TERMINATE
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        raise _win_error(f"Unable to open project process {pid}")
    return handle


def _process_record(pid: int, name: str) -> dict[str, object]:
    handle = _open_process(pid)
    try:
        created, exited, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            raise _win_error(f"Unable to read creation time for process {pid}")
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise _win_error(f"Unable to read executable for process {pid}")
        creation_filetime = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return {"name": name, "pid": pid, "creation_filetime": creation_filetime, "executable": str(Path(buffer.value).resolve())}
    finally:
        kernel32.CloseHandle(handle)


def _same_process(record: dict[str, object]) -> bool:
    try:
        current = _process_record(int(record["pid"]), str(record.get("name", "process")))
    except OSError:
        return False
    return (
        current["creation_filetime"] == record.get("creation_filetime")
        and str(current["executable"]).casefold() == str(record.get("executable", "")).casefold()
    )


def _atomic_write(path: Path, value: dict[str, object]) -> None:
    temporary = Path(f"{path}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _assign(job: int, record: dict[str, object]) -> None:
    handle = _open_process(int(record["pid"]), assign=True)
    try:
        if not kernel32.AssignProcessToJobObject(job, handle):
            raise _win_error(f"Unable to assign {record['name']} PID {record['pid']} to the project Job Object")
    finally:
        kernel32.CloseHandle(handle)


def _read_request(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Project session request must be a JSON object")
    return value


def _records(request: dict[str, object], key: str) -> list[dict[str, object]]:
    return [_process_record(int(item["pid"]), str(item["name"])) for item in request.get(key, [])]


def _cleanup(project_root: Path, state_path: Path, session_id: str, job: int, components: list[dict[str, object]]) -> None:
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    cleanup_environment = os.environ.copy()
    cleanup_environment["QWEN3_TTS_SUPERVISOR_CLEANUP"] = "1"
    for script in (project_root / "scripts" / "stop-comfyui.ps1", project_root / "stop.ps1"):
        try:
            subprocess.run(
                [str(powershell), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                cwd=project_root,
                timeout=20,
                check=False,
                capture_output=True,
                env=cleanup_environment,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(_same_process(record) for record in components):
        time.sleep(0.2)
    if any(_same_process(record) for record in components):
        kernel32.TerminateJobObject(job, 1)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        if state.get("session_id") == session_id:
            state_path.unlink(missing_ok=True)
            for name in ("server.json", "qwentts.json", "comfyui.json"):
                (state_path.parent / name).unlink(missing_ok=True)
    except (OSError, ValueError, json.JSONDecodeError):
        pass


def supervise(project_root: Path, request_path: Path, state_path: Path) -> int:
    request = _read_request(request_path)
    request_path.unlink(missing_ok=True)
    session_id = uuid4().hex
    job_name = f"Local\\Qwen3TTS-{session_id}"
    job = kernel32.CreateJobObjectW(None, job_name)
    if not job:
        raise _win_error("Unable to create the project Job Object")
    try:
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise _win_error("Unable to configure project Job Object cleanup")
        owners = _records(request, "owners")
        components = _records(request, "components")
        for record in components:
            _assign(job, record)
        state: dict[str, object] = {
            "schema": 1,
            "session_id": session_id,
            "job_name": job_name,
            "supervisor": _process_record(os.getpid(), "supervisor"),
            "owners": owners,
            "components": components,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_write(state_path, state)
        reason = "unknown"
        while True:
            try:
                latest = json.loads(state_path.read_text(encoding="utf-8-sig"))
                if latest.get("session_id") == session_id:
                    owners = latest.get("owners", owners)
                    components = latest.get("components", components)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            missing_owner = next((record for record in owners if not _same_process(record)), None)
            missing_component = next((record for record in components if not _same_process(record)), None)
            if missing_owner:
                reason = f"owner {missing_owner['name']} closed"
                break
            if missing_component:
                reason = f"component {missing_component['name']} closed"
                break
            time.sleep(0.4)
        (project_root / "logs").mkdir(parents=True, exist_ok=True)
        with (project_root / "logs" / "project-session.log").open("a", encoding="utf-8") as log:
            log.write(f"{datetime.now(timezone.utc).isoformat()} session={session_id} stopping: {reason}\n")
        _cleanup(project_root, state_path, session_id, job, components)
        return 0
    finally:
        kernel32.CloseHandle(job)


def attach(request_path: Path, state_path: Path) -> int:
    if not state_path.exists():
        return 3
    state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    if not _same_process(state.get("supervisor", {})):
        return 3
    if any(not _same_process(record) for record in state.get("components", [])):
        return 4
    request = _read_request(request_path)
    request_path.unlink(missing_ok=True)
    job = kernel32.OpenJobObjectW(JOB_OBJECT_ASSIGN_PROCESS, False, str(state["job_name"]))
    if not job:
        return 3
    try:
        owners = list(state.get("owners", []))
        components = list(state.get("components", []))
        known = {(int(item["pid"]), int(item["creation_filetime"])) for item in components}
        for record in _records(request, "components"):
            identity = (int(record["pid"]), int(record["creation_filetime"]))
            if identity not in known:
                _assign(job, record)
                components.append(record)
                known.add(identity)
        owner_known = {(int(item["pid"]), int(item["creation_filetime"])) for item in owners}
        for record in _records(request, "owners"):
            identity = (int(record["pid"]), int(record["creation_filetime"]))
            if identity not in owner_known:
                owners.append(record)
                owner_known.add(identity)
        state["owners"] = owners
        state["components"] = components
        _atomic_write(state_path, state)
        return 0
    finally:
        kernel32.CloseHandle(job)


def main() -> int:
    parser = argparse.ArgumentParser(description="Qwen3-TTS Windows project session supervisor")
    parser.add_argument("mode", choices=("supervise", "attach"))
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    request = Path(args.request).resolve()
    state = Path(args.state).resolve()
    state.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "attach":
        return attach(request, state)
    return supervise(root, request, state)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Project session supervisor failed: {exc}", file=sys.stderr)
        raise
