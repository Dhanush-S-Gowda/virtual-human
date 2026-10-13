# Backend

This directory contains the Virtual Human API. One FastAPI application handles
the WebSocket session, in-process Faster-Whisper transcription, LLM streaming,
and the selected TTS provider. Faster-Whisper and Supertonic execute inside the
backend. Kokoro and the OpenAI-compatible LLM endpoint are provider services.

## Files

- `main.py`: application startup, provider selection, and `/health` and `/ws`
  routes.
- `pipeline.py`: WebSocket protocol, conversation history, live speech
  segmentation, and session metrics.
- `config.yaml`: provider URLs, model selection, and runtime settings.
- `stt/faster_whisper.py`: local speech recognition.
- `tts/supertonic.py`: in-process ONNX synthesis.
- `tts/kokoro.py`: HTTP adapter for the optional Kokoro service.
- `llm/openai.py`: streaming OpenAI-compatible chat adapter.
- `Dockerfile`: Python 3.13 API image using the locked `uv` environment.
- `setup.sh` / `setup.ps1`: local development environment and model asset setup
  for Linux/macOS and Windows, respectively.

## Model setup

From this directory, run:

```bash
./setup.sh
```

On Windows, run the PowerShell equivalent from this directory:

```powershell
.\setup.ps1
```

This synchronizes dependencies, downloads the configured Faster-Whisper model
to the repository's `.cache/faster-whisper` directory, and downloads pinned
Supertonic 3 assets to `supertonic/assets`. The API uses local model files and
does not download missing models during startup. Run setup again when changing
the Faster-Whisper model.

## Run locally

Configure provider URLs in `config.yaml`, then start the API:

```bash
uv run main.py
```

When running directly on the host, use host-local addresses such as
`http://localhost:8888/v1` for a host LLM provider and `http://localhost:8880`
for Kokoro. In Compose, the default LLM URL uses `host.docker.internal`; Compose
provider service URLs use their service names (for example, `http://kokoro:8880`
or `http://ollama:11434/v1`).

## Run with Compose

From the repository root, prepare the models with `./backend/setup.sh`, then
build and start the stack:

```bash
docker compose up --build
```

The backend image installs dependencies from `pyproject.toml` and `uv.lock`.
Compose mounts the Faster-Whisper cache and Supertonic assets read-only, mounts
`config.yaml` read-only, and persists session metrics under
`backend/metrics-data/metrics.csv`.

## TTS device selection

Supertonic uses `tts_device: auto` by default. It selects ONNX Runtime CUDA
when available, then OpenVINO on an Intel GPU, and keeps CPU as the final
fallback (including for unsupported operators). `tts_device: cpu` forces CPU.
Automatic GPU selection still requires the matching ONNX Runtime package and
working drivers: use an ONNX Runtime build with CUDA support for NVIDIA, or
`onnxruntime-openvino` plus Intel graphics drivers for Intel GPUs. Startup logs
report the providers attached to the four model sessions. Actual speed depends
on device support and model operator coverage.

## WebSocket behavior

The client connects to `/ws`, sends a JSON `start` event, streams microphone
bytes, and sends an `end` event. The server transcribes the complete recording,
streams each `llm_delta` as soon as it arrives, and queues speech segments for
concurrent synthesis. Speech segments split at sentence-ending periods followed
by whitespace (decimal/version periods stay within the word) or at the
configured `max_speech_segment_words` limit for long unpunctuated text. Commas do not
trigger a split. Ordered `speech` events, metrics, and `done` follow.

TTS responses include base64 WAV audio, sample rate, and word timing arrays for
lip synchronization. Supertonic timings are approximate. The root
[README](../Readme.md) describes the full application setup and websocket
event flow.
