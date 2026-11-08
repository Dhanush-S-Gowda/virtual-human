"""Host and optional GPU measurements; model work never depends on these probes."""

import csv
import importlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import time

import psutil


MIB = 1024 ** 2


def host_samples():
    stamp = time.time_ns() // 1000
    common = {"_timestamp": stamp, "event": "host_resource", "resource_scope": "host"}
    cpus = psutil.cpu_percent(interval=None, percpu=True)
    yield {**common, "task": "host", "host_cpu_percent": sum(cpus) / len(cpus) if cpus else None,
           "host_ram_percent": psutil.virtual_memory().percent}
    for index, value in enumerate(cpus):
        yield {**common, "task": f"CPU {index}", "core_cpu_percent": value}
    try:
        pressure = Path("/proc/pressure/cpu").read_text().splitlines()
        some = next(line for line in pressure if line.startswith("some "))
        avg10 = dict(item.split("=", 1) for item in some.split()[1:])["avg10"]
        yield {**common, "task": "CPU scheduling pressure", "cpu_pressure_percent": float(avg10)}
    except (OSError, ValueError, KeyError, StopIteration):
        pass


def process_roles(process, role, external):
    """Attribute only known processes and descendants, never guess from a model name."""
    roles = {process.pid: role}
    for pid, (task, child) in external.items():
        roles[pid] = task
        try:
            roles.update({item.pid: task for item in child.children(recursive=True)})
        except psutil.Error:
            pass
    return roles


def _number(value):
    try:
        result = float(value)
        return result if 0 <= result < 2 ** 63 else None
    except (TypeError, ValueError):
        return None


class GPUCollector:
    """Prefer NVML, fall back to the driver CLI; DRM is used for non-NVIDIA clients."""

    def __init__(self):
        self.nvml = None
        self.cli = shutil.which("nvidia-smi")
        if not self.cli and os.name == "nt":
            candidate = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/nvidia-smi.exe"
            if candidate.is_file():
                self.cli = str(candidate)
        self.engine_previous = {}
        try:
            module = importlib.import_module("pynvml")
            module.nvmlInit()
            self.nvml = module
        except (ImportError, OSError, RuntimeError):
            pass
        except Exception:
            # NVML raises its own errors when the driver/device is absent.
            pass

    def close(self):
        if self.nvml:
            try:
                self.nvml.nvmlShutdown()
            except Exception:
                pass

    def sample(self, roles, *, devices=True):
        common = {"_timestamp": time.time_ns() // 1000, "event": "gpu_resource"}
        try:
            if self.nvml:
                rows = self._nvml_samples(roles, devices)
            elif self.cli:
                rows = self._cli_samples(roles, devices)
            else:
                rows = self._drm_samples(roles)
            if rows:
                return [{**common, **row} for row in rows]
            return [{**common, "task": "GPU", "resource_scope": "device", "gpu_status": "unavailable",
                     "gpu_detail": "No supported GPU statistics are visible to this process."}] if devices else []
        except Exception as exc:
            return [{**common, "task": "GPU", "resource_scope": "device", "gpu_status": "unavailable",
                     "gpu_detail": type(exc).__name__}] if devices else []

    def _nvml_samples(self, roles, devices):
        n = self.nvml
        rows = []
        for index in range(n.nvmlDeviceGetCount()):
            device = n.nvmlDeviceGetHandleByIndex(index)
            name, identifier = n.nvmlDeviceGetName(device), n.nvmlDeviceGetUUID(device)
            name = name.decode() if isinstance(name, bytes) else name
            identifier = identifier.decode() if isinstance(identifier, bytes) else identifier
            base = {"gpu_id": identifier, "gpu_name": name, "gpu_vendor": "NVIDIA"}
            if devices:
                row = {**base, "task": name, "resource_scope": "device", "gpu_status": "available"}
                try:
                    row["gpu_busy_percent"] = n.nvmlDeviceGetUtilizationRates(device).gpu
                except Exception:
                    row["gpu_status"] = "partial"
                try:
                    memory = n.nvmlDeviceGetMemoryInfo(device)
                    row.update(vram_used_mib=memory.used / MIB, vram_total_mib=memory.total / MIB,
                               vram_used_percent=memory.used / memory.total * 100 if memory.total else None)
                except Exception:
                    row["gpu_status"] = "partial"
                rows.append(row)
            processes = {}
            for function in ("nvmlDeviceGetComputeRunningProcesses", "nvmlDeviceGetGraphicsRunningProcesses"):
                try:
                    for process in getattr(n, function)(device):
                        value = _number(process.usedGpuMemory)
                        if value is not None:
                            processes[process.pid] = max(value, processes.get(process.pid) or 0.)
                        elif process.pid not in processes:
                            processes[process.pid] = value
                except Exception:
                    continue
            for pid, memory in processes.items():
                if pid in roles:
                    rows.append({**base, "task": roles[pid], "pid": pid, "resource_scope": "process_gpu",
                                 "process_vram_mib": memory / MIB if memory is not None else None,
                                 "gpu_status": "available" if memory is not None else "unsupported_process_memory"})
        return rows

    def _query_cli(self, query):
        result = subprocess.run([self.cli, query, "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=1.5, check=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return list(csv.reader(io.StringIO(result.stdout), skipinitialspace=True))

    def _cli_samples(self, roles, devices):
        rows = []
        names = {}
        for identifier, name, busy, used, total in self._query_cli(
                "--query-gpu=uuid,name,utilization.gpu,memory.used,memory.total"):
            names[identifier] = name
            if devices:
                used, total = _number(used), _number(total)
                rows.append({"task": name, "gpu_name": name, "gpu_id": identifier, "gpu_vendor": "NVIDIA",
                             "resource_scope": "device", "gpu_status": "available" if _number(busy) is not None and used is not None and total else "partial",
                             "gpu_busy_percent": _number(busy), "vram_used_mib": used, "vram_total_mib": total,
                             "vram_used_percent": used / total * 100 if used is not None and total else None})
        try:
            for identifier, pid, used in self._query_cli("--query-compute-apps=gpu_uuid,pid,used_gpu_memory"):
                pid = int(pid)
                if pid in roles:
                    used = _number(used)
                    rows.append({"task": roles[pid], "pid": pid, "gpu_id": identifier,
                                 "gpu_name": names.get(identifier, identifier), "gpu_vendor": "NVIDIA",
                                 "resource_scope": "process_gpu", "process_vram_mib": used,
                                 "gpu_status": "available" if used is not None else "unsupported_process_memory"})
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return rows

    def _drm_samples(self, roles):
        rows, seen, current = [], set(), {}
        now = time.monotonic()
        for pid, task in roles.items():
            try:
                files = list(Path(f"/proc/{pid}/fdinfo").iterdir())
            except OSError:
                continue
            for path in files:
                try:
                    fields = dict(line.split(":", 1) for line in path.read_text().splitlines() if ":" in line)
                    fields = {key: value.strip() for key, value in fields.items()}
                except (OSError, ValueError):
                    continue
                client = fields.get("drm-client-id")
                driver = fields.get("drm-driver")
                if not client or driver == "nvidia" or not driver:
                    continue
                device = fields.get("drm-pdev", driver)
                identity = (device, client)
                if identity in seen:
                    continue
                seen.add(identity)
                base = {"pid": pid, "gpu_id": device, "gpu_name": driver, "gpu_vendor": driver,
                        "resource_scope": "process_gpu", "gpu_status": "partial"}
                for key, value in fields.items():
                    if key.startswith("drm-engine-") and not key.startswith("drm-engine-capacity-"):
                        engine = key.removeprefix("drm-engine-")
                        counter = int(value.split()[0])
                        ident = (*identity, engine)
                        previous = self.engine_previous.get(ident)
                        current[ident] = (now, max(counter, previous[1]) if previous else counter)
                        if previous and now > previous[0]:
                            capacity = int(fields.get("drm-engine-capacity-" + engine, "1"))
                            busy = max(0, counter - previous[1]) / ((now - previous[0]) * 1e9 * max(1, capacity)) * 100
                            rows.append({**base, "task": task + " / " + engine, "gpu_engine": engine,
                                         "gpu_engine_percent": min(100., busy)})
                    elif key.startswith("drm-resident-"):
                        amount, unit = value.split()[:2]
                        mib = int(amount) / 1024 if unit == "KiB" else int(amount)
                        region = key.removeprefix("drm-resident-")
                        # Resident/shared system memory is not dedicated VRAM.
                        rows.append({**base, "task": task + " / " + region, "memory_region": region,
                                     "gpu_resident_mib": mib})
        self.engine_previous = current
        return rows
