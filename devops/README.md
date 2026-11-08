# DevOps

This folder owns container builds, service configuration, routing, and startup.
Run Compose commands from the repository root.

| File | Purpose |
|---|---|
| [compose.yaml](compose.yaml) | Base CPU stack, health checks, networks, and named volumes |
| [compose.gpu.yaml](compose.gpu.yaml) | NVIDIA settings for Ollama and Kokoro |
| [backend.Dockerfile](backend.Dockerfile) | Python image used by API, CV, model initialization, and Celery |
| [frontend.Dockerfile](frontend.Dockerfile) | Node build and Nginx runtime image |
| [nginx.conf](nginx.conf) | Frontend files plus `/api` and `/cv` proxies |
| [Start-VirtualHuman.ps1](Start-VirtualHuman.ps1) | Windows launcher; supports `-Rebuild` and `-Stop` |

The root [docker-compose.yml](../docker-compose.yml) includes the base and GPU
files. Both deployment modes use the `virtual-human` project name and named
volumes. Docker build contexts point to the repository root.

## Common commands

```powershell
# GPU demo
docker compose up --build -d

# CPU demo
docker compose -f devops/compose.yaml up --build -d

# Validate the two configurations
docker compose config --quiet
docker compose -f devops/compose.yaml config --quiet
```

For CPU operation, keep `-f devops/compose.yaml` in all later Compose commands.
The frontend binds to `127.0.0.1:4173`; other services stay on the internal
network. `docker compose down` retains named volumes. Avoid `down -v` for a
routine restart because it deletes stored data and model caches.

See [Docker setup](../docs/docker.md) for logs, service checks, and tests.
See [configuration and data](../docs/configuration.md) before changing model
names, credentials, or persistent storage.
