FROM python:3.12-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir fastapi httpx pydantic "uvicorn[standard]"

EXPOSE 8000

CMD ["python", "main.py"]
