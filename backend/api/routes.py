from fastapi import APIRouter, HTTPException, Request
from backend.config import settings
from backend.conversation import Conversation
from backend.schemas import ChatRequest, ChatResponse, LLMHealthResponse, SynthesizeRequest, SynthesizeResponse

router = APIRouter()

@router.get("/health")
async def health():
    return {"status": "ok", "transport": "http"}

@router.get("/llm/health", response_model=LLMHealthResponse)
async def llm_health(request: Request):
    available = settings.enabled and await request.app.state.providers.llm.health_check()
    return LLMHealthResponse(available=available, enabled=settings.enabled, base_url=settings.llm_url, model=settings.llm_model)

@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request):
    sessions = request.app.state.conversations
    key = body.session_id
    if key not in sessions:
        if len(sessions) >= 128:
            sessions.popitem(last=False)
        sessions[key] = Conversation(request.app.state.providers.llm)
    sessions.move_to_end(key)
    return await sessions[key].reply(body.message, body.reset_history)

@router.post("/synthesize", response_model=SynthesizeResponse)
async def synthesize(body: SynthesizeRequest, request: Request):
    return await request.app.state.providers.tts.synthesize(body)

@router.post("/transcribe")
async def transcribe(request: Request):
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        raise HTTPException(415, "Expected multipart audio upload")
    body = await request.body()
    if len(body) > 10_000_000:
        raise HTTPException(413, "Audio upload exceeds 10 MB")
    return await request.app.state.providers.stt.transcribe(body, content_type)
