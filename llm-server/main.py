from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import ollama
import uvicorn

app = FastAPI(title="Virtual Human LLM Server")


class Query(BaseModel):
    prompt: str


@app.post("/chat")
async def chat(query: Query) -> dict[str, str]:
    try:
        response = ollama.chat(
            model="qwen3.5:4b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful and friendly virtual human assistant. "
                        "Keep your responses concise and conversational for speech output."
                    ),
                },
                {"role": "user", "content": query.prompt},
            ],
        )
        return {"response": response["message"]["content"]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
