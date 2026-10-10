FROM python:3.12-slim

WORKDIR /app

ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg is required by faster-whisper for audio decoding
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY stt-server/requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

# The configured model downloads at startup into the persistent Compose volume.

COPY backend/stt/whisper_engine.py ./main.py

EXPOSE 8001

CMD ["python", "main.py"]
