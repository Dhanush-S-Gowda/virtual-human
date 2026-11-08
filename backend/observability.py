"""Bounded, asynchronous OpenObserve transport. No file logger or disk spool."""

import atexit
from collections import defaultdict
import json
import logging
import os
from pathlib import Path
import queue
import socket
import sys
import threading
import time
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
import psutil


load_dotenv(Path(__file__).with_name(".env"))
STREAMS = {"cv": "vh_cv", "stages": "vh_stages", "conversation": "vh_conversation",
           "llm": "vh_llm", "stt": "vh_stt", "tts": "vh_tts",
           "resources": "vh_resources", "application": "vh_application", "performance": "vh_performance"}


def enabled():
    return not _transport_disabled and os.getenv("OPENOBSERVE_ENABLED", "true").lower() not in {"0", "false", "no"}


def credentials():
    token = os.getenv("OPENOBSERVE_TOKEN")
    if token:
        return {"headers": {"Authorization": "Basic " + token}}
    user, password = os.getenv("OPENOBSERVE_USER"), os.getenv("OPENOBSERVE_PASSWORD")
    if not user or not password:
        raise ValueError("Set OPENOBSERVE_USER and OPENOBSERVE_PASSWORD in backend/.env")
    return {"auth": (user, password)}


def redact(value):
    if isinstance(value, dict):
        return {key: ("[redacted]" if str(key).lower() in {
            "authorization", "api_key", "password", "token", "llm_api_key"} else redact(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        for key in ("OPENOBSERVE_PASSWORD", "OPENOBSERVE_TOKEN", "LLM_API_KEY", "TTS_API_KEY"):
            secret = os.getenv(key)
            if secret:
                value = value.replace(secret, "[redacted]")
    return value


class Exporter:
    def __init__(self, client=None):
        self.url = os.getenv("OPENOBSERVE_URL", "http://localhost:5080").rstrip("/")
        self.org = os.getenv("OPENOBSERVE_ORG", "default")
        self.queue = queue.Queue(maxsize=512)
        self.stop_event = threading.Event()
        self.client = client
        self.dropped = 0
        self.disabled = False
        self.disabled_reason = None
        self.thread = threading.Thread(target=self._run, name="openobserve-export", daemon=True)
        self.thread.start()

    def submit(self, kind, payload):
        if self.disabled:
            return
        try:
            self.queue.put_nowait((STREAMS.get(kind, STREAMS["application"]),
                {"host": socket.gethostname(), "experiment_id": os.getenv("VH_EXPERIMENT_ID", "default"),
                 "phase": os.getenv("VH_MEASUREMENT_PHASE", "measured"),
                 **redact(payload), "exporter_dropped_total": self.dropped}))
        except queue.Full:
            self.dropped += 1

    def _run(self):
        try:
            client = self.client or httpx.Client(timeout=3)
            if self.client is None:
                health = client.get(f"{self.url}/healthz", timeout=1)
                health.raise_for_status()
                auth = credentials()
                if "auth" in auth:
                    client.auth = auth["auth"]
                else:
                    client.headers.update(auth["headers"])
        except Exception as exc:
            if self.client is None and "client" in locals():
                client.close()
            self._disable(type(exc).__name__)
            return
        try:
            while not self.stop_event.is_set() or not self.queue.empty():
                batch = defaultdict(list)
                try:
                    stream, payload = self.queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                batch[stream].append(payload)
                for _ in range(99):
                    try:
                        stream, payload = self.queue.get_nowait()
                        batch[stream].append(payload)
                    except queue.Empty:
                        break
                for stream, entries in batch.items():
                    for attempt in range(3):
                        try:
                            response = client.post(f"{self.url}/api/{self.org}/{stream}/_json", json=entries)
                            response.raise_for_status()
                            body = response.json()
                            if any(item.get("failed", 0) for item in body.get("status", []) if isinstance(item, dict)):
                                raise ValueError("OpenObserve rejected records")
                            break
                        except (httpx.HTTPError, ValueError, TypeError):
                            if attempt == 2:
                                self.dropped += len(entries)
                                self._disable("ingestion failed after three attempts")
                                return
                            elif self.stop_event.wait(0.2 * (attempt + 1)):
                                self.dropped += len(entries)
                                break
        finally:
            if self.client is None:
                client.close()

    def _disable(self, reason):
        self.disabled = True
        self.disabled_reason = reason
        while True:
            try:
                self.queue.get_nowait()
                self.dropped += 1
            except queue.Empty:
                break
        print("OpenObserve is not installed, not running, unreachable, or not configured "
              f"at {self.url} ({reason}). Remote logging is disabled; all other application "
              "processes continue. No local log files will be written. Restart after enabling OpenObserve.",
              file=sys.stderr)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=5)


_exporter = None
_lock = threading.Lock()
_transport_disabled = False


def emit(kind, payload):
    global _exporter, _transport_disabled
    if not enabled():
        return
    try:
        with _lock:
            if _exporter is None:
                _exporter = Exporter()
                atexit.register(_exporter.close)
        _exporter.submit(kind, payload)
    except Exception:
        _transport_disabled = True
        print("OpenObserve logging could not start and is disabled; all other application processes continue.", file=sys.stderr)


class OpenObserveHandler(logging.Handler):
    def emit(self, record):
        if record.name.startswith(("httpx", "httpcore")) and threading.current_thread().name == "openobserve-export":
            return
        try:
            emit("application", {"_timestamp": int(record.created * 1e6), "event": "log",
                 "level": record.levelname, "logger": record.name, "message": record.getMessage(),
                 "pid": os.getpid(), "service": os.getenv("VH_PROCESS_ROLE", "application"),
                 "exception": self.formatter.formatException(record.exc_info) if record.exc_info else None})
        except Exception:
            self.handleError(record)


def setup(role):
    os.environ["VH_PROCESS_ROLE"] = role
    if not enabled():
        return
    root = logging.getLogger()
    if not any(isinstance(handler, OpenObserveHandler) for handler in root.handlers):
        handler = OpenObserveHandler()
        handler.setFormatter(logging.Formatter())
        root.addHandler(handler)
    emit("application", {"_timestamp": time.time_ns() // 1000, "event": "service_start",
                         "service": role, "pid": os.getpid()})
    # File logging is explicitly disabled for application-owned handlers.
    for logger in [root, *(item for item in logging.Logger.manager.loggerDict.values() if isinstance(item, logging.Logger))]:
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler):
                logger.removeHandler(handler)
                handler.close()


def shutdown():
    if _exporter is not None:
        _exporter.close()


def resource_sample(process, role):
    memory = process.memory_info()
    sample = {"_timestamp": time.time_ns() // 1000, "event": "process_resource", "task": role,
              "pid": process.pid, "cpu_percent": process.cpu_percent(None), "rss_mib": memory.rss / 1024**2,
              "thread_count": process.num_threads(), "memory_scope": "dedicated_process"}
    try:
        full = process.memory_full_info()
        sample["uss_mib"] = full.uss / 1024**2
        if hasattr(full, "pss"):
            sample["pss_mib"] = full.pss / 1024**2
    except (psutil.Error, AttributeError):
        pass
    return sample


class ResourceReporter:
    def __init__(self, role):
        self.role = role
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="resource-reporter", daemon=True)
        if enabled():
            try:
                self.thread.start()
            except RuntimeError:
                print("OpenObserve resource reporting could not start; application processes continue.", file=sys.stderr)

    def _run(self):
        try:
            from .resource_metrics import GPUCollector, host_samples, process_roles
        except ImportError:
            from resource_metrics import GPUCollector, host_samples, process_roles
        process = psutil.Process()
        process.cpu_percent(None)
        host_collector = self.role == "conversation_api"
        if host_collector:
            psutil.cpu_percent(interval=None, percpu=True)
        gpu = GPUCollector()
        external = {}
        ticks = 0
        try:
            while not self.stop_event.wait(2):
                if _exporter is not None and _exporter.disabled:
                    return
                try:
                    emit("resources", resource_sample(process, self.role))
                    if host_collector:
                        for sample in host_samples():
                            emit("resources", sample)
                        if ticks % 5 == 0:
                            external = self._external_processes(external)
                        for pid, (task, service_process) in list(external.items()):
                            try:
                                sample = resource_sample(service_process, task)
                                sample["memory_scope"] = "external_listener_process"
                                emit("resources", sample)
                            except psutil.Error:
                                external.pop(pid, None)
                        ticks += 1
                    for sample in gpu.sample(process_roles(process, self.role, external),
                                             devices=host_collector):
                        emit("resources", sample)
                except (psutil.Error, OSError, ValueError):
                    continue
        finally:
            gpu.close()

    @staticmethod
    def _external_processes(previous):
        try:
            try:
                from .config import settings
            except ImportError:
                from config import settings
            ports = {}
            for task, name in (("llm.server", "llm_url"), ("stt.server", "nemotron_url"), ("tts.server", "tts_url")):
                if task.startswith("stt") and settings.stt_provider != "nemotron":
                    continue
                if task.startswith("tts") and settings.tts_provider != "kokoro":
                    continue
                parsed = urlparse(getattr(settings, name, ""))
                if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
                    provider = settings.stt_provider if task.startswith("stt") else settings.tts_provider if task.startswith("tts") else settings.llm_model
                    ports[parsed.port or (443 if parsed.scheme == "https" else 80)] = task.replace(".server", "." + provider + ".server")
            found = {}
            for item in psutil.net_connections(kind="inet"):
                if item.status == psutil.CONN_LISTEN and item.pid and item.laddr.port in ports:
                    if item.pid in previous:
                        found[item.pid] = previous[item.pid]
                    else:
                        service_process = psutil.Process(item.pid)
                        service_process.cpu_percent(None)
                        found[item.pid] = (ports[item.laddr.port], service_process)
            return found
        except (psutil.Error, ValueError):
            return previous

    def close(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=3)
