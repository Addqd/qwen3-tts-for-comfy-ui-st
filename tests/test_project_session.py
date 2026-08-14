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
PROCESS_HELPER = ROOT / "scripts" / "project-process.py"


def _load_supervisor_module():
    spec = importlib.util.spec_from_file_location("project_session", SUPERVISOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _load_process_helper_module():
    spec = importlib.util.spec_from_file_location("project_process", PROCESS_HELPER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


SESSION = _load_supervisor_module() if os.name == "nt" else None
PROCESS = _load_process_helper_module() if os.name == "nt" else None


def _wait(predicate, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition was not reached before timeout")


def _sleeping_process() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _session_command(mode: str, root: Path, request: Path, state: Path) -> list[str]:
    return [
        sys.executable, str(SUPERVISOR), mode, "--project-root", str(root),
        "--request", str(request), "--state", str(state),
    ]


def _request_stop(root: Path, state: Path, supervisor_pid: int) -> None:
    request = root / f"stop-{time.time_ns()}.json"
    request.write_text(json.dumps({"supervisor_pid": supervisor_pid, "reason": "test cleanup"}), encoding="utf-8")
    subprocess.run(_session_command("request-stop", root, request, state), check=False, timeout=10)


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


def test_ensure_attaches_components_to_an_existing_live_session(tmp_path):
    _prepare_cleanup_scripts(tmp_path)
    owner, component = _sleeping_process(), _sleeping_process()
    state = tmp_path / "project-session.json"
    initial = tmp_path / "initial.json"
    initial.write_text(json.dumps({
        "owners": [{"name": "launcher", "pid": owner.pid}], "components": [],
    }), encoding="utf-8")
    assert subprocess.run(_session_command("ensure", tmp_path, initial, state), timeout=20).returncode == 0
    attach = tmp_path / "ensure-component.json"
    attach.write_text(json.dumps({
        "owners": [], "components": [{"name": "component", "pid": component.pid}],
    }), encoding="utf-8")
    try:
        assert subprocess.run(_session_command("ensure", tmp_path, attach, state), timeout=20).returncode == 0
        persisted = json.loads(state.read_text(encoding="utf-8"))
        assert [item["name"] for item in persisted["components"]] == ["component"]
    finally:
        owner.terminate()
        owner.wait(timeout=5)
        if component.poll() is None:
            component.wait(timeout=15)
        _wait(lambda: not state.exists(), timeout=15)


@pytest.mark.parametrize("payload", ["{", "[]", "{}", '{"schema":1}', '{"schema":1,"job_name":"x"}'])
def test_attach_treats_corrupt_or_incomplete_state_as_stale(tmp_path, payload):
    state = tmp_path / "project-session.json"
    state.write_text(payload, encoding="utf-8")
    assert SESSION.attach(tmp_path / "unused-request.json", state) == 3


def test_attach_returns_four_for_dead_existing_component(tmp_path, monkeypatch):
    record = {"name": "process", "pid": 1, "creation_filetime": 1, "executable": "x.exe"}
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
    record = {"name": "supervisor", "pid": 10, "creation_filetime": 10, "executable": "python.exe"}
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
        SESSION._attach_locked(request, state)
    persisted = json.loads(state.read_text(encoding="utf-8"))
    assert assignments == ["first", "second"]
    assert [item["name"] for item in persisted["components"]] == ["first"]
    assert persisted["stop_requested"] is True
    assert not request.exists()


def test_cleanup_preserves_state_when_force_termination_cannot_remove_survivors(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    state = tmp_path / "project-session.json"
    state.write_text("diagnostic", encoding="utf-8")
    survivor = {"name": "facade", "pid": 123, "creation_filetime": 1, "executable": "python.exe"}
    monkeypatch.setattr(SESSION.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(SESSION, "_same_process", lambda _record: True)
    monkeypatch.setattr(SESSION.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(SESSION.time, "monotonic", iter([0, 6, 6, 12]).__next__)
    monkeypatch.setattr(SESSION, "kernel32", type("Kernel", (), {
        "TerminateJobObject": staticmethod(lambda *_args: False),
    })())
    assert SESSION._cleanup(tmp_path, state, "session", 1, [survivor]) is False
    assert state.read_text(encoding="utf-8") == "diagnostic"


def test_launcher_owner_and_stop_waiting_are_conditionally_scoped():
    combined = (ROOT / "scripts" / "start-tts-and-comfyui.ps1").read_text(encoding="utf-8-sig")
    backend = (ROOT / "start.ps1").read_text(encoding="utf-8-sig")
    comfy = (ROOT / "scripts" / "start-comfyui.ps1").read_text(encoding="utf-8-sig")
    stop_backend = (ROOT / "stop.ps1").read_text(encoding="utf-8-sig")
    stop_comfy = (ROOT / "scripts" / "stop-comfyui.ps1").read_text(encoding="utf-8-sig")
    session_common = (ROOT / "scripts" / "session-common.ps1").read_text(encoding="utf-8-sig")
    process_helper = (ROOT / "scripts" / "project-process.py").read_text(encoding="utf-8-sig")
    assert combined.index('Start-OrJoin-ProjectSession -OwnerName "combined launcher" -MonitorOwner -Components @()') < combined.index('start-comfyui.ps1')
    assert backend.index('Start-OrJoin-ProjectSession -OwnerName "backend startup" -MonitorOwner -Components @()') < backend.index("Start-ManagedProjectProcess")
    assert "Start-ManagedProjectProcess" in comfy
    assert 'name = "combined startup launcher"' not in combined
    assert 'name = "backend startup launcher"' not in backend
    assert 'name = "ComfyUI startup launcher"' not in comfy
    assert '@{ name = "combined startup launcher"; pid = $PID }' not in combined
    assert '@{ name = "backend startup launcher"; pid = $PID }' not in backend
    assert '@{ name = "ComfyUI startup launcher"; pid = $PID }' not in comfy
    assert "Release-ProjectSessionOwner" in combined
    assert "Release-ProjectSessionOwner" in backend
    assert "Release-ProjectSessionOwner" in comfy
    assert "Release-ProjectSessionComponent" not in combined
    assert "Release-ProjectSessionComponent" not in backend
    assert "Release-ProjectSessionComponent" not in comfy
    assert "Test-ProjectSessionComponent" in stop_backend and "Wait-ProjectSessionTeardown" in stop_backend
    assert "Test-ProjectSessionComponent" in stop_comfy and "Wait-ProjectSessionTeardown" in stop_comfy
    assert session_common.count("for ($Attempt = 1; $Attempt -le 3; $Attempt++)") == 2
    assert session_common.count("Test-ProjectSessionRecord") >= 3
    assert session_common.index("Release-ProjectSessionComponent -ComponentName $BootstrapName") < session_common.index("New-Item -ItemType File -Path $ReleasePath")
    assert 'parser.add_argument("--done"' not in process_helper
    assert "if created and not resumed:" in process_helper


def test_reused_comfyui_schema_is_validated_before_job_attachment():
    comfy = (ROOT / "scripts" / "start-comfyui.ps1").read_text(encoding="utf-8-sig")
    reused = comfy[comfy.index("if (Test-Path -LiteralPath $script:ComfyUIStatePath)"):comfy.index("if (Test-LocalPortInUse")]
    assert reused.index("Assert-QwenTTSCloneVoiceSchema") < reused.index('@{ name = "ComfyUI"; pid = $OldProcess.Id }')


@pytest.mark.parametrize(
    "mutator",
    [
        lambda state: {**state, "owners": None},
        lambda state: {**state, "components": "bad"},
        lambda state: {**state, "owners": [{"pid": os.getpid(), "creation_filetime": 1, "executable": "x"}]},
    ],
)
def test_malformed_live_state_triggers_controlled_job_cleanup(tmp_path, mutator):
    _prepare_cleanup_scripts(tmp_path)
    component = _sleeping_process()
    request = tmp_path / "request.json"
    state_path = tmp_path / "project-session.json"
    request.write_text(json.dumps({
        "owners": [{"name": "test owner", "pid": os.getpid()}],
        "components": [{"name": "component", "pid": component.pid}],
    }), encoding="utf-8")
    supervisor = subprocess.Popen(_session_command("supervise", tmp_path, request, state_path))
    try:
        _wait(state_path.exists)
        state_path.write_text(json.dumps(mutator(json.loads(state_path.read_text(encoding="utf-8")))), encoding="utf-8")
        component.wait(timeout=15)
        supervisor.wait(timeout=15)
        assert not state_path.exists()
    finally:
        for process in (component, supervisor):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_two_simultaneous_session_starters_share_one_supervisor(tmp_path):
    _prepare_cleanup_scripts(tmp_path)
    owner = _sleeping_process()
    state = tmp_path / "project-session.json"
    requests = [tmp_path / f"ensure-{index}.json" for index in range(2)]
    for request in requests:
        request.write_text(json.dumps({"owners": [{"name": "launcher", "pid": owner.pid}], "components": []}), encoding="utf-8")
    starters = [subprocess.Popen(_session_command("ensure", tmp_path, request, state)) for request in requests]
    try:
        assert [process.wait(timeout=20) for process in starters] == [0, 0]
        persisted = json.loads(state.read_text(encoding="utf-8"))
        assert SESSION._same_process(persisted["supervisor"])
    finally:
        owner.terminate()
        owner.wait(timeout=5)
        if state.exists():
            persisted = json.loads(state.read_text(encoding="utf-8"))
            _request_stop(tmp_path, state, int(persisted["supervisor"]["pid"]))
        _wait(lambda: not state.exists(), timeout=15)


def test_stale_state_claim_cannot_remove_newly_published_session(tmp_path):
    _prepare_cleanup_scripts(tmp_path)
    owner = _sleeping_process()
    state = tmp_path / "project-session.json"
    state.write_text('{"schema":1}', encoding="utf-8")
    requests = [tmp_path / f"stale-{index}.json" for index in range(2)]
    for request in requests:
        request.write_text(json.dumps({"owners": [{"name": "launcher", "pid": owner.pid}], "components": []}), encoding="utf-8")
    starters = [subprocess.Popen(_session_command("ensure", tmp_path, request, state)) for request in requests]
    try:
        assert [process.wait(timeout=20) for process in starters] == [0, 0]
        assert json.loads(state.read_text(encoding="utf-8"))["session_id"]
    finally:
        owner.terminate()
        owner.wait(timeout=5)
        _wait(lambda: not state.exists(), timeout=15)


def test_dead_creation_claim_is_recovered_by_one_supervisor(tmp_path):
    _prepare_cleanup_scripts(tmp_path)
    owner = _sleeping_process()
    dead_creator = _sleeping_process()
    dead_record = SESSION._process_record(dead_creator.pid, "session creator")
    dead_creator.terminate()
    dead_creator.wait(timeout=5)
    state = tmp_path / "project-session.json"
    state.write_text(json.dumps({
        "schema": 1, "status": "creating", "claim_id": "abandoned", "creator": dead_record,
    }), encoding="utf-8")
    requests = [tmp_path / f"recover-{index}.json" for index in range(2)]
    for request in requests:
        request.write_text(json.dumps({"owners": [{"name": "launcher", "pid": owner.pid}], "components": []}), encoding="utf-8")
    starters = [subprocess.Popen(_session_command("ensure", tmp_path, request, state)) for request in requests]
    try:
        assert [process.wait(timeout=20) for process in starters] == [0, 0]
        persisted = json.loads(state.read_text(encoding="utf-8"))
        assert persisted["session_id"]
        assert SESSION._same_process(persisted["supervisor"])
    finally:
        owner.terminate()
        owner.wait(timeout=5)
        _wait(lambda: not state.exists(), timeout=15)


def test_two_concurrent_attaches_merge_components_without_loss(tmp_path):
    _prepare_cleanup_scripts(tmp_path)
    owner, first, second = _sleeping_process(), _sleeping_process(), _sleeping_process()
    state = tmp_path / "project-session.json"
    initial = tmp_path / "initial.json"
    initial.write_text(json.dumps({"owners": [{"name": "launcher", "pid": owner.pid}], "components": []}), encoding="utf-8")
    created = subprocess.run(_session_command("ensure", tmp_path, initial, state), check=False, timeout=20)
    assert created.returncode == 0
    requests = []
    for name, process in (("first", first), ("second", second)):
        request = tmp_path / f"attach-{name}.json"
        request.write_text(json.dumps({"owners": [], "components": [{"name": name, "pid": process.pid}]}), encoding="utf-8")
        requests.append(request)
    attaches = [subprocess.Popen(_session_command("attach", tmp_path, request, state)) for request in requests]
    try:
        assert [process.wait(timeout=20) for process in attaches] == [0, 0]
        persisted = json.loads(state.read_text(encoding="utf-8"))
        assert {item["name"] for item in persisted["components"]} == {"first", "second"}
    finally:
        owner.terminate()
        owner.wait(timeout=5)
        for process in (first, second):
            if process.poll() is None:
                process.wait(timeout=15)
        _wait(lambda: not state.exists(), timeout=15)


def test_owner_death_during_partial_startup_stops_first_attached_component(tmp_path):
    _prepare_cleanup_scripts(tmp_path)
    owner, component = _sleeping_process(), _sleeping_process()
    state = tmp_path / "project-session.json"
    initial = tmp_path / "initial.json"
    initial.write_text(json.dumps({"owners": [{"name": "startup launcher", "pid": owner.pid}], "components": []}), encoding="utf-8")
    created = subprocess.run(_session_command("ensure", tmp_path, initial, state), check=False, timeout=20)
    assert created.returncode == 0
    attach_request = tmp_path / "attach.json"
    attach_request.write_text(json.dumps({"owners": [], "components": [{"name": "first component", "pid": component.pid}]}), encoding="utf-8")
    assert subprocess.run(_session_command("attach", tmp_path, attach_request, state), check=False, timeout=20).returncode == 0
    owner.terminate()
    owner.wait(timeout=5)
    component.wait(timeout=15)
    _wait(lambda: not state.exists(), timeout=15)


def test_project_teardown_does_not_kill_unassigned_user_launcher(tmp_path):
    _prepare_cleanup_scripts(tmp_path)
    launcher, component = _sleeping_process(), _sleeping_process()
    state = tmp_path / "project-session.json"
    initial = tmp_path / "initial.json"
    initial.write_text(json.dumps({"owners": [{"name": "startup", "pid": launcher.pid}], "components": []}), encoding="utf-8")
    assert subprocess.run(_session_command("ensure", tmp_path, initial, state), check=False, timeout=20).returncode == 0
    attach_request = tmp_path / "attach.json"
    attach_request.write_text(json.dumps({
        "owners": [],
        "components": [{"name": "ready service", "pid": component.pid}],
    }), encoding="utf-8")
    assert subprocess.run(_session_command("attach", tmp_path, attach_request, state), check=False, timeout=20).returncode == 0
    component.terminate()
    component.wait(timeout=5)
    _wait(lambda: not state.exists(), timeout=15)
    assert launcher.poll() is None
    launcher.terminate()
    launcher.wait(timeout=5)


def test_bootstrap_exit_after_child_creation_leaves_no_orphan(tmp_path):
    _prepare_cleanup_scripts(tmp_path)
    state = tmp_path / "project-session.json"
    initial = tmp_path / "initial.json"
    initial.write_text(json.dumps({"owners": [{"name": "test owner", "pid": os.getpid()}], "components": []}), encoding="utf-8")
    assert subprocess.run(_session_command("ensure", tmp_path, initial, state), check=False, timeout=20).returncode == 0
    request = tmp_path / "process.json"
    ready = tmp_path / "process.ready"
    go = tmp_path / "process.go"
    result = tmp_path / "process-result.json"
    release = tmp_path / "process.release"
    request.write_text(json.dumps({
        "file_path": sys._base_executable,
        "arguments": ["-c", "import time; time.sleep(60)"],
        "working_directory": str(tmp_path),
        "environment": {},
        "hidden": True,
        "stdout": "",
        "stderr": "",
    }), encoding="utf-8")
    helper = subprocess.Popen([
        sys.executable, str(PROCESS_HELPER), "--request", str(request), "--ready", str(ready),
        "--go", str(go), "--result", str(result), "--release", str(release), "--state", str(state),
    ])
    try:
        _wait(ready.exists)
        bootstrap_pid = int(json.loads(ready.read_text(encoding="utf-8"))["pid"])
        persisted = json.loads(state.read_text(encoding="utf-8"))
        job = SESSION.kernel32.OpenJobObjectW(SESSION.JOB_OBJECT_QUERY, False, persisted["job_name"])
        helper_handle = SESSION._open_process(bootstrap_pid)
        try:
            assigned = SESSION.wintypes.BOOL()
            assert SESSION.kernel32.IsProcessInJob(helper_handle, job, SESSION.ctypes.byref(assigned))
            assert assigned.value
        finally:
            SESSION.kernel32.CloseHandle(helper_handle)
            SESSION.kernel32.CloseHandle(job)
        attach_request = tmp_path / "attach-helper.json"
        attach_request.write_text(json.dumps({"owners": [], "components": [{"name": "bootstrap", "pid": bootstrap_pid}]}), encoding="utf-8")
        assert subprocess.run(_session_command("attach", tmp_path, attach_request, state), check=False, timeout=20).returncode == 0
        go.touch()
        _wait(result.exists)
        child_record = SESSION._process_record(int(json.loads(result.read_text(encoding="utf-8"))["pid"]), "child")
        persisted = json.loads(state.read_text(encoding="utf-8"))
        job = SESSION.kernel32.OpenJobObjectW(SESSION.JOB_OBJECT_QUERY, False, persisted["job_name"])
        child_handle = SESSION._open_process(int(child_record["pid"]))
        try:
            assigned = SESSION.wintypes.BOOL()
            assert SESSION.kernel32.IsProcessInJob(child_handle, job, SESSION.ctypes.byref(assigned))
            assert assigned.value
        finally:
            SESSION.kernel32.CloseHandle(child_handle)
            SESSION.kernel32.CloseHandle(job)
        supervisor_pid = int(json.loads(state.read_text(encoding="utf-8"))["supervisor"]["pid"])
        supervisor = subprocess.Popen([sys.executable, "-c", f"import os; os.kill({supervisor_pid}, 15)"])
        assert supervisor.wait(timeout=5) == 0
        helper.wait(timeout=10)
        _wait(lambda: not SESSION._same_process(child_record), timeout=15)
        state.unlink(missing_ok=True)
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait(timeout=5)


def test_process_helper_accepts_missing_working_directory():
    assert PROCESS._working_directory({}) is None
    assert PROCESS._working_directory({"working_directory": ""}) is None
    assert PROCESS._working_directory({"working_directory": r"C:\work"}) == r"C:\work"


def test_powershell_start_or_join_component_path_and_user_shell_survival(tmp_path):
    state = tmp_path / "project-session.json"
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    command = (
        f"$ErrorActionPreference='Stop'; $script:ProjectRoot='{ROOT}'; "
        f". '{ROOT / 'scripts' / 'session-common.ps1'}'; "
        f"$script:ProjectSessionStatePath='{state}'; "
        "$null=Start-OrJoin-ProjectSession -OwnerName 'test shell' -MonitorOwner -Components @(); "
        "$child=Start-Process -FilePath $env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe "
        "-ArgumentList @('-NoProfile','-Command','Start-Sleep -Seconds 60') -WindowStyle Hidden -PassThru; "
        "try { $null=Start-OrJoin-ProjectSession -Components @(@{name='wrapper child';pid=$child.Id}); "
        f"$s=Get-Content -Raw -LiteralPath '{state}' -Encoding UTF8|ConvertFrom-Json; "
        "if (@($s.components|Where-Object {$_.name -eq 'wrapper child'}).Count -ne 1) { exit 12 }; "
        "Stop-Process -Id $child.Id; $child.WaitForExit(); "
        f"$d=(Get-Date).AddSeconds(15); while((Test-Path -LiteralPath '{state}') -and (Get-Date)-lt $d){{Start-Sleep -Milliseconds 100}}; "
        f"if(Test-Path -LiteralPath '{state}'){{exit 13}}; Write-Output 'shell-survived' "
        "} finally { if(-not $child.HasExited){Stop-Process -Id $child.Id -ErrorAction SilentlyContinue} }"
    )
    completed = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT, capture_output=True, text=True, timeout=40,
    )
    assert completed.returncode == 0, completed.stderr
    assert "shell-survived" in completed.stdout


def test_powershell_managed_process_handshake_resumes_target_and_releases_bootstrap(tmp_path):
    state = tmp_path / "project-session.json"
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    command = (
        f"$ErrorActionPreference='Stop'; $script:ProjectRoot='{ROOT}'; "
        f". '{ROOT / 'scripts' / 'session-common.ps1'}'; $script:ProjectSessionStatePath='{state}'; "
        "$null=Start-OrJoin-ProjectSession -OwnerName 'managed test shell' -MonitorOwner -Components @(); "
        "$managed=Start-ManagedProjectProcess -Name 'managed target' -FilePath $env:SystemRoot\\System32\\PING.EXE "
        f"-ArgumentList @('-n','60','127.0.0.1') -WorkingDirectory '{tmp_path}' -Hidden; "
        "if($managed.HasExited){exit 21}; "
        f"$s=Get-Content -Raw -LiteralPath '{state}' -Encoding UTF8|ConvertFrom-Json; "
        "if(@($s.components|Where-Object {$_.name -like 'managed target bootstrap*'}).Count -ne 0){exit 22}; "
        "if(@($s.components|Where-Object {$_.name -eq 'managed target'}).Count -ne 1){exit 23}; "
        "Stop-Process -Id $managed.Id; $managed.WaitForExit(); "
        f"$d=(Get-Date).AddSeconds(15); while((Test-Path -LiteralPath '{state}') -and (Get-Date)-lt $d){{Start-Sleep -Milliseconds 100}}; "
        f"if(Test-Path -LiteralPath '{state}'){{exit 24}}; Write-Output 'managed-handshake-ok'"
    )
    completed = subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT, capture_output=True, text=True, timeout=45,
    )
    assert completed.returncode == 0, completed.stderr
    assert "managed-handshake-ok" in completed.stdout
