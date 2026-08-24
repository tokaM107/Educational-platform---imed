"""The student-facing chatbot endpoint."""

from fastapi import APIRouter, Depends

from app.api.deps import get_conn, get_current_user, get_tutor
from app.schemas.chat import ChatRequest, ChatResponse, Citation, VideoSegment
from app.services.prompts import to_stamp


router = APIRouter(
    prefix="/api",
    tags=["Tutor"],
)


@router.post("/chat", response_model=ChatResponse)
def chat(
    data: ChatRequest,
    conn=Depends(get_conn),
    tutor=Depends(get_tutor),
    current_user=Depends(get_current_user),
):
    """Answer a question from the lecture transcript and point at the video.

    Authenticated: this is the paid tutor, and every call spends a model
    request. Left open it is both a way to read course material without an
    account and a way to run up the bill from outside.
    """

    result = tutor.ask(
        conn,
        question=data.message,
        lecture_id=data.lecture_id,
        history=[(message.role, message.content) for message in data.history],
    )

    segments = [
        VideoSegment(
            lecture_id=segment.lecture_id,
            lecture_title=segment.lecture_title,
            video_url=f"/api/lectures/{segment.lecture_id}/video",
            start_ts=segment.start_ts,
            end_ts=segment.end_ts,
            start_label=to_stamp(segment.start_ts),
            end_label=to_stamp(segment.end_ts),
        )
        for segment in result.segments
    ]

    citations = [
        Citation(
            index=index,
            chunk_id=passage.chunk_id,
            lecture_id=passage.lecture_id,
            start_ts=passage.start_ts,
            end_ts=passage.end_ts,
            text=passage.text,
            distance=round(passage.distance, 4),
        )
        for index, passage in enumerate(result.passages, start=1)
    ]

    return ChatResponse(
        answer=result.answer,
        grounded=result.grounded,
        segments=segments,
        citations=citations,
        notice=result.notice,
    )
