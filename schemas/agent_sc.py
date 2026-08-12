from pydantic import BaseModel


class ChatRequest(BaseModel):
    student_id: int
    message: str


class Source(BaseModel):
    lecture_id: int
    lecture_title: str
    start_ts: int
    end_ts: int


class Recommendation(BaseModel):
    lecture_id: int
    reason: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    recommendations: list[Recommendation]