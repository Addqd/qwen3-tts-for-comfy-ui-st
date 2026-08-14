from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object lifecycle")
ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "scripts" / "project-session.py"


def _load_supervisor_module():
    spec = importlib.util.spec_from_file_location("project_session", SUPERVISOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


SESSION = _load_supervisor_module() if os.name == "nt" else None


def _wait(predicate, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition was not reached before timeout")


def _sleeping_process() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _prepare_cleanup_scripts(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "stop-comfyui.ps1").write_text("exit 0\n", encoding="utf-8")
    (root / "stop.ps1").write_text("exit 0\n", encoding="utf-8")


def test_component_exit_terminates_the_entire_project_job(tmp_path):
    _prepare_cleanup_scripts(tmp_path)
    first, second = _sleeping_process(), _sleeping_process()
    request = tmp_path / "request.json"
    state = tmp_path / "project-session.json"
    request.write_text(json.dumps({
        "owners": [{"name": "test owner", "pid": os.getpid()}],
        "components": [{"name": "first", "pid": first.pid}, {"name": "second", "pid": second.pid}],
    }), encoding="utf-8")
    supervisor = subprocess.Popen([
        sys.executable, str(SUPERVISOR), "supervise", "--project-root", str(tmp_path),
        "--request", str(request), "--state", str(state),
    ])
    try:
        _wait(state.exists)
        first.terminate()
        first.wait(timeout=5)
        second.wait(timeout=15)
        supervisor.wait(timeout=15)
        assert not state.exists()
    finally:
        for process in (first, second, supervisor):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_supervisor_exit_closes_the_job_and_kills_components(tmp_path):
    _prepare_cleanup_scripts(tmp_path)
    first, second = _sleeping_process(), _sleeping_process()
    request = tmp_path / "request.json"
    state = tmp_path / "project-session.json"
    request.write_text(json.dumps({
        "owners": [{"name": "test owner", "pid": os.getpid()}],
        "components": [{"name": "first", "pid": first.pid}, {"name": "second", "pid": second.pid}],
    }), encoding="utf-8")
    supervisor = subprocess.Popen([
        sys.executable, str(SUPERVISOR), "supervise", "--project-root", str(tmp_path),
        "--request", str(request), "--state", str(state),
    ])
    try:
        _wait(state.exists)
        supervisor.terminate()
        supervisor.wait(timeout=5)
        first.wait(timeout=10)
        second.wait(timeout=10)
    finally:
        state.unlink(missing_ok=True)
        for process in (first, second, supervisor):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_supported_entrypoint_can_join_existing_project_session(tmp_path):
    _prepare_cleanup_scripts(tmp_path)
    first, second = _sleeping_process(), _sleeping_process()
    request = tmp_path / "request.json"
    attach_request = tmp_path / "attach.json"
    state = tmp_path / "project-session.json"
    request.write_text(json.dumps({
        "owners": [{"name": "first launcher", "pid": os.getpid()}],
        "components": [{"name": "first", "pid": first.pid}],
    }), encoding="utf-8")
    supervisor = subprocess.Popen([
        sys.executable, str(SUPERVISOR), "supervise", "--project-root", str(tmp_path),
        "--request", str(request), "--state", str(state),
    ])
    try:
        _wait(state.exists)
        attach_request.write_text(json.dumps({
            "owners": [{"name": "second launcher", "pid": os.getpid()}],
            "components": [{"name": "second", "pid": second.pid}],
        }), encoding="utf-8")
        attached = subprocess.run([
            sys.executable, str(SUPERVISOR), "attach", "--project-root", str(tmp_path),
            "--request", str(attach_request), "--state", str(state),
        ], check=False)
        assert attached.returncode == 0
        persisted = json.loads(state.read_text(encoding="utf-8"))
        assert {item["name"] for item in persisted["components"]} == {"first", "second"}
        second.terminate()
        second.wait(timeout=5)
        first.wait(timeout=15)
        supervisor.wait(timeout=15)
    finally:
        for process in (first, second, supervisor):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_owner_exit_terminates_managed_components(tmp_path):
    _prepare_cleanup_scripts(tmp_path)
    owner, component = _sleeping_process(), _sleeping_process()
    request = tmp_path / "request.json"
    state = tmp_path / "project-session.json"
    request.write_text(json.dumps({
        "owners": [{"name": "launcher", "pid": owner.pid}],
        "components": [{"name": "component", "pid": component.pid}],
    }), encoding="utf-8")
    supervisor = subprocess.Popen([
        sys.executable, str(SUPERVISOR), "supervise", "--project-root", str(tmp_path),
        "--request", str(request), "--state", str(state),
    ])
    try:
        _wait(state.exists)
        owner.terminate()
        owner.wait(timeout=5)
        component.wait(timeout=15)
        supervisor.wait(timeout=15)
        assert not state.exists()
    finally:
        for process in (owner, component, supervisor):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("payload", ["{", "[]", "{}", '{"schema":1}', '{"schema":1,"job_name":"x"}'])
def test_attach_treats_corrupt_or_incomplete_state_as_stale(tmp_path, payload):
    state = tmp_path / "project-session.json"
    state.write_text(payload, encoding="utf-8")
    assert SESSION.attach(tmp_path / "unused-request.json", state) == 3


def test_attach_returns_four_for_dead_existing_component(tmp_path, monkeypatch):
    record = {"pid": 1, "creation_filetime": 1, "executable": "x.exe"}
    state = tmp_path / "project-session.json"
    state.write_text(json.dumps({
        "schema": 1, "session_id": "s", "job_name": "Local\\job",
        "supervisor": record, "owners": [], "components": [record],
    }), encoding="utf-8")
    calls = iter((True, False))
    monkeypatch.setattr(SESSION, "_same_process", lambda _record: next(calls))
    assert SESSION.attach(tmp_path / "unused-request.json", state) == 4


def test_initial_assignment_failure_does_not_arm_kill_on_close(monkeypatch):
    events = []

    def assign(_job, record):
        events.append(record["name"])
        if record["name"] == "second":
            raise OSError("assignment failed")

    monkeypatch.setattr(SESSION, "_assign", assign)
    monkeypatch.setattr(SESSION, "_arm_job", lambda _job: events.append("armed"))
    with pytest.raises(OSError, match="assignment failed"):
        SESSION._assign_initial_components(1, [{"name": "first"}, {"name": "second"}])
    assert events == ["first", "second"]


def test_partial_attach_persists_assigned_component_and_requests_teardown(tmp_path, monkeypatch):
    record = {"pid": 10, "creation_filetime": 10, "executable": "python.exe"}
    first = {"name": "first", "pid": 11, "creation_filetime": 11, "executable": "python.exe"}
    second = {"name": "second", "pid": 12, "creation_filetime": 12, "executable": "python.exe"}
    state = tmp_path / "project-session.json"
    state.write_text(json.dumps({
        "schema": 1, "session_id": "s", "job_name": "Local\\job",
        "supervisor": record, "owners": [], "components": [],
    }), encoding="utf-8")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"owners": [], "components": [{"name": "first", "pid": 11}, {"name": "second", "pid": 12}]}), encoding="utf-8")
    monkeypatch.setattr(SESSION, "_same_process", lambda _record: True)
    monkeypatch.setattr(SESSION, "_records", lambda _request, key: [first, second] if key == "components" else [])
    monkeypatch.setattr(SESSION, "_preflight_assignments", lambda _records: None)
    monkeypatch.setattr(SESSION, "kernel32", type("Kernel", (), {
        "OpenJobObjectW": staticmethod(lambda *_args: 1),
        "CloseHandle": staticmethod(lambda *_args: True),
        "TerminateJobObject": staticmethod(lambda *_args: True),
    })())
    assignments = []

    def assign(_job, item):
        assignments.append(item["name"])
        if item["name"] == "second":
            raise OSError("late assignment failure")

    monkeypatch.setattr(SESSION, "_assign", assign)
    with pytest.raises(OSError, match="late assignment failure"):
        SESSION.attach(request, state)
    persisted = json.loads(state.read_text(encoding="utf-8"))
    assert assignments == ["first", "second"]
    assert [item["name"] for item in persisted["components"]] == ["first"]
    assert persisted["stop_requested"] is True
    assert not request.exists()


def test_launcher_owner_and_stop_waiting_are_conditionally_scoped():
    combined = (ROOT / "scripts" / "start-tts-and-comfyui.ps1").read_text(encoding="utf-8-sig")
    stop_backend = (ROOT / "stop.ps1").read_text(encoding="utf-8-sig")
    stop_comfy = (ROOT / "scripts" / "stop-comfyui.ps1").read_text(encoding="utf-8-sig")
    assert "if ($WaitForComfyUIExit) { $SessionParameters.MonitorOwner = $true }" in combined
    assert "Test-ProjectSessionComponent" in stop_backend and "Wait-ProjectSessionTeardown" in stop_backend
    assert "Test-ProjectSessionComponent" in stop_comfy and "Wait-ProjectSessionTeardown" in stop_comfy
