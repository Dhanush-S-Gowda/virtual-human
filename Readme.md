# Virtual Human

Local talking avatar with an HTTP backend, Ollama Qwen3.5 2B, Kokoro speech synthesis, and faster-whisper transcription.

## Start

Start Docker Desktop with NVIDIA GPU support, then run:

```powershell
.\Start-VirtualHuman.ps1 -Rebuild
```

The launcher starts Ollama, pulls `qwen3.5:2b` into a persistent volume, and starts the services. Open http://localhost:4173. Hold Space to speak; release it to transcribe, generate a reply, and speak that reply with lip synchronization.

```powershell
.\Start-VirtualHuman.ps1 -Stop
docker compose logs -f tts-server stt-server
```

## Modular backend

```text
main.py                         Single HTTP server entry point
backend/
  app.py                        Startup, CORS, errors, latency logging
  config.py                     Environment configuration and prompt loading
  schemas.py                    Shared request and response contracts
  providers.py                  Provider registry and construction
  conversation.py               Session history and chat orchestration
  api/routes.py                 HTTP transport and endpoint wiring
  llm/
    base.py                     LLM provider interface
    ollama.py                   Ollama implementation
    prompts/system.txt          Exactly 300 nonempty instruction lines
  tts/
    base.py                     TTS provider interface
    kokoro.py                   Kokoro audio and word timings
  stt/
    base.py                     STT provider interface
    whisper_http.py             Gateway adapter for the Whisper worker
    whisper_engine.py           Isolated faster-whisper worker
  Dockerfile                    Main API image
  requirements.txt              Main API dependencies
tests/test_backend.py           Backend contract tests
```

Run the main server locally with `pip install -r backend/requirements.txt`, then `python main.py`. Set `LLM_BASE_URL=http://localhost:11434`, `KOKORO_URL=http://localhost:8880`, and `STT_BASE_URL=http://localhost:8001` when using locally published Docker providers.

The Compose service name `tts-server` is retained for compatibility, but it now runs the main API, not just TTS. The browser sends STT, LLM, and TTS requests through port 8000. Ollama, Kokoro, and the isolated Whisper worker remain separate model processes so heavy model dependencies do not enter the API process. The old `llm-server/main.py`, `tts-server/main.py`, and `stt-server/main.py` are compatibility launchers; their implementations live under `backend/`.

There is no WebSocket endpoint. HTTP is the current transport.

## Add a model or SDK

Implement the domain's `base.py` interface in a new module under `backend/llm`, `backend/tts`, or `backend/stt`. Register its constructor in `backend/providers.py` and select it with `LLM_PROVIDER`, `TTS_PROVIDER`, or `STT_PROVIDER`. Keep SDK imports and provider response conversion inside that module. Install new SDK dependencies in the appropriate image. Routes, conversation history, and avatar playback do not need provider-specific changes.

LLM adapters return plain reply text. TTS adapters return base64 WAV audio plus `words`, `wtimes`, and `wdurations` in milliseconds; these timings are required for lip synchronization. STT adapters return `text`, `language`, and audio `duration`.

## Prompt and configuration

The default prompt is `backend/llm/prompts/system.txt`, containing 300 instruction lines for the conversational avatar. Edit it and rebuild the main API image to apply changes. Override the path with `LLM_SYSTEM_PROMPT_FILE` or the entire prompt with `LLM_SYSTEM_PROMPT`. Prompt files are read once at server startup.

| Variable | Default |
|---|---|
| LLM_PROVIDER | ollama |
| LLM_MODEL | qwen3.5:2b |
| LLM_BASE_URL | http://ollama:11434 |
| LLM_CONTEXT_LENGTH | 8192 |
| LLM_MAX_TOKENS | 256 |
| LLM_TEMPERATURE | 0.7 |
| LLM_HISTORY_TURNS | 8 |
| LLM_TIMEOUT | 120 seconds |
| TTS_PROVIDER | kokoro |
| KOKORO_URL | http://kokoro:8880 |
| STT_PROVIDER | whisper-http |
| STT_BASE_URL | http://stt-server:8001 |

Thinking is disabled for concise spoken responses. Browser sessions use separate IDs for history. History is held in memory with at most 128 sessions, eight turns per session by default; restart or eviction clears it. HTTP callers should supply a unique `session_id`; omitted IDs use the shared compatibility session `default`.

## HTTP API

- `GET /health`: main server status.
- `GET /llm/health`: configured model availability.
- `POST /chat`: `message`, optional `session_id`, optional `reset_history`; returns `reply` and `history_length`.
- `POST /synthesize`: `text`, optional `voice` and `speed`; returns audio and lip-sync timings.
- `POST /transcribe`: multipart `audio` file, at most 10 MB; returns recognized text, language, and recording duration.

API documentation: http://localhost:8000/docs. The front end requests transcription, chat, and synthesis in sequence. `main.py` hosts and routes all three HTTP operations.

## Latency

Backend logs show STT, LLM, and TTS request times in milliseconds. The Ollama adapter additionally logs model loading, prompt evaluation, and token generation. These exclude microphone capture and browser playback. First requests may include model loading and prompt cache initialization; measure repeated requests separately. A 300-line prompt increases prompt processing and context usage.

Run contract tests in the API image:

```powershell
Get-Content -Raw tests/test_backend.py | docker compose run --rm --no-deps -T tts-server python -
```

### Measured with the 300-line prompt

Test input: "Hello, how are you today?" on Qwen3.5 2B, RTX 4050, 8192-token context. One initial request and one warm request; timings vary and are not averages.

| Stage | Initial request | Warm request |
|---|---:|---:|
| STT | 1.722 s | 3.600 s |
| LLM | 37.494 s | 1.022 s |
| TTS | 7.373 s | 3.743 s |
| Total | 46.591 s | 8.365 s |

The initial LLM request included 35.814 seconds of model loading; prompt evaluation took 0.817 seconds. Measurements exclude input audio preparation, microphone capture, and browser playback. Full results: `benchmarks/300-line-prompt.json`.
