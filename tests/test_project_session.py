from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object lifecycle")
ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "scripts" / "project-session.py"


def _wait(predicate, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition was not reached before timeout")


def _sleeping_process() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def test_component_exit_terminates_the_entire_project_job(tmp_path):
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
