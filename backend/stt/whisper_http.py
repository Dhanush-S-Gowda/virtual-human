import httpx
from backend.config import settings

class WhisperHTTPProvider:
    async def transcribe(self, body: bytes, content_type: str) -> dict:
        # Preserve the multipart boundary supplied by the browser.
        async with httpx.AsyncClient(timeout=settings.timeout) as client:
            response = await client.post(
                settings.stt_url.rstrip("/") + "/transcribe",
                content=body, headers={"Content-Type": content_type},
            )
            response.raise_for_status()
            return response.json()
