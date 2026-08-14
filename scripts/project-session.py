from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
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
JOB_OBJECT_QUERY = 0x0004
JOB_OBJECT_TERMINATE = 0x0008
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102


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
kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
kernel32.IsProcessInJob.restype = wintypes.BOOL
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
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
kernel32.ReleaseMutex.restype = wintypes.BOOL


def _win_error(message: str) -> OSError:
    return OSError(ctypes.get_last_error(), message)


@contextmanager
def _session_mutex(state_path: Path, timeout_seconds: int = 30):
    identity = hashlib.sha256(str(state_path.resolve()).casefold().encode("utf-8")).hexdigest()[:24]
    handle = kernel32.CreateMutexW(None, False, f"Local\\Qwen3TTS-State-{identity}")
    if not handle:
        raise _win_error("Unable to create the project-session mutex")
    try:
        result = kernel32.WaitForSingleObject(handle, timeout_seconds * 1000)
        if result not in (WAIT_OBJECT_0, WAIT_ABANDONED):
            if result == WAIT_TIMEOUT:
                raise TimeoutError("Timed out waiting for the project-session mutation lock")
            raise _win_error("Unable to acquire the project-session mutex")
        try:
            yield
        finally:
            kernel32.ReleaseMutex(handle)
    finally:
        kernel32.CloseHandle(handle)


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
    except (OSError, KeyError, TypeError, ValueError):
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
        already_assigned = wintypes.BOOL()
        if not kernel32.IsProcessInJob(handle, job, ctypes.byref(already_assigned)):
            raise _win_error(f"Unable to inspect Job Object membership for {record['name']} PID {record['pid']}")
        if already_assigned.value:
            return
        if not kernel32.AssignProcessToJobObject(job, handle):
            raise _win_error(f"Unable to assign {record['name']} PID {record['pid']} to the project Job Object")
    finally:
        kernel32.CloseHandle(handle)


def _preflight_assignments(records: list[dict[str, object]]) -> None:
    for record in records:
        handle = _open_process(int(record["pid"]), assign=True)
        kernel32.CloseHandle(handle)


def _arm_job(job: int) -> None:
    limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        raise _win_error("Unable to configure project Job Object cleanup")


def _assign_initial_components(job: int, components: list[dict[str, object]]) -> None:
    for record in components:
        _assign(job, record)
    _arm_job(job)


def _read_request(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Project session request must be a JSON object")
    return value


def _records(request: dict[str, object], key: str) -> list[dict[str, object]]:
    return [_process_record(int(item["pid"]), str(item["name"])) for item in request.get(key, [])]


def _valid_record(record: object) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("name"), str)
        and bool(record["name"].strip())
        and isinstance(record.get("pid"), int)
        and isinstance(record.get("creation_filetime"), int)
        and isinstance(record.get("executable"), str)
        and bool(record["executable"])
    )


def _load_session_state(state_path: Path) -> dict[str, object] | None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(state, dict)
        or state.get("schema") != 1
        or not isinstance(state.get("session_id"), str)
        or not isinstance(state.get("job_name"), str)
        or not _valid_record(state.get("supervisor"))
        or not isinstance(state.get("owners"), list)
        or not isinstance(state.get("components"), list)
        or any(not _valid_record(record) for record in state["owners"])
        or any(not _valid_record(record) for record in state["components"])
    ):
        return None
    return state


def _load_creation_claim(state_path: Path) -> dict[str, object] | None:
    try:
        claim = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(claim, dict)
        or claim.get("schema") != 1
        or claim.get("status") != "creating"
        or not isinstance(claim.get("claim_id"), str)
        or not claim["claim_id"]
        or not _valid_record(claim.get("creator"))
    ):
        return None
    return claim


def _cleanup(project_root: Path, state_path: Path, session_id: str, job: int, components: list[dict[str, object]]) -> bool:
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    cleanup_environment = os.environ.copy()
    cleanup_environment["QWEN3_TTS_SUPERVISOR_CLEANUP"] = "1"
    names = {str(record.get("name", "")) for record in components}
    scripts = []
    if "ComfyUI" in names:
        scripts.append(project_root / "scripts" / "stop-comfyui.ps1")
    if names & {"facade", "qwentts runner", "qwentts.cpp"}:
        scripts.append(project_root / "stop.ps1")
    for script in scripts:
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
    survivors = [record for record in components if _same_process(record)]
    force_failed = False
    if survivors and not kernel32.TerminateJobObject(job, 1):
        force_failed = True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(_same_process(record) for record in components):
        time.sleep(0.2)
    survivors = [record for record in components if _same_process(record)]
    if force_failed or survivors:
        with (project_root / "logs" / "project-session.log").open("a", encoding="utf-8") as log:
            names = ", ".join(f"{item['name']} PID {item['pid']}" for item in survivors) or "force operation failed"
            log.write(f"{datetime.now(timezone.utc).isoformat()} session={session_id} cleanup incomplete: {names}\n")
        return False
    try:
        with _session_mutex(state_path):
            state = _load_session_state(state_path)
            if state is None or state.get("session_id") == session_id:
                state_path.unlink(missing_ok=True)
                state_files = []
                if names & {"facade", "qwentts runner", "qwentts.cpp"}:
                    state_files.extend(("server.json", "qwentts.json"))
                if "ComfyUI" in names:
                    state_files.append("comfyui.json")
                for name in state_files:
                    (state_path.parent / name).unlink(missing_ok=True)
    except (OSError, TimeoutError):
        return False
    return True


def supervise(project_root: Path, request_path: Path, state_path: Path, claim_id: str | None = None) -> int:
    with _session_mutex(state_path):
        if claim_id is not None:
            claim = _load_creation_claim(state_path)
            if claim is None or claim["claim_id"] != claim_id:
                return 9
        request = _read_request(request_path)
        request_path.unlink(missing_ok=True)
        existing = _load_session_state(state_path) if state_path.exists() else None
        if existing is not None and _same_process(existing["supervisor"]):
            return 6
        session_id = uuid4().hex
        job_name = f"Local\\Qwen3TTS-{session_id}"
        job = kernel32.CreateJobObjectW(None, job_name)
        if not job:
            raise _win_error("Unable to create the project Job Object")
        try:
            owners = _records(request, "owners")
            components = _records(request, "components")
            _preflight_assignments(components)
            _assign_initial_components(job, components)
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
        except Exception:
            kernel32.CloseHandle(job)
            raise
    try:
        reason = "unknown"
        while True:
            missing_owner = None
            missing_component = None
            try:
                with _session_mutex(state_path):
                    latest = _load_session_state(state_path)
                    if latest is None or latest["session_id"] != session_id:
                        reason = "project session state became invalid"
                        break
                    owners = latest["owners"]
                    components = latest["components"]
                    if latest.get("stop_requested"):
                        reason = str(latest.get("stop_reason") or "controlled teardown requested")
                        break
                    missing_owner = next((record for record in owners if not _same_process(record)), None)
                    missing_component = next((record for record in components if not _same_process(record)), None)
            except (OSError, TimeoutError):
                reason = "project session state could not be read safely"
                break
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
        return 0 if _cleanup(project_root, state_path, session_id, job, components) else 10
    finally:
        kernel32.CloseHandle(job)


def _attach_locked(request_path: Path, state_path: Path) -> int:
    if not state_path.exists():
        return 3
    state = _load_session_state(state_path)
    if state is None or not _same_process(state["supervisor"]):
        return 3
    if any(not _same_process(record) for record in state["components"]):
        return 4
    request = _read_request(request_path)
    request_path.unlink(missing_ok=True)
    requested_components = _records(request, "components")
    requested_owners = _records(request, "owners")
    _preflight_assignments(requested_components)
    job = kernel32.OpenJobObjectW(JOB_OBJECT_ASSIGN_PROCESS | JOB_OBJECT_QUERY | JOB_OBJECT_TERMINATE, False, str(state["job_name"]))
    if not job:
        return 5
    try:
        owners = list(state["owners"])
        components = list(state["components"])
        known = {(int(item["pid"]), int(item["creation_filetime"])) for item in components}
        assigned: list[dict[str, object]] = []
        try:
            for record in requested_components:
                identity = (int(record["pid"]), int(record["creation_filetime"]))
                if identity not in known:
                    _assign(job, record)
                    components.append(record)
                    assigned.append(record)
                    known.add(identity)
                    state["components"] = components
                    _atomic_write(state_path, state)
        except Exception:
            if assigned:
                state["components"] = components
                state["stop_requested"] = True
                state["stop_reason"] = "attach failed after assigning components"
                try:
                    _atomic_write(state_path, state)
                except OSError:
                    kernel32.TerminateJobObject(job, 1)
            raise
        owner_known = {(int(item["pid"]), int(item["creation_filetime"])) for item in owners}
        for record in requested_owners:
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


def attach(request_path: Path, state_path: Path) -> int:
    with _session_mutex(state_path):
        return _attach_locked(request_path, state_path)


def ensure(project_root: Path, request_path: Path, state_path: Path) -> int:
    request = _read_request(request_path)
    claim_id = uuid4().hex
    claimed = False
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with _session_mutex(state_path):
            state = _load_session_state(state_path) if state_path.exists() else None
            if state is not None and _same_process(state["supervisor"]):
                return _attach_locked(request_path, state_path)
            if request.get("components"):
                return 7
            claim = _load_creation_claim(state_path) if state_path.exists() else None
            if claim is None or not _same_process(claim["creator"]):
                _atomic_write(state_path, {
                    "schema": 1,
                    "status": "creating",
                    "claim_id": claim_id,
                    "creator": _process_record(os.getpid(), "session creator"),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
                claimed = True
        if claimed:
            break
        time.sleep(0.1)
    if not claimed:
        return 8
    logs = project_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(Path(__file__).resolve()), "supervise",
        "--project-root", str(project_root), "--request", str(request_path),
        "--state", str(state_path), "--claim-id", claim_id,
    ]
    with (logs / "project-session.out.log").open("ab", buffering=0) as stdout, \
         (logs / "project-session.err.log").open("ab", buffering=0) as stderr:
        supervisor = subprocess.Popen(command, cwd=project_root, stdout=stdout, stderr=stderr)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = _load_session_state(state_path) if state_path.exists() else None
        if state is not None and _same_process(state["supervisor"]):
            return 0
        if supervisor.poll() is not None:
            return supervisor.returncode or 1
        time.sleep(0.1)
    supervisor.terminate()
    supervisor.wait(timeout=5)
    return 8


def release_owner(request_path: Path, state_path: Path) -> int:
    with _session_mutex(state_path):
        state = _load_session_state(state_path) if state_path.exists() else None
        if state is None or not _same_process(state["supervisor"]):
            return 3
        request = _read_request(request_path)
        request_path.unlink(missing_ok=True)
        releasing = _records(request, "owners")
        identities = {(int(item["pid"]), int(item["creation_filetime"])) for item in releasing}
        state["owners"] = [
            item for item in state["owners"]
            if (int(item["pid"]), int(item["creation_filetime"])) not in identities
        ]
        _atomic_write(state_path, state)
        return 0


def release_component(request_path: Path, state_path: Path) -> int:
    with _session_mutex(state_path):
        state = _load_session_state(state_path) if state_path.exists() else None
        if state is None or not _same_process(state["supervisor"]):
            return 3
        request = _read_request(request_path)
        request_path.unlink(missing_ok=True)
        releasing = _records(request, "components")
        identities = {(int(item["pid"]), int(item["creation_filetime"])) for item in releasing}
        state["components"] = [
            item for item in state["components"]
            if (int(item["pid"]), int(item["creation_filetime"])) not in identities
        ]
        _atomic_write(state_path, state)
        return 0


def request_stop(request_path: Path, state_path: Path) -> int:
    with _session_mutex(state_path):
        state = _load_session_state(state_path) if state_path.exists() else None
        if state is None or not _same_process(state["supervisor"]):
            return 3
        request = _read_request(request_path)
        request_path.unlink(missing_ok=True)
        expected_pid = request.get("supervisor_pid")
        if expected_pid is not None and int(expected_pid) != int(state["supervisor"]["pid"]):
            return 5
        state["stop_requested"] = True
        state["stop_reason"] = str(request.get("reason") or "controlled teardown requested")
        _atomic_write(state_path, state)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Qwen3-TTS Windows project session supervisor")
    parser.add_argument("mode", choices=("supervise", "attach", "ensure", "release-owner", "release-component", "request-stop"))
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--claim-id")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    request = Path(args.request).resolve()
    state = Path(args.state).resolve()
    state.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "attach":
        return attach(request, state)
    if args.mode == "ensure":
        return ensure(root, request, state)
    if args.mode == "release-owner":
        return release_owner(request, state)
    if args.mode == "release-component":
        return release_component(request, state)
    if args.mode == "request-stop":
        return request_stop(request, state)
    return supervise(root, request, state, claim_id=args.claim_id)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Project session supervisor failed: {exc}", file=sys.stderr)
        raise
