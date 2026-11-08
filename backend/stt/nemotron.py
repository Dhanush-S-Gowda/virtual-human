"""Live PCM16 client for NeMo-Speech.cpp's transcription WebSocket."""

import asyncio
import json
from time import perf_counter
from io import BytesIO
from urllib.parse import urlsplit

import numpy as np
from websockets.asyncio.client import connect

from config import settings


class NemotronStream:
    def __init__(self, socket, timeout, language, sample_rate=16000, on_partial=None):
        self.socket = socket
        self.timeout = timeout
        self.language = language
        self.segments = []
        self.commit_requested = False
        self.sample_rate = sample_rate
        self.started = perf_counter()
        self.audio_bytes = 0
        self.first_partial_ms = None
        self.commit_started = None
        self.partial_text = ""
        self.on_partial = on_partial
        self.receiver = asyncio.create_task(self._receive())

    async def _receive(self):
        while True:
            event = json.loads(await self.socket.recv())
            kind = event.get("type", "")
            if kind == "error":
                raise ValueError(f"Nemotron: {event.get('message', event.get('error', 'server error'))}")
            if kind == "conversation.item.input_audio_transcription.delta":
                if self.first_partial_ms is None:
                    self.first_partial_ms = round((perf_counter() - self.started) * 1000, 2)
                if isinstance(event.get("transcript"), str):
                    self.partial_text = event["transcript"]
                elif isinstance(event.get("delta"), str):
                    self.partial_text += event["delta"]
                if self.on_partial is not None:
                    await self.on_partial(self.partial_text)
            if kind == "conversation.item.input_audio_transcription.completed":
                text = str(event.get("transcript", event.get("text", ""))).strip()
                if text:
                    self.segments.append(text)
            elif kind == "input_audio_buffer.committed":
                # NeMo sends this after stream.finish() and all final events.
                # Silence may produce no completed event at all.
                if not self.commit_requested:
                    raise ValueError("Nemotron acknowledged an unexpected audio commit.")
                return {"text": " ".join(self.segments), "language": self.language,
                        "stream_metrics": {"first_partial_ms": self.first_partial_ms,
                                           "audio_duration_ms": self.audio_bytes / (2 * self.sample_rate) * 1000,
                                           "finalization_ms": round((perf_counter() - self.commit_started) * 1000, 2)}}
            # Partial text is display-only; only committed text reaches the LLM.

    async def send_audio(self, chunk):
        if self.receiver.done():
            self.receiver.result()  # Surface a server error immediately.
            raise ValueError("Nemotron closed the transcription session early.")
        if len(chunk) % 2:
            raise ValueError("PCM16 audio must contain complete 16-bit samples.")
        await self.socket.send(chunk)
        self.audio_bytes += len(chunk)

    async def finish(self):
        self.commit_requested = True
        self.commit_started = perf_counter()
        await self.socket.send(json.dumps({"type": "input_audio_buffer.commit"}))
        try:
            return await asyncio.wait_for(asyncio.shield(self.receiver), self.timeout)
        except TimeoutError as exc:
            raise ValueError("Nemotron timed out waiting for the completed transcript.") from exc

    async def close(self):
        self.receiver.cancel()
        await asyncio.gather(self.receiver, return_exceptions=True)
        await self.socket.close()


class NemotronProvider:
    async def initialize(self):
        """The external server owns model loading; connections are per recording."""

    async def open_stream(self, sample_rate, on_partial=None):
        if not 8000 <= sample_rate <= 96000:
            raise ValueError("Nemotron sample rate must be between 8000 and 96000 Hz.")
        headers = ({"Authorization": f"Bearer {settings.nemotron_api_key}"}
                   if settings.nemotron_api_key else None)
        try:
            socket = await connect(settings.nemotron_url, additional_headers=headers,
                                   open_timeout=settings.nemotron_timeout, close_timeout=2)
        except OSError as exc:
            address = urlsplit(settings.nemotron_url)
            host = address.hostname or "the configured address"
            port = f":{address.port}" if address.port is not None else ""
            raise ValueError(
                f"Cannot connect to the Nemotron speech server at {host}{port}. "
                "Start your nemo-speech server, or check nemotron_url in backend/config.yaml."
            ) from exc
        try:
            async with asyncio.timeout(settings.nemotron_timeout):
                event = json.loads(await socket.recv())
                if event.get("type") != "session.created":
                    raise ValueError(f"Nemotron did not create a session: {event.get('message', event)}")
                await socket.send(json.dumps({"type": "session.update", "session": {
                    "sample_rate": sample_rate, "language": settings.nemotron_language,
                    "model": settings.nemotron_model,
                }}))
                event = json.loads(await socket.recv())
                if event.get("type") != "session.updated":
                    raise ValueError(f"Nemotron rejected session settings: {event.get('message', event)}")
            return NemotronStream(socket, settings.nemotron_timeout, settings.nemotron_language,
                                  sample_rate, on_partial)
        except BaseException:
            await socket.close()
            raise

    async def transcribe(self, audio, content_type):
        """Support existing encoded-recording clients through the same socket."""
        from faster_whisper.audio import decode_audio

        waveform = await asyncio.to_thread(decode_audio, BytesIO(audio), sampling_rate=16000)
        pcm = (np.clip(waveform, -1, 1) * 32767).astype("<i2").tobytes()
        stream = await self.open_stream(16000)
        try:
            for offset in range(0, len(pcm), 3200):
                await stream.send_audio(pcm[offset:offset + 3200])
            return await stream.finish()
        finally:
            await stream.close()
