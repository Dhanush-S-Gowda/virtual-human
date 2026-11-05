"""Correlated OpenObserve events and worker spans; no local log files."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import logging
import os
from pathlib import Path
import threading
import time
import uuid

try:
    from .observability import emit
except ImportError:
    from observability import emit

try:
    from .config import settings
except ImportError:
    from config import settings


context = ContextVar("diagnostic_context", default={})


def current_cpu(tid=None):
    try:
        tid = tid or threading.get_native_id()
        fields = Path(f"/proc/self/task/{tid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[36])
    except (OSError, ValueError, IndexError):
        return None



def record(kind, event, **fields):
    if not getattr(settings, "diagnostics_enabled", False):
        return
    try:
        payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event,
                   "pid": os.getpid(), "tid": threading.get_native_id(),
                   **context.get(), **fields}
        payload["_timestamp"] = time.time_ns() // 1000
        payload["service"] = os.getenv("VH_PROCESS_ROLE", "application")
        emit(kind, payload)
    except (OSError, TypeError, ValueError):
        # Diagnostics must not break model work if storage is unavailable.
        logging.getLogger(__name__).warning("Unable to export %s diagnostic event", kind)


@contextmanager
def scope(**values):
    token = context.set({**context.get(), **values})
    try:
        yield
    finally:
        context.reset(token)


@contextmanager
def stage(name, **fields):
    if not getattr(settings, "diagnostics_enabled", False):
        yield
        return
    started, cpu_started = time.perf_counter(), time.thread_time()
    first_cpu = current_cpu()
    status = "ok"
    record("stages", "start", stage=name, logical_cpu=first_cpu, **fields)
    try:
        yield
    except BaseException:
        status = "error_or_cancelled"
        raise
    finally:
        record("stages", "end", stage=name, status=status,
               duration_ms=round((time.perf_counter() - started) * 1000, 3),
               calling_thread_cpu_ms=round((time.thread_time() - cpu_started) * 1000, 3),
               start_logical_cpu=first_cpu, end_logical_cpu=current_cpu(), **fields)


def traced_stage(name):
    """Wrap synchronous work INSIDE to_thread so the recorded TID is its worker."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            frame = args[1] if len(args) > 1 and hasattr(args[1], "frame_id") else None
            fields = {"frame_id": frame.frame_id, "capture_timestamp": frame.timestamp} if frame else {}
            with stage(name, **fields):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def bind_session(session_id):
    context.set({**context.get(), "session_id": session_id})


def traced_turn(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        with scope(turn_id=str(uuid.uuid4())):
            return await function(*args, **kwargs)
    return wrapped
