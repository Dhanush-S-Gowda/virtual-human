import asyncio
import csv
import json
import logging
import os
import re
import threading
from collections import OrderedDict, deque
from collections.abc import AsyncIterator
from pathlib import Path
import sys
from time import perf_counter

_BACKEND_DIR = str(Path(__file__).resolve().parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from config import settings

logger = logging.getLogger(__name__)
_METRICS_FILE = Path(
    os.environ.get("METRICS_CSV_PATH", Path(__file__).with_name("metrics.csv"))
)
_METRICS_LOCK = threading.Lock()
_METRICS_FIELDS = [
    "stt_provider", "stt_model", "llm_provider", "llm_model",
    "tts_provider", "tts_model", "stt_total_ms", "llm_ttft_ms",
    "llm_total_ms", "llm_tokens_per_second", "tts_ttfb_ms",
    "first_speech_end_to_end_ms", "end_to_end_ms", "audio_bytes",
    "tts_segments", "status",
]


def _append_metrics_csv(metrics: dict) -> None:
    """Append one quantitative session summary to the backend metrics CSV."""
    row = {field: metrics.get(field) for field in _METRICS_FIELDS}
    with _METRICS_LOCK:
        file_exists = _METRICS_FILE.exists()
        with _METRICS_FILE.open("a", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=_METRICS_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)


def _model_details():
    return {
        "stt_provider": settings.stt_provider,
        "stt_model": settings.faster_whisper_model,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "tts_provider": settings.tts_provider,
        "tts_model": "kokoro" if settings.tts_provider == "kokoro" else "supertonic-3",
    }


_SCENE_CACHE: dict = {"context": None, "time": 0.0}


def _format_scene_context(scene: dict) -> str | None:
    """Format visual scene data into a concise context prompt string."""
    people = scene.get("people", [])
    objects = scene.get("objects", [])
    speaker = scene.get("active_speaker")
    parts = []

    # If active speaker is missing or Unknown, check if there's any identified person in people
    identified_people = [
        p for p in people
        if p.get("display_name") and p.get("display_name") != "Unknown"
    ]
    if (not speaker or not speaker.get("display_name") or speaker.get("display_name") == "Unknown") and identified_people:
        speaker = identified_people[0]

    if speaker and speaker.get("display_name"):
        name = speaker.get("display_name")
        tid = speaker.get("track_id")
        if name and name != "Unknown":
            parts.append(f"Person currently speaking to you: {name} (Track #{tid})")
        else:
            parts.append(f"Person currently speaking to you: Unknown Person (Track #{tid})")
    elif identified_people:
        name = identified_people[0].get("display_name")
        tid = identified_people[0].get("track_id")
        parts.append(f"Person currently speaking to you: {name} (Track #{tid})")

    if people:
        people_strs = [
            f"{p.get('display_name') or 'Unknown'} (Track #{p.get('track_id')})"
            for p in people
        ]
        parts.append(f"Visible people in frame: {', '.join(people_strs)}")
    else:
        parts.append("No people visible")

    if objects:
        obj_strs = [o.get("label", "") for o in objects if o.get("label")]
        if obj_strs:
            parts.append(f"Visible objects: {', '.join(obj_strs)}")

    return "; ".join(parts) if parts else None


def _is_ws_available(ws_url: str, timeout: float = 0.05) -> bool:
    try:
        import socket
        import urllib.parse
        parsed = urllib.parse.urlparse(ws_url)
        host = "127.0.0.1" if parsed.hostname in ("localhost", "127.0.0.1") else parsed.hostname
        port = parsed.port or 80
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


async def _get_scene_context(client_scene: dict | None = None) -> str | None:
    """Query current visual scene state from CV adapter with caching and fallback."""
    now = perf_counter()
    if now - _SCENE_CACHE["time"] < 1.0 and _SCENE_CACHE["context"] is not None:
        return _SCENE_CACHE["context"]

    cv_url = getattr(settings, "cv_adapter_url", None)
    if cv_url:
        cv_url = cv_url.replace("ws://localhost:", "ws://127.0.0.1:")
        if _is_ws_available(cv_url):
            try:
                import websockets
                async with websockets.connect(cv_url, open_timeout=0.2, close_timeout=0.2) as ws:
                    await ws.send(json.dumps({"type": "get_scene"}))
                    resp = await asyncio.wait_for(ws.recv(), timeout=0.2)
                    data = json.loads(resp)
                    scene = data.get("data", {})
                    context_str = _format_scene_context(scene)
                    if context_str:
                        _SCENE_CACHE["context"] = context_str
                        _SCENE_CACHE["time"] = now
                        return context_str
            except Exception as exc:
                logger.debug("CV adapter scene query skipped: %s", exc)

    # Fallback to client-provided scene if available
    if client_scene and isinstance(client_scene, dict):
        fallback_str = _format_scene_context(client_scene)
        if fallback_str:
            _SCENE_CACHE["context"] = fallback_str
            _SCENE_CACHE["time"] = now
            return fallback_str

    return _SCENE_CACHE.get("context")


async def _forward_transcript_to_cv(text: str) -> None:
    """Forward speech transcript to CV adapter for name-intent binding."""
    cv_url = getattr(settings, "cv_adapter_url", None)
    if not cv_url or not text.strip():
        return
    cv_url = cv_url.replace("ws://localhost:", "ws://127.0.0.1:")
    if not _is_ws_available(cv_url):
        return
    try:
        import websockets
        async with websockets.connect(cv_url, open_timeout=0.2, close_timeout=0.2) as ws:
            await ws.send(json.dumps({"type": "transcript", "text": text}))
    except Exception as exc:
        logger.debug("CV adapter transcript forwarding skipped: %s", exc)


class Conversation:
    """Keep bounded chat history for one websocket session."""

    def __init__(self, llm):
        self.llm = llm
        self.history = deque(maxlen=settings.history_turns * 2)
        self.lock = asyncio.Lock()

    async def stream_reply(self, text: str, client_scene: dict | None = None) -> AsyncIterator[str]:
        if not settings.enabled:
            raise RuntimeError("LLM is disabled")
        if not text.strip():
            raise ValueError("Transcript is empty")
        async with self.lock:
            system_prompt = settings.system_prompt
            scene_ctx = await _get_scene_context(client_scene=client_scene)
            if scene_ctx:
                system_prompt = f"{system_prompt}\n\n[Visual Scene Context]: {scene_ctx}"
            messages = [
                {"role": "system", "content": system_prompt},
                *self.history,
                {"role": "user", "content": text},
            ]
            parts = []
            async for part in self.llm.stream_chat(messages):
                parts.append(part)
                yield part
            reply = "".join(parts).strip()
            if not reply:
                raise ValueError("LLM returned an empty reply")
            self.history.extend(
                [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": reply},
                ]
            )


_ABBREVIATIONS = {
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "prof.",
    "sr.",
    "jr.",
    "vs.",
    "etc.",
    "e.g.",
    "i.e.",
}


def _split_ready_segments(buffer: str, force: bool = False) -> tuple[list[str], str]:
    """Split at natural sentence boundaries (. ? !), with word-count bounding for unpunctuated text."""
    segments = []
    max_words = max(1, int(settings.max_speech_segment_words))
    max_sentence_words = max(max_words * 2, 24)
    pattern = (
        r"(?<!\d)[.?!]+(?:\s+|\n)" if not force else r"(?<!\d)[.?!]+(?:\s+|\n|$)"
    )
    while buffer:
        boundary = -1
        for match in re.finditer(pattern, buffer):
            prefix = buffer[: match.end()].strip()
            tokens = prefix.split()
            last_word = tokens[-1].lower() if tokens else ""
            if last_word in _ABBREVIATIONS:
                continue
            words_in_match = len(tokens)
            if words_in_match <= max_sentence_words:
                boundary = match.end()
                break
            else:
                comma_match = None
                for cm in re.finditer(r"[,;:]\s+", buffer[: match.end()]):
                    if len(buffer[: cm.end()].split()) <= max_words:
                        comma_match = cm
                boundary = comma_match.end() if comma_match else match.end()
                break

        if boundary < 0 and not force:
            words = list(re.finditer(r"\S+", buffer))
            if len(words) >= max_sentence_words:
                for match in re.finditer(r"[,;:]\s+", buffer):
                    if len(buffer[: match.end()].split()) >= max_words:
                        boundary = match.end()
                        break
                if boundary < 0:
                    boundary = words[max_words - 1].end()

        if boundary < 0:
            break
        segment, buffer = buffer[:boundary].strip(), buffer[boundary:].lstrip()
        if segment:
            segments.append(segment)

    if force and buffer.strip():
        segments.append(buffer.strip())
        buffer = ""
    return segments, buffer



async def handle_websocket(websocket: Any):
    await websocket.accept()
    started = perf_counter()
    try:
        first = await websocket.receive_json()
        if not isinstance(first, dict) or first.get("type") != "start":
            await websocket.send_json(
                {"type": "error", "message": "First message must be a start event."}
            )
            await websocket.close(code=1008)
            return

        session_id = str(first.get("session_id", "default"))[:128] or "default"
        mime_type = str(first.get("mime_type", "audio/webm"))[:100]
        voice = str(first.get("voice", settings.tts_voice))[:100]
        speed = float(first.get("speed", settings.tts_speed))
        provided_text = str(first.get("text", "")).strip()
        mode = str(first.get("mode", "conversation"))
        if mode not in {"conversation", "speak"}:
            await websocket.send_json(
                {"type": "error", "message": "mode must be conversation or speak."}
            )
            await websocket.close(code=1008)
            return
        if not 0.25 <= speed <= 4.0:
            await websocket.send_json(
                {"type": "error", "message": "speed must be between 0.25 and 4.0."}
            )
            await websocket.close(code=1008)
            return
        if len(provided_text) > 4096:
            await websocket.send_json(
                {"type": "error", "message": "Text input exceeds 4096 characters."}
            )
            await websocket.close(code=1008)
            return

        if mode == "speak":
            if not provided_text:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "Text input is required for speak mode.",
                    }
                )
                await websocket.close(code=1008)
                return
            tts_started = perf_counter()
            result = await websocket.app.state.providers.tts.synthesize_segment(
                provided_text, voice, speed
            )
            tts_ms = round((perf_counter() - tts_started) * 1000, 2)
            payload = (
                result.model_dump() if hasattr(result, "model_dump") else result.dict()
            )
            await websocket.send_json(
                {"type": "speech", "index": 0, "text": provided_text, **payload}
            )
            metrics = {
                "stt_total_ms": None,
                "llm_ttft_ms": None,
                "tts_ttfb_ms": tts_ms,
                "first_speech_end_to_end_ms": round(
                    (perf_counter() - started) * 1000, 2
                ),
                "tts_segment_ms": [tts_ms],
                "tts_segments": 1,
                "end_to_end_ms": round((perf_counter() - started) * 1000, 2),
                "audio_bytes": 0,
                "status": "ok",
                **_model_details(),
            }
            _append_metrics_csv(metrics)
            logger.info("Speech response segments=%s metrics=%s", 1, metrics)
            await websocket.send_json({"type": "metrics", "metrics": metrics})
            await websocket.send_json({"type": "done"})
            return
        sessions: OrderedDict = websocket.app.state.conversations
        if session_id not in sessions:
            if len(sessions) >= 128:
                sessions.popitem(last=False)
            sessions[session_id] = Conversation(websocket.app.state.providers.llm)
        sessions.move_to_end(session_id)

        client_scene = first.get("client_scene") or first.get("scene")
        audio_parts = []
        audio_size = 0
        if not provided_text:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                if message.get("bytes") is not None:
                    chunk = message["bytes"]
                    audio_size += len(chunk)
                    if audio_size > settings.max_audio_bytes:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": "Audio recording exceeds the configured size limit.",
                            }
                        )
                        await websocket.close(code=1009)
                        return
                    audio_parts.append(chunk)
                    continue
                if message.get("text") is not None:
                    import json

                    command = json.loads(message["text"])
                    if isinstance(command, dict) and command.get("type") == "end":
                        if command.get("client_scene"):
                            client_scene = command.get("client_scene")
                        break
                    if isinstance(command, dict) and command.get("type") == "cancel":
                        await websocket.send_json({"type": "cancelled"})
                        await websocket.close()
                        return
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Expected audio bytes or an end event.",
                        }
                    )

        metrics: dict[str, Any] = {"audio_bytes": audio_size}
        if provided_text:
            text = provided_text
            metrics["stt_total_ms"] = None
        elif audio_parts:
            stt_started = perf_counter()
            transcript = await websocket.app.state.providers.stt.transcribe(
                b"".join(audio_parts), mime_type
            )
            metrics["stt_total_ms"] = round((perf_counter() - stt_started) * 1000, 2)
            text = str(transcript.get("text", "")).strip()
        else:
            await websocket.send_json(
                {"type": "error", "message": "No audio was received."}
            )
            await websocket.close(code=1008)
            return
        await websocket.send_json(
            {"type": "transcript", "text": text, "language": "en"}
        )
        logger.info("User transcript: %s", text)
        asyncio.create_task(_forward_transcript_to_cv(text))
        if not text:
            metrics.update(
                {
                    "llm_ttft_ms": None,
                    "tts_ttfb_ms": None,
                    "tts_segments": 0,
                    "end_to_end_ms": round((perf_counter() - started) * 1000, 2),
                    "status": "no_speech",
                    **_model_details(),
                }
            )
            _append_metrics_csv(metrics)
            logger.info("Conversation metrics metrics=%s", metrics)
            await websocket.send_json({"type": "metrics", "metrics": metrics})
            await websocket.send_json({"type": "done"})
            return

        llm_started = perf_counter()
        llm_ttft = None
        tts_first_byte = None
        tts_first_call_ms = None
        tts_task_errors = []
        speech_queue: asyncio.Queue = asyncio.Queue()

        async def synthesize_segment(segment_index, segment_text):
            tts_started = perf_counter()
            try:
                result = await websocket.app.state.providers.tts.synthesize_segment(
                    segment_text, voice, speed
                )
                payload = (
                    result.model_dump()
                    if hasattr(result, "model_dump")
                    else result.dict()
                )
                return segment_index, segment_text, payload, tts_started, None
            except Exception as exc:
                logger.exception("TTS segment failed")
                return segment_index, segment_text, None, tts_started, exc

        async def send_synthesized(task):
            nonlocal tts_first_byte, tts_first_call_ms
            segment_index, segment_text, payload, tts_started, error = await task
            if error is not None:
                tts_task_errors.append(str(error))
                try:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "stage": "tts",
                            "message": "Speech synthesis failed for a response segment.",
                        }
                    )
                except Exception:
                    pass
                return
            if tts_first_byte is None:
                tts_first_byte = perf_counter()
                tts_first_call_ms = round((tts_first_byte - tts_started) * 1000, 2)
            try:
                await websocket.send_json(
                    {
                        "type": "speech",
                        "index": segment_index,
                        "text": segment_text,
                        **payload,
                    }
                )
            except Exception:
                pass
            metrics.setdefault("tts_segment_ms", []).append(
                round((perf_counter() - tts_started) * 1000, 2)
            )

        async def synthesize_queue():
            concurrency = max(1, int(settings.tts_max_concurrent_segments))
            sem = asyncio.Semaphore(concurrency)
            active_tasks = {}
            next_to_send = 0
            done_event = asyncio.Event()

            async def bounded_synthesize(idx, seg_text):
                async with sem:
                    return await synthesize_segment(idx, seg_text)

            async def sender():
                nonlocal next_to_send
                while True:
                    if next_to_send in active_tasks:
                        task = active_tasks.pop(next_to_send)
                        await send_synthesized(task)
                        next_to_send += 1
                    elif done_event.is_set() and not active_tasks:
                        break
                    else:
                        await asyncio.sleep(0.005)

            sender_task = asyncio.create_task(sender())
            while True:
                item = await speech_queue.get()
                try:
                    if item is None:
                        done_event.set()
                        break
                    idx, seg_text = item
                    active_tasks[idx] = asyncio.create_task(bounded_synthesize(idx, seg_text))
                finally:
                    speech_queue.task_done()
            await sender_task

        tts_worker = asyncio.create_task(synthesize_queue())
        reply_parts, text_buffer = [], ""
        segment_index = 0
        try:
            async for delta in sessions[session_id].stream_reply(text, client_scene=client_scene):
                now = perf_counter()
                if llm_ttft is None:
                    llm_ttft = round((now - llm_started) * 1000, 2)
                reply_parts.append(delta)
                text_buffer += delta
                await websocket.send_json({"type": "llm_delta", "text": delta})
                ready, text_buffer = _split_ready_segments(text_buffer)
                for segment in ready:
                    await speech_queue.put((segment_index, segment))
                    segment_index += 1
            ready, text_buffer = _split_ready_segments(text_buffer, force=True)
            for segment in ready:
                await speech_queue.put((segment_index, segment))
                segment_index += 1
        finally:
            await speech_queue.put(None)
            await tts_worker

        metrics["llm_ttft_ms"] = llm_ttft
        metrics["llm_total_ms"] = round((perf_counter() - llm_started) * 1000, 2)
        metrics["tts_ttfb_ms"] = tts_first_call_ms
        metrics["first_speech_end_to_end_ms"] = (
            round((tts_first_byte - started) * 1000, 2) if tts_first_byte else None
        )
        metrics["tts_segments"] = segment_index
        metrics["end_to_end_ms"] = round((perf_counter() - started) * 1000, 2)
        metrics["status"] = "partial_tts_failure" if tts_task_errors else "ok"
        reply = "".join(reply_parts).strip()
        # The OpenAI-compatible streaming response does not expose usage data;
        # estimate tokens at roughly four UTF-8 bytes per token.
        estimated_tokens = round(len(reply.encode("utf-8")) / 4)
        metrics.update(_model_details())
        metrics["llm_tokens_per_second"] = (
            round(estimated_tokens / (metrics["llm_total_ms"] / 1000), 2)
            if metrics["llm_total_ms"]
            else None
        )
        _append_metrics_csv(metrics)
        logger.info(
            "Final LLM response: %s | estimated_tokens_per_second=%s | segments=%s",
            reply,
            metrics["llm_tokens_per_second"],
            segment_index,
        )
        logger.info("Conversation metrics metrics=%s", metrics)
        await websocket.send_json({"type": "metrics", "metrics": metrics})
        await websocket.send_json({"type": "done", "text": reply})
    except WebSocketDisconnect:
        logger.info("Conversation websocket disconnected")
    except Exception as exc:
        logger.exception("Conversation pipeline failed")
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": str(exc)
                    if isinstance(exc, ValueError)
                    else "Conversation pipeline failed.",
                }
            )
            await websocket.close(code=1011)
        except Exception:
            pass
