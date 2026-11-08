# Troubleshooting

[Documentation index](README.md) | [Docker setup](docker.md)

| Symptom | Check or action |
|---|---|
| GPU reservation or driver error | Verify Docker GPU access, or use the CPU-only Compose command. |
| Startup appears stuck | Read `ollama-init`, `speech-init`, and model service logs. First startup downloads weights. |
| Port 4173 is in use | Stop the conflicting listener or change the frontend host port in Compose. |
| An old sign-in page appears | Open the app root at `http://localhost:4173`, rather than an old `/oauth2/callback` URL. Rebuild the current stack if needed. |
| Camera preview works but scene context is missing | Check the `cv` service logs and browser `/cv` connection. Preview and inference have separate status. |
| Microphone or camera access fails | Allow browser permissions, check other apps using the device, and use localhost or an HTTPS origin. |
| Speech takes too long | Compare transcription, LLM, and first-audio timings. Check GPU use, cold model loads, and CPU contention before changing segment settings. |
| `/think` appears in text | Confirm the Docker configuration and model are current, then rebuild. Docker uses structured plans and stop markers for thinking controls. |
| A conversation fails | Check `docker compose logs --tail 100 backend ollama kokoro` for the provider error. |
| SQLite metrics are missing | Check Redis and worker ping, then worker logs. CSV may still contain the turn if queue submission failed. |
| A config edit has no effect | Use `config.docker.yaml` and rebuild for Docker; use `config.yaml` and restart for host development. |
