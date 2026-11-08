"""Download the speech model once into the shared Compose volume."""

from pathlib import Path
import shutil
import urllib.request


MODEL_NAME = "nemotron-speech-streaming-en-0.6b.q8_0.gguf"
MODEL_URL = (
    "https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b/resolve/main/"
    + MODEL_NAME
)


def download_model(model_dir=Path("/models"), opener=urllib.request.urlopen):
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / MODEL_NAME
    if target.exists() and target.stat().st_size > 0:
        print(f"Model already present: {target}", flush=True)
        return target
    temporary = target.with_suffix(".partial")
    print(f"Downloading {MODEL_NAME}", flush=True)
    with opener(MODEL_URL, timeout=120) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
        expected = response.headers.get("Content-Length")
    if temporary.stat().st_size == 0 or (
        expected is not None and temporary.stat().st_size != int(expected)
    ):
        raise ValueError("The Nemotron model download is incomplete.")
    temporary.chmod(0o644)
    temporary.replace(target)
    print(f"Model ready: {target}", flush=True)
    return target


if __name__ == "__main__":
    download_model()
