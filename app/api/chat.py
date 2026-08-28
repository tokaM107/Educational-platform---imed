"""The student-facing chatbot endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb

from app.api.deps import get_conn, get_current_user, get_tutor, require_student
from app.config import get_settings
from app.schemas.chat import (
    ChatMessageCreate,
    ChatRequest,
    ChatResponse,
    ChatSession,
    ChatSessionCreate,
    ChatTurnResponse,
    Citation,
    StoredChatMessage,
    VideoSegment,
)
from app.services import subscriptions
from app.services.prompts import to_stamp


router = APIRouter(
    prefix="/api",
    tags=["Tutor"],
)


def _segments(result):
    return [
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


def _citations(result):
    return [
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


def _stored_message(row):
    return StoredChatMessage(
        id=row[0],
        session_id=row[1],
        role=row[2],
        content=row[3],
        standalone_query=row[4],
        citations=row[5],
        created_at=row[6],
    )


def _require_lecture_access(conn, student_id, lecture_id):
    allowed, doctor_id, title = subscriptions.can_watch(conn, student_id, lecture_id)

    if doctor_id is None:
        raise HTTPException(status_code=404, detail="Lecture not found")

    if get_settings().enforce_subscriptions and not allowed:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "subscription_required",
                "message": "محتاج تشترك مع المحاضر عشان تستخدم مساعد المحاضرة.",
                "lecture_id": lecture_id,
                "lecture_title": title,
                "doctor_id": doctor_id,
            },
        )


@router.post("/chat/sessions", response_model=ChatSession, status_code=201)
def create_chat_session(
    data: ChatSessionCreate,
    conn=Depends(get_conn),
    current_user=Depends(require_student),
):
    """Create a lecture-scoped chat owned by the authenticated student."""

    student_id = current_user["id"]
    _require_lecture_access(conn, student_id, data.lecture_id)

    title = data.title.strip() if data.title and data.title.strip() else None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO chat_sessions (student_id, lecture_id, title)
            VALUES (%s, %s, %s)
            RETURNING id, student_id, lecture_id, title, created_at, updated_at
            """,
            (student_id, data.lecture_id, title),
        )
        row = cur.fetchone()

    conn.commit()
    return ChatSession(
        id=row[0],
        student_id=row[1],
        lecture_id=row[2],
        title=row[3],
        created_at=row[4],
        updated_at=row[5],
    )


@router.get(
    "/chat/sessions/{session_id}/messages",
    response_model=list[StoredChatMessage],
)
def get_chat_messages(
    session_id: UUID,
    conn=Depends(get_conn),
    current_user=Depends(require_student),
):
    """Return a thread only when it belongs to the authenticated student."""

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.id, m.session_id, m.role, m.content,
                   m.standalone_query, m.citations, m.created_at
            FROM chat_sessions AS s
            JOIN chat_messages AS m ON m.session_id = s.id
            WHERE s.id = %s AND s.student_id = %s
            ORDER BY m.created_at, m.id
            """,
            (session_id, current_user["id"]),
        )
        rows = cur.fetchall()

        if not rows:
            cur.execute(
                "SELECT 1 FROM chat_sessions WHERE id = %s AND student_id = %s",
                (session_id, current_user["id"]),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Chat session not found")

    return [_stored_message(row) for row in rows]


@router.post(
    "/chat/sessions/{session_id}/messages",
    response_model=ChatTurnResponse,
    status_code=201,
)
def create_chat_message(
    session_id: UUID,
    data: ChatMessageCreate,
    conn=Depends(get_conn),
    tutor=Depends(get_tutor),
    current_user=Depends(require_student),
):
    """Generate and persist the student's turn and the assistant's reply."""

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT lecture_id
            FROM chat_sessions
            WHERE id = %s AND student_id = %s
            """,
            (session_id, current_user["id"]),
        )
        session = cur.fetchone()

        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")

        lecture_id = session[0]
        _require_lecture_access(conn, current_user["id"], lecture_id)

        cur.execute(
            """
            SELECT role, content
            FROM (
                SELECT id, role, content, created_at
                FROM chat_messages
                WHERE session_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 6
            ) AS recent
            ORDER BY created_at, id
            """,
            (session_id,),
        )
        history = cur.fetchall()

    result = tutor.ask(
        conn,
        question=data.content,
        lecture_id=lecture_id,
        history=history,
    )
    segments = _segments(result)
    citations = _citations(result)
    citation_payload = [citation.model_dump() for citation in citations]

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO chat_messages
                (session_id, role, content, standalone_query)
            VALUES (%s, 'user', %s, %s)
            RETURNING id, session_id, role, content,
                      standalone_query, citations, created_at
            """,
            (session_id, data.content, data.content),
        )
        user_row = cur.fetchone()

        cur.execute(
            """
            INSERT INTO chat_messages (session_id, role, content, citations)
            VALUES (%s, 'assistant', %s, %s)
            RETURNING id, session_id, role, content,
                      standalone_query, citations, created_at
            """,
            (session_id, result.answer, Jsonb(citation_payload)),
        )
        assistant_row = cur.fetchone()

        cur.execute(
            "UPDATE chat_sessions SET updated_at = now() WHERE id = %s",
            (session_id,),
        )

    conn.commit()

    return ChatTurnResponse(
        user_message=_stored_message(user_row),
        assistant_message=_stored_message(assistant_row),
        grounded=result.grounded,
        segments=segments,
        notice=result.notice,
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

    return ChatResponse(
        answer=result.answer,
        grounded=result.grounded,
        segments=_segments(result),
        citations=_citations(result),
        notice=result.notice,
    )
