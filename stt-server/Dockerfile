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

# Pre-download the Whisper model at build time so it is cached in the image
# and the container starts instantly.
RUN python -c "\
from faster_whisper import WhisperModel; \
WhisperModel('base', device='cpu', compute_type='int8')"

COPY backend/stt/whisper_engine.py ./main.py

EXPOSE 8001

CMD ["python", "main.py"]
