from fastapi import APIRouter

from schemas.agent_sc import (
    ChatRequest,
    ChatResponse
)


router = APIRouter(
    prefix="/agent",
    tags=["AI Agent"]
)


@router.post("/chat", response_model=ChatResponse)
def chat(data: ChatRequest):

    # Temporary response.
    # Real RAG + LLM logic will be added later.

    return ChatResponse(
        answer=f"You asked: {data.message}",
        sources=[],
        recommendations=[]
    )