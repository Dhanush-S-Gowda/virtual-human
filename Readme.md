# Virtual Human

Virtual Human is a browser based talking avatar. One FastAPI backend handles
speech recognition, LLM streaming, speech synthesis, and websocket sessions.
Faster-Whisper and Supertonic run inside that backend. Kokoro and the selected
OpenAI-compatible LLM endpoint remain provider services.

## Architecture

| Component | Responsibility | Default location |
|---|---|---|
| `talking-avatar` | Browser UI, microphone capture, avatar rendering, ordered audio playback | `http://localhost:4173` |
| `backend` | WebSocket API, STT, conversation history, streamed LLM handling, TTS | `http://localhost:8000` |
| Faster-Whisper | In-process STT model loaded by the backend | Backend container |
| Supertonic 3 | In-process ONNX TTS model loaded by the backend | Backend container |
| Kokoro | Optional HTTP TTS provider | `http://localhost:8880` |
| LLM provider | OpenAI-compatible streaming chat completions | Host port `8888` by default; Ollama is also available in Compose |

The Compose stack has no separate STT or application LLM/TTS API containers.
The `backend` service is the application API. The `ollama` and `kokoro`
services are provider endpoints; the backend calls them only when configured
to do so. The `ollama-init` service checks the configured endpoint and prepares
the model when Ollama is selected.

## Requirements

- Docker Desktop or Docker Engine with the Compose plugin.
- `uv` and network access for the one-time local model setup.
- An OpenAI-compatible LLM provider. The default configuration expects it on
  the host at port `8888` and requests `unsloth/Qwen3.5-0.8B-GGUF:BF16`.
- For local Faster-Whisper and Supertonic, enough disk space for their model
  files. Supertonic ONNX assets are about 390 MB.

The included Ollama service requests an NVIDIA GPU. If Docker cannot satisfy
that reservation, configure a supported GPU runtime or use another LLM
provider.

## Set up models

From the repository root, download the Faster-Whisper model selected in
`backend/config.yaml` and the pinned Supertonic 3 assets:

```bash
cd backend
./setup.sh
cd ..
```

The script creates/syncs the local `uv` environment, downloads Faster-Whisper
to `.cache/faster-whisper`, and downloads Supertonic assets to
`backend/supertonic/assets`. Compose mounts both model locations into the
backend container. Run setup again after changing the Faster-Whisper model.

## Configure providers

Edit `backend/config.yaml` before startup. The Compose backend reads this file
as a read-only mount.

### LLM

The default `llm_url` is `http://host.docker.internal:8888/v1`, which lets the
backend container reach a provider listening on the host. Keep the provider
model ID in `llm_model` aligned with that server.

To use the Ollama service in Compose, set the active values to the Ollama
endpoint and model, for example:

```yaml
llm_url: http://ollama:11434/v1
llm_model: qwen3.5:2b
llm_api_key: not-needed
llm_extra_body: {}
```

`ollama-init` pulls the configured model if it is missing and warms it before
the backend starts. For another OpenAI-compatible provider, set `llm_url`,
`llm_model`, and `llm_api_key` as required by that provider.

### TTS

Supertonic is the default and runs in the backend process. Set
`tts_provider: supertonic`; choose a Supertonic voice style such as `F1` or
`F2`. The model assets must be present from setup.

To use the Kokoro service instead, configure:

```yaml
tts_provider: kokoro
tts_url: http://kokoro:8880
tts_voice: af_bella
```

Kokoro returns word timings for lip synchronization. Supertonic returns
approximate word timings distributed across the generated audio. The Compose
Kokoro service is started with the stack and reports ready before the backend
starts.

### STT and conversation

Faster-Whisper runs in process. `faster_whisper_model`, `faster_whisper_device`,
and `faster_whisper_compute_type` select its model and runtime. The API reads
the cached model locally and does not download it on startup.

Other commonly changed settings include `max_audio_bytes`, `max_tokens`,
`temperature`, `history_turns`, `tts_speed`, `tts_supertonic_steps`,
`max_speech_segment_chars`, and `tts_max_concurrent_segments`. Restart the
backend after editing configuration.

## Start and stop

After model setup and provider configuration, build and start the application:

```bash
docker compose up --build
```

Open [http://localhost:4173](http://localhost:4173), hold Space to record, and
release Space to submit the audio. The backend transcribes the complete
recording, streams LLM text, and synthesizes speech segments while generation
continues.

For detached startup with health checks:

```bash
docker compose up -d --build --wait --wait-timeout 900
```

Stop the stack with:

```bash
docker compose down
```

`Start-VirtualHuman.ps1` is a Windows convenience wrapper for Compose.

## Streaming and playback

The backend prefers sentence periods (`.`) as speech boundaries. When buffered
text reaches `max_speech_segment_chars` without a period, it falls back to
configured punctuation and then a word boundary. It synthesizes and sends each
segment during LLM generation. The browser processes speech events in arrival
order before queuing them with TalkingHead, so asynchronous audio decoding
cannot reorder segments.

Supertonic audio is peak-normalized to -1 dBFS before WAV encoding. Speech
playback uses unity gain in TalkingHead, and the browser's operating-system
output level still affects what you hear.

## API and observability

- `GET /health` reports backend health.
- `WS /ws` accepts a JSON `start` event, microphone audio chunks, and an `end`
  event. It emits `transcript`, `llm_delta`, ordered `speech`, and `metrics`
  events, followed by `done`. A `cancel` event cancels the current recording.

The `start` event accepts `session_id`, `mime_type`, `voice`, `speed`, `mode`,
and optional `text`. Use `mode: "speak"` with `text` for direct TTS; omit text
to send microphone audio for transcription and conversation. API logs include
the configured provider/model names, user transcript, final LLM response,
estimated tokens per second, TTS segment count, and latency metrics. Token rate
is estimated because the OpenAI-compatible stream does not provide token usage.

Metrics are appended to `backend/metrics-data/metrics.csv` and persist on the
host across container restarts. They include provider/model names, STT total
time, LLM time to first token and total time, TTS time to first audio result,
segment count, end-to-end timing, audio size, and status.

Inspect service logs with:

```bash
docker compose logs -f backend talking-avatar
```

## Backend development

The backend image uses Python 3.13, installs locked dependencies from
`backend/pyproject.toml` and `backend/uv.lock`, and installs FFmpeg for audio
decoding. Model files are mounted at runtime rather than downloaded during the
image build.

To run the API directly from the backend directory after setup:

```bash
cd backend
uv run main.py
```

For direct host execution, update provider URLs in `config.yaml` to host-local
addresses (for example, `http://localhost:8880` for Kokoro). The API listens on
port 8000 by default. The browser frontend is built separately by its
`talking-avatar` service.
