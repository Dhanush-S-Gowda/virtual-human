"""Sample backend/CV/local-LLM processes and Linux thread core placement.

Run from repository root: python -m backend.scripts.monitor_resources --seconds 30
CPU percent uses one logical CPU = 100%; multithreaded processes may exceed 100%.
No process affinity or scheduling policy is changed.
"""

import argparse
import csv
from datetime import datetime, timezone
import json
from math import isfinite
import os
from pathlib import Path
import time
from urllib.parse import urlparse

import psutil


ROOT = Path(__file__).resolve().parents[2]


def read_thread_location(pid, tid):
    """Linux stat field 39 is the CPU on which the thread last executed."""
    try:
        task = Path(f"/proc/{pid}/task/{tid}")
        fields = (task / "stat").read_text().rsplit(")", 1)[1].split()
        cpu = int(fields[36])
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        return {
            "last_logical_cpu": cpu,
            "physical_core": int((topology / "core_id").read_text()),
            "package": int((topology / "physical_package_id").read_text()),
            "name": (task / "comm").read_text().strip(),
        }
    except (OSError, ValueError, IndexError):
        return {"last_logical_cpu": None, "physical_core": None, "package": None, "name": None}


def discover_processes(settings):
    """Use listener ownership, not loose Python-process command-line matching."""
    llm = urlparse(settings.llm_url)
    llm_port = llm.port or (443 if llm.scheme == "https" else 80)
    roles = {settings.port: "backend", settings.cv.integration.websocket_port: "cv"}
    if llm.hostname in {"localhost", "127.0.0.1", "::1"}:
        roles[llm_port] = "llm"
    found = {}
    for connection in psutil.net_connections(kind="inet"):
        if connection.status != psutil.CONN_LISTEN or not connection.pid or not connection.laddr:
            continue
        role = roles.get(connection.laddr.port)
        if role:
            found[connection.pid] = role
    for pid, role in list(found.items()):
        try:
            for child in psutil.Process(pid).children(recursive=True):
                found.setdefault(child.pid, role + ".child")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return found


class ProcessSampler:
    def __init__(self, pid, role):
        self.process = psutil.Process(pid)
        self.role = role
        self.previous_threads = {}
        self.previous_time = None
        self.process.cpu_percent(None)  # Prime the first CPU interval.

    def sample(self, now):
        process = self.process
        with process.oneshot():
            memory = process.memory_info()
            threads = process.threads()
            cpu = process.cpu_percent(None)
            try:
                affinity = process.cpu_affinity()
            except (AttributeError, psutil.AccessDenied):
                affinity = None
            try:
                last_cpu = process.cpu_num()
            except (AttributeError, psutil.AccessDenied):
                last_cpu = None
            result = {
                "timestamp": datetime.now(timezone.utc).isoformat(), "monotonic": now,
                "role": self.role, "pid": process.pid, "name": process.name(),
                "cpu_percent": cpu, "rss_mib": memory.rss / 1024**2,
                "vms_mib": memory.vms / 1024**2, "thread_count": len(threads),
                "last_logical_cpu": last_cpu, "allowed_logical_cpus": affinity,
            }
        elapsed = now - self.previous_time if self.previous_time is not None else None
        current_threads, samples = {}, []
        for thread in threads:
            total = thread.user_time + thread.system_time
            current_threads[thread.id] = total
            previous = self.previous_threads.get(thread.id)
            percent = (max(0., total - previous) / elapsed * 100
                       if previous is not None and elapsed and elapsed > 0 else None)
            samples.append({"tid": thread.id, "cpu_percent": percent,
                            **read_thread_location(process.pid, thread.id)})
        self.previous_threads, self.previous_time = current_threads, now
        result["threads"] = samples
        return result


def accumulate(groups, sample):
    key = (sample["role"], sample["pid"])
    group = groups.setdefault(key, {"role": key[0], "pid": key[1], "samples": 0,
                                    "cpu_percent_sum": 0., "cpu_percent_peak": 0.,
                                    "rss_mib_peak": 0., "thread_count_peak": 0,
                                    "observed_active_logical_cpus": set()})
    group["samples"] += 1
    group["cpu_percent_sum"] += sample["cpu_percent"]
    group["cpu_percent_peak"] = max(group["cpu_percent_peak"], sample["cpu_percent"])
    group["rss_mib_peak"] = max(group["rss_mib_peak"], sample["rss_mib"])
    group["thread_count_peak"] = max(group["thread_count_peak"], sample["thread_count"])
    group["allowed_logical_cpus"] = sample["allowed_logical_cpus"]
    group["observed_active_logical_cpus"].update(
        thread["last_logical_cpu"] for thread in sample["threads"]
        if thread["cpu_percent"] is not None and thread["cpu_percent"] > 0
        and thread["last_logical_cpu"] is not None
    )


def summarize(groups):
    results = []
    for value in groups.values():
        group = dict(value)
        group["cpu_percent_mean"] = group.pop("cpu_percent_sum") / group["samples"]
        group["observed_active_logical_cpus"] = sorted(group["observed_active_logical_cpus"])
        results.append(group)
    return results


def run(seconds=30., interval=1., output=None, pids=()):
    from backend.config import settings

    if not isfinite(seconds) or not isfinite(interval) or seconds < 0 or interval <= 0:
        raise ValueError("Duration must be nonnegative and interval must be positive")
    if output:
        raise ValueError("File logging is disabled. Resource samples are exported to OpenObserve.")
    from backend.observability import emit, setup
    setup("resource_monitor")
    roles = discover_processes(settings)
    expected_roles = {"backend", "cv"}
    if urlparse(settings.llm_url).hostname in {"localhost", "127.0.0.1", "::1"}:
        expected_roles.add("llm")
    for item in pids:
        role, separator, pid = item.partition("=")
        if not separator:
            raise ValueError("Explicit processes use --pid role=PID")
        roles[int(pid)] = role
    if not roles:
        raise RuntimeError("No configured listeners found. Start the services or pass --pid role=PID")
    missing_roles = sorted(expected_roles - set(roles.values()))
    if missing_roles:
        print("No listener PID discovered for: " + ", ".join(missing_roles), flush=True)
    samplers = []
    for pid, role in roles.items():
        try:
            samplers.append(ProcessSampler(pid, role))
        except psutil.NoSuchProcess:
            pass
    # Establish thread baselines; do not report this first meaningless interval.
    started = time.monotonic()
    for sampler in samplers:
        sampler.sample(started)
    totals = {}
    try:
        while seconds == 0 or time.monotonic() - started < seconds:
            remaining = seconds - (time.monotonic() - started) if seconds else interval
            time.sleep(min(interval, max(0., remaining)))
            now = time.monotonic()
            for sampler in samplers:
                try:
                    sample = sampler.sample(now)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                accumulate(totals, sample)
                emit("resources", {**sample, "task": sample["role"],
                     "_timestamp": time.time_ns() // 1000, "event": "external_process_resource"})
    except KeyboardInterrupt:
        pass
    report = {"logical_cpus": psutil.cpu_count(), "physical_cores": psutil.cpu_count(logical=False),
              "duration_seconds": time.monotonic() - started,
              "cpu_percent_basis": "100 percent = one logical CPU",
              "processes": summarize(totals), "missing_listener_roles": missing_roles,
              "workload": "Passive sampling only; no inference requests are sent by this monitor",
              "limitations": ["Thread CPU is sampled; last CPU is not fixed affinity.",
                              "Native worker CPU is not automatically attributable to a Python module.",
                              "RSS includes shared pages; adding process RSS can double-count memory."]}
    emit("resources", {"_timestamp": time.time_ns() // 1000, "event": "resource_summary", "data": report})
    print("Resource measurements exported to OpenObserve")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=30., help="0 runs until Ctrl+C")
    parser.add_argument("--interval", type=float, default=1.)
    parser.add_argument("--output")
    parser.add_argument("--pid", action="append", default=[], help="Additional role=PID")
    args = parser.parse_args()
    run(args.seconds, args.interval, args.output, args.pid)
