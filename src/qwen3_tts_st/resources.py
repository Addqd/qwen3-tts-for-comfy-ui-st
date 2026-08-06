from __future__ import annotations

import csv
import io
import os
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

import psutil


@dataclass
class ResourceSnapshot:
    cpu_percent: float
    cpu_logical: int
    ram_total_mb: int
    ram_available_mb: int
    gpu_name: str | None = None
    gpu_total_vram_mb: int | None = None
    gpu_free_vram_mb: int | None = None
    gpu_used_vram_mb: int | None = None
    gpu_utilization_percent: int | None = None
    gpu_process_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _nvidia_snapshot(device: int) -> dict[str, Any]:
    command = [
        "nvidia-smi",
        f"--id={device}",
        "--query-gpu=name,memory.total,memory.free,memory.used,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=True)
    row = next(csv.reader(io.StringIO(result.stdout.strip()), skipinitialspace=True))
    processes = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    process_count = len([line for line in processes.stdout.splitlines() if line.strip()])
    return {
        "gpu_name": row[0].strip(),
        "gpu_total_vram_mb": int(row[1]),
        "gpu_free_vram_mb": int(row[2]),
        "gpu_used_vram_mb": int(row[3]),
        "gpu_utilization_percent": int(row[4]),
        "gpu_process_count": process_count,
    }


def snapshot(device: int = 0) -> ResourceSnapshot:
    memory = psutil.virtual_memory()
    values: dict[str, Any] = {
        "cpu_percent": psutil.cpu_percent(interval=0.15),
        "cpu_logical": os.cpu_count() or 1,
        "ram_total_mb": int(memory.total / 1024 / 1024),
        "ram_available_mb": int(memory.available / 1024 / 1024),
    }
    try:
        values.update(_nvidia_snapshot(device))
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, StopIteration):
        pass
    return ResourceSnapshot(**values)


def choose_mode(config: Any) -> tuple[str, str, ResourceSnapshot]:
    requested = str(config.get("resources.mode", "auto")).lower()
    current = snapshot(int(config.get("resources.gpu.device", 0)))
    if requested != "auto":
        return requested, f"режим явно задан: {requested}", current
    min_free = int(config.get("resources.gpu.minimum_free_vram_mb", 2500))
    safety = int(config.get("resources.gpu.safety_reserve_mb", 750))
    gpu_enabled = bool(config.get("resources.gpu.enabled", True))
    ram_reserve = int(config.get("resources.ram.safety_reserve_mb", 4096))
    if not gpu_enabled or current.gpu_free_vram_mb is None:
        return "cpu", "CUDA GPU недоступна или отключена; выбран CPU", current
    required = min_free + safety
    if current.gpu_free_vram_mb < required:
        return "cpu", f"свободно {current.gpu_free_vram_mb} MB VRAM, требуется безопасно не менее {required} MB", current
    if current.ram_available_mb < ram_reserve:
        return "cuda_on_demand", f"RAM ниже резерва {ram_reserve} MB; выбран изолированный CUDA on demand", current
    if (current.gpu_process_count or 0) > 0 and bool(config.get("resources.gpu.reject_shared_memory_pressure", True)):
        return "cuda_on_demand", "обнаружены GPU-процессы внешней нагрузки; выбран CUDA on demand", current
    return "cuda", f"доступно {current.gpu_free_vram_mb} MB VRAM; выбран постоянный CUDA worker", current

