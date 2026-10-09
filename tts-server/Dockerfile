# Compatibility Dockerfile: build with repository root as context.
FROM python:3.12-slim
WORKDIR /app
ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m pip install --no-cache-dir fastapi httpx pydantic "uvicorn[standard]"
COPY backend ./backend
COPY main.py .
EXPOSE 8000
CMD ["python", "main.py"]
