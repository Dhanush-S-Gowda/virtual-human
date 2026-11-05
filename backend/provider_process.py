"""One persistent spawned process per local model provider, with serialized RPC."""

import asyncio
import importlib
import inspect
import multiprocessing
import os
import threading

try:
    from .diagnostics import context, record
    from .observability import ResourceReporter, setup, shutdown
except ImportError:
    from diagnostics import context, record
    from observability import ResourceReporter, setup, shutdown


def _serve(connection, module_name, class_name, args, kwargs, role):
    setup(role)
    reporter = ResourceReporter(role)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    provider = None
    try:
        provider = getattr(importlib.import_module(module_name), class_name)(*args, **kwargs)
        while True:
            method, parameters, values = connection.recv()
            token = context.set(values)
            try:
                function = getattr(provider, method, None)
                result = function(*parameters) if function else None
                if inspect.isawaitable(result):
                    result = loop.run_until_complete(result)
                connection.send((True, result))
            except Exception as exc:
                record("application", "provider_error", task=role, method=method,
                       error_type=type(exc).__name__, error=str(exc))
                connection.send((False, f"{role}.{method}: {type(exc).__name__}: {exc}"))
            finally:
                context.reset(token)
            if method == "close":
                break
    except (EOFError, BrokenPipeError):
        pass
    finally:
        reporter.close()
        connection.close()
        loop.close()
        shutdown()


class ProviderProcess:
    """Cancellation abandons the wait, not the RPC receive; subsequent calls stay ordered."""

    def __init__(self, module, name, *args, role, **kwargs):
        self.module, self.name = module, name
        self.args, self.kwargs, self.role = args, kwargs, role
        self.process = None
        self.connection = None
        self.lock = threading.Lock()
        self.closing = False
        self._prune_task = None

    def _exchange(self, method, parameters, values):
        with self.lock:
            if self.process is None:
                if method == "close":
                    return None
                spawn = multiprocessing.get_context("spawn")
                self.connection, child = spawn.Pipe()
                self.process = spawn.Process(target=_serve, args=(child, self.module, self.name,
                    self.args, self.kwargs, self.role), name=f"model-{self.role}", daemon=True)
                self.process.start()
                child.close()
            if not self.process.is_alive():
                if method == "close":
                    return None
                raise RuntimeError(f"{self.role} provider process is not running")
            self.connection.send((method, parameters, values))
            # poll detects a crashed child instead of blocking recv indefinitely.
            while not self.connection.poll(0.2):
                if not self.process.is_alive():
                    raise RuntimeError(f"{self.role} provider process exited during {method}")
            success, result = self.connection.recv()
            if not success:
                raise RuntimeError(result)
            return result

    async def _call(self, method, *parameters):
        if self.closing and method != "close":
            raise RuntimeError(f"{self.role} provider is closing")
        return await asyncio.to_thread(self._exchange, method, parameters, dict(context.get()))

    async def initialize(self): return await self._call("initialize")
    async def detect(self, *args): return await self._call("detect", *args)
    async def recognize(self, *args): return await self._call("recognize", *args)
    async def reset(self): return await self._call("reset")
    async def forget_track(self, *args): return await self._call("forget_track", *args)
    async def rename_track(self, *args): return await self._call("rename_track", *args)
    async def handle_transcript(self, *args): return await self._call("handle_transcript", *args)
    async def transcribe(self, *args): return await self._call("transcribe", *args)
    async def synthesize_segment(self, *args): return await self._call("synthesize_segment", *args)

    def prune(self, tracks):
        # CV ticks must never wait behind model inference or enqueue unbounded pruning jobs.
        if self.closing or (self._prune_task and not self._prune_task.done()):
            return
        async def run():
            try:
                await self._call("prune", tracks)
            except RuntimeError:
                pass
        self._prune_task = asyncio.create_task(run())

    async def close(self):
        self.closing = True
        try:
            await asyncio.wait_for(self._call("close"), timeout=15)
        finally:
            if self.process and self.process.pid is not None:
                await asyncio.to_thread(self.process.join, 2)
                if self.process.is_alive():
                    self.process.terminate()
                    await asyncio.to_thread(self.process.join, 2)
                self.connection.close()
