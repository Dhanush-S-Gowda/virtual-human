# CV Subsystem — Implementation Notes

## 1. Repository Reconnaissance Findings

### 1.1 virtual-human Architecture

The existing project is a **Docker Compose** stack with four services:

| Service | Role | Technology |
|---|---|---|
| `backend` | FastAPI WebSocket API, in-process STT (Faster-Whisper), in-process TTS (Supertonic 3 ONNX), LLM streaming via OpenAI-compatible API | Python 3.13, FastAPI, uvicorn |
| `talking-avatar` | Browser UI, microphone capture, avatar rendering (TalkingHead), ordered audio playback | Vite + vanilla JS, Three.js |
| `kokoro` | Optional HTTP TTS provider | External Docker image |
| `ollama` | Optional local LLM provider | External Docker image + NVIDIA GPU |

### 1.2 Entry Point and Runtime

- **Backend entry point**: `virtual-human/backend/main.py` — creates a FastAPI app with
  a single WebSocket endpoint at `/ws` and a health check at `/health`.
- **Pipeline**: `virtual-human/backend/pipeline.py` — `handle_websocket()` handles the
  full conversation flow: receive audio → STT → LLM streaming → TTS → speech events.
- **Config**: `virtual-human/backend/config.yaml` loaded via `config.py` into a
  `SimpleNamespace` called `settings`.

### 1.3 STT Transcript Format

The STT provider (`stt/faster_whisper.py`) returns a dict:
```python
{"text": "transcribed text", "language": "en", "duration": 3.14}
```

**Key observations:**
- **No timestamps per word/segment** are exposed to the pipeline — only concatenated text.
- **No speaker diarization** — single speaker assumed.
- **No VAD timestamps** forwarded — VAD is used internally by faster-whisper for filtering
  silence but segment-level timestamps are discarded after joining.
- The transcript is sent to the browser as:
  `{"type": "transcript", "text": "...", "language": "en"}`

### 1.4 WebSocket Protocol (Backend → Browser)

Events emitted on the WS:
- `transcript` — final STT text
- `llm_delta` — streamed LLM response chunks
- `speech` — synthesized audio segment with word timings for lip-sync
- `metrics` — latency/performance data
- `done` — conversation turn complete
- `error` — error messages
- `cancelled` — recording cancelled

### 1.5 TTS Interface

- `TTSProvider.synthesize_segment(text, voice, speed) -> SynthesizeResponse`
- `SynthesizeResponse`: `audio_base64`, `audio_encoding`, `words`, `wtimes`,
  `wdurations`, `sample_rate`
- CV work must **never** block TTS synthesis.

### 1.6 Target Environment

- **OS**: Windows 10/11
- **System Python**: 3.14.4 (host) — backend uses 3.13 inside Docker
- **GPU**: NVIDIA GeForce RTX 4060 Laptop GPU, 8 GB VRAM, Driver 610.62
- **Package manager**: `uv` 0.13.0 available
- **No pip** in system Python; `uv` is the preferred tool
- **No PyTorch** installed in system Python yet
- Backend runs in Docker; CV subsystem will run on the **host** directly

### 1.7 Existing Dependencies (Backend, inside Docker)

From `pyproject.toml`:
- `faster-whisper==1.2.1`, `onnxruntime-openvino==1.23.0`, `numpy>=1.26`,
  `pydantic`, `fastapi`, `httpx`, `uvicorn`, `PyYAML`, `soundfile`, `librosa`, `av`

## 2. Integration Strategy

### 2.1 Chosen Approach: Sidecar Process with WebSocket Bridge

The backend runs inside Docker. The CV subsystem runs on the host with direct
camera/GPU access. Integration options considered:

1. **In-process import** — Not viable: backend is containerized, CV needs host GPU
   and camera.
2. **WebSocket bridge** — CV exposes a local WebSocket server on the host. The
   backend (or a thin proxy) can connect to it. ✅ **Selected.**
3. **HTTP polling** — Higher latency, not suitable for real-time events.
4. **Shared file/SQLite** — Fragile, not real-time.

**Implementation:**
- CV runs as an independent Python process on the host.
- Exposes a WebSocket server (default `ws://localhost:8765`) for event streaming
  and state queries.
- Provides an async Python adapter class (`VirtualHumanAdapter`) that the backend
  could import if it were running on the host, or connect to via WebSocket.
- The adapter can also receive STT transcript events to trigger name binding.

### 2.2 STT Integration for Name Binding

Since the backend's STT produces only final text without per-word timestamps or
speaker labels, the CV subsystem will:

1. **Receive transcript text** via the WebSocket bridge (backend forwards the
   transcript event to CV after sending it to the browser).
2. **Parse self-introduction intent** from the text ("my name is X", "I am X",
   "call me X").
3. **Associate speaker with visible person** using timing heuristics (who was
   visible and had mouth movement during the speech window).
4. If ambiguous, emit `speaker_association_ambiguous` event.

### 2.3 Minimal Backend Change (Deferred)

A single addition to `pipeline.py` would forward the transcript to the CV
WebSocket — approximately 5 lines. This change is **not made now**; it is
documented here and will be proposed when the CV subsystem is tested and ready.

```python
# In handle_websocket(), after sending transcript to browser:
# await cv_bridge.send_transcript(text, timestamp)
```

## 3. Technology Decisions

| Component | Choice | Rationale |
|---|---|---|
| Python version | 3.14 (host system) | Already installed; CV runs on host |
| Person detection + pose | `ultralytics` YOLOv8n-pose | Small model, single pass for boxes+keypoints |
| Tracking | ByteTrack via ultralytics `.track()` | Integrated, well-tested |
| Face detection + embeddings | `insightface` with ONNX Runtime | ArcFace embeddings, no PyTorch required for inference |
| Face matching | Cosine similarity, configurable threshold | Simple, effective for small DB |
| Identity store | SQLite3 (stdlib) + numpy | No extra dependency for storage |
| Event transport | `websockets` library | Lightweight, async, bidirectional |
| Config | YAML via `PyYAML` | Consistent with backend style |
| Package management | `uv` with `pyproject.toml` | Matches project conventions |

## 4. Assumptions

1. The CV subsystem runs on the **host** Windows machine, not inside Docker.
2. Camera access is via OpenCV's `cv2.VideoCapture` on the host.
3. The RTX 4060 Laptop GPU (8 GB) can handle YOLOv8n-pose + InsightFace
   concurrently with Ollama (if Ollama is running in Docker with GPU reservation).
4. CUDA 12.x is available via the NVIDIA driver 610.62.
5. The backend can optionally forward STT transcripts to the CV WebSocket.

## 5. Unresolved Questions

1. **GPU sharing**: If Ollama reserves all GPU memory, CV may need CPU fallback.
   Need to test actual VRAM usage.
2. **Speaker diarization**: The current STT provides no speaker labels. For
   multi-person scenarios, mouth-movement heuristics are the only available signal.
3. **Backend modification**: Forwarding transcripts requires a small change to
   `pipeline.py`. This should be discussed with the user before implementation.
4. **Consent workflow**: The spec requires explicit consent before face enrollment.
   The UI for consent collection is not defined — CV will enforce the flag but
   the UX is deferred.

## 6. Implementation Status

1. ✅ Reconnaissance and notes (this document)
2. ✅ Project structure, config, event schema, adapter contract
3. ✅ Camera capture and frame scheduling (threaded, drop-stale queue)
4. ✅ Person detection (YOLOv8-pose CUDA), pose estimation, and ByteTrack tracking
5. ✅ Action classification (waving, hand raised) with keypoint buffers and debounce cooldowns
6. ✅ Face detection, ArcFace embeddings (ONNX Runtime GPU), quality filtering, and cosine matcher
7. ✅ Identity store (SQLite) with transactional safety, consent tracking, and cascading deletion
8. ✅ STT name binding and speaker association with ambiguity safeguards
9. ✅ Integration adapter and WebSocket bridge (`ws://0.0.0.0:8765`)
10. ✅ Optional general object detection (`objects.py`) with rate-limiting and one-shot queries
11. ✅ Full test suite (44 unit & end-to-end integration tests passing) and documentation (`README.md`)
