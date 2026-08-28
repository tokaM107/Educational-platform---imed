"""Authenticated lecture-scoped conversational RAG endpoints."""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from types import SimpleNamespace
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from psycopg.types.json import Jsonb

from app.api.deps import get_conn, get_current_user, get_tutor, require_student
from app.config import get_settings
from app.schemas.chat import (
    ChatMessageCreate, ChatRequest, ChatResponse, ChatSession, ChatSessionCreate,
    ChatTurnResponse, Citation, StoredChatMessage, TokenUsage, VideoSegment,
)
from app.services import prompts, retrieval, subscriptions
from app.services.chat_memory import (
    MemoryMessage, bound_text, messages_to_summarize,
)
from app.services.token_budget import (
    PromptTooLarge, ProviderTokenCounter, effective_input_limit,
)
from app.services.prompts import to_stamp


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Tutor"])

MESSAGE_COLUMNS = """
    id, session_id, message_order, role, content, standalone_query, citations,
    token_count, tokenizer_name, model_name, prompt_version, input_tokens,
    output_tokens, status, failure_code, grounded, created_at
"""


@lru_cache
def _default_counter():
    settings = get_settings()
    return ProviderTokenCounter(settings.chat_model)


def _segments(result):
    return [VideoSegment(
        lecture_id=segment.lecture_id,
        lecture_title=segment.lecture_title,
        video_url=f"/api/lectures/{segment.lecture_id}/video",
        start_ts=segment.start_ts, end_ts=segment.end_ts,
        start_label=to_stamp(segment.start_ts), end_label=to_stamp(segment.end_ts),
    ) for segment in result.segments]


def _citations(result):
    return [Citation(
        index=index, chunk_id=passage.chunk_id, lecture_id=passage.lecture_id,
        start_ts=passage.start_ts, end_ts=passage.end_ts, text=passage.text,
        distance=round(passage.distance, 4),
    ) for index, passage in enumerate(result.passages, start=1)]


def _stored_message(row):
    return StoredChatMessage(
        id=row[0], session_id=row[1], message_order=row[2], role=row[3],
        content=row[4], standalone_query=row[5], citations=row[6] or [],
        token_count=row[7], tokenizer_name=row[8], model_name=row[9],
        prompt_version=row[10], input_tokens=row[11], output_tokens=row[12],
        status=row[13], failure_code=row[14], grounded=row[15], created_at=row[16],
    )


def _require_lecture_access(conn, student_id, lecture_id):
    allowed, doctor_id, title = subscriptions.can_watch(conn, student_id, lecture_id)
    if doctor_id is None:
        raise HTTPException(status_code=404, detail="Lecture not found")
    if get_settings().enforce_subscriptions and not allowed:
        raise HTTPException(status_code=402, detail={
            "error": "subscription_required",
            "message": "محتاج تشترك مع المحاضر عشان تستخدم مساعد المحاضرة.",
            "lecture_id": lecture_id, "lecture_title": title, "doctor_id": doctor_id,
        })
    return title


def _memory_rows(rows):
    return [MemoryMessage(
        id=row[0], message_order=row[1], role=row[2], content=row[3],
        token_count=row[4], tokenizer_name=row[5], citations=row[6] or [],
    ) for row in reversed(rows)]


def _turn_response(user_message, assistant_message, segments=None, notice=None,
                   prompt_count=None, estimated=False, prompt_tokenizer_name=None):
    citations = assistant_message.citations or []
    input_values = [value for value in (
        user_message.input_tokens, assistant_message.input_tokens
    ) if value is not None]
    output_values = [value for value in (
        user_message.output_tokens, assistant_message.output_tokens
    ) if value is not None]
    return ChatTurnResponse(
        user_message=user_message, assistant_message=assistant_message,
        original_question=user_message.content,
        standalone_query=user_message.standalone_query or user_message.content,
        answer=assistant_message.content, citations=citations,
        grounded=bool(assistant_message.grounded), segments=segments or [],
        notice=notice, insufficient_evidence=not bool(assistant_message.grounded),
        token_usage=TokenUsage(
            input_tokens=sum(input_values) if input_values else None,
            output_tokens=sum(output_values) if output_values else None,
            assembled_prompt_tokens=prompt_count,
            tokenizer_name=prompt_tokenizer_name or assistant_message.tokenizer_name,
            estimated=estimated,
        ),
    )


@router.post("/chat/sessions", response_model=ChatSession, status_code=201)
def create_chat_session(data: ChatSessionCreate, conn=Depends(get_conn),
                        current_user=Depends(require_student)):
    student_id = current_user["id"]
    _require_lecture_access(conn, student_id, data.lecture_id)
    title = data.title.strip() if data.title and data.title.strip() else None
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO chat_sessions (student_id, lecture_id, title)
            VALUES (%s, %s, %s)
            RETURNING id, student_id, lecture_id, title, created_at, updated_at,
                      summary_token_count
        """, (student_id, data.lecture_id, title))
        row = cur.fetchone()
    conn.commit()
    return ChatSession(id=row[0], student_id=row[1], lecture_id=row[2], title=row[3],
                       created_at=row[4], updated_at=row[5], summary_token_count=row[6])


@router.get("/chat/sessions", response_model=list[ChatSession])
def list_chat_sessions(lecture_id: int | None = None,
                       limit: int = Query(20, ge=1, le=100),
                       offset: int = Query(0, ge=0), conn=Depends(get_conn),
                       current_user=Depends(require_student)):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, student_id, lecture_id, title, created_at, updated_at,
                   summary_token_count
            FROM chat_sessions
            WHERE student_id = %s AND (%s::int IS NULL OR lecture_id = %s)
            ORDER BY updated_at DESC, id DESC
            LIMIT %s OFFSET %s
        """, (current_user["id"], lecture_id, lecture_id, limit, offset))
        rows = cur.fetchall()
    return [ChatSession(id=row[0], student_id=row[1], lecture_id=row[2], title=row[3],
                        created_at=row[4], updated_at=row[5],
                        summary_token_count=row[6]) for row in rows]


@router.get("/chat/sessions/{session_id}/messages",
            response_model=list[StoredChatMessage])
def get_chat_messages(session_id: UUID, limit: int = Query(50, ge=1, le=200),
                      offset: int = Query(0, ge=0), conn=Depends(get_conn),
                      current_user=Depends(require_student)):
    with conn.cursor() as cur:
        cur.execute("SELECT lecture_id FROM chat_sessions WHERE id = %s AND student_id = %s",
                    (session_id, current_user["id"]))
        session = cur.fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        _require_lecture_access(conn, current_user["id"], session[0])
        cur.execute(f"""
            SELECT {MESSAGE_COLUMNS} FROM chat_messages
            WHERE session_id = %s ORDER BY message_order
            LIMIT %s OFFSET %s
        """, (session_id, limit, offset))
        rows = cur.fetchall()
    return [_stored_message(row) for row in rows]


def _update_summary(conn, session_id, previous_summary, checkpoint,
                    tutor, counter, settings):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, message_order, role, content, token_count, tokenizer_name,
                   citations
            FROM chat_messages
            WHERE session_id = %s AND status = 'completed' AND message_order > %s
            ORDER BY message_order
            LIMIT %s
        """, (session_id, checkpoint, settings.chat_history_load_limit))
        rows = cur.fetchall()
    messages = [MemoryMessage(
        id=row[0], message_order=row[1], role=row[2], content=row[3],
        token_count=row[4], tokenizer_name=row[5], citations=row[6] or [],
    ) for row in rows]
    candidates = messages_to_summarize(
        messages, checkpoint, settings.chat_summary_update_threshold, counter
    )
    if not candidates:
        return
    # Summary input is also bounded. Keep an oldest contiguous prefix so the
    # checkpoint can never jump over messages that were not summarized.
    input_limit = effective_input_limit(settings)
    bounded_candidates = []
    for candidate in candidates:
        proposal = bounded_candidates + [candidate]
        summary_prompt = prompts.build_summary_prompt(
            previous_summary, [(item.role, item.content) for item in proposal]
        )
        count = counter.count_text(prompts.SUMMARY_SYSTEM_INSTRUCTION).count
        count += counter.count_text(summary_prompt).count + 8
        if count > input_limit:
            break
        bounded_candidates = proposal
    candidates = bounded_candidates
    if not candidates:
        return
    summary_started_at = time.monotonic()
    try:
        generated = tutor._generate(
            system_instruction=prompts.SUMMARY_SYSTEM_INSTRUCTION,
            user_prompt=prompts.build_summary_prompt(
                previous_summary, [(item.role, item.content) for item in candidates]
            ),
            response_schema=prompts.ConversationSummaryReply,
            history=None,
            max_output_tokens=settings.chat_summary_max_output_tokens,
        )
        summary = bound_text(generated.parsed.summary, settings.chat_summary_tokens, counter)
        counted = counter.count_text(summary)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE chat_sessions
                SET memory_summary = %s, summary_token_count = %s,
                    summary_tokenizer_name = %s,
                    summarized_until_message_order = %s
                WHERE id = %s AND summarized_until_message_order = %s
            """, (summary, counted.count, counted.tokenizer_name,
                  candidates[-1].message_order, session_id, checkpoint))
        logger.info(
            "chat_summary_updated session=%s model=%s prompt_version=%s "
            "input_tokens=%s output_tokens=%s checkpoint=%s latency_ms=%s",
            session_id, generated.model_name, prompts.SUMMARY_PROMPT_VERSION,
            generated.input_tokens, generated.output_tokens,
            candidates[-1].message_order,
            round((time.monotonic() - summary_started_at) * 1000),
        )
    except Exception as error:
        logger.warning("rolling summary update failed for session %s: %s", session_id, error)


@router.post("/chat/sessions/{session_id}/messages",
             response_model=ChatTurnResponse, status_code=201)
def create_chat_message(session_id: UUID, data: ChatMessageCreate,
                        idempotency_key: str = Header(..., alias="Idempotency-Key",
                                                      min_length=8, max_length=255),
                        conn=Depends(get_conn), tutor=Depends(get_tutor),
                        current_user=Depends(require_student)):
    settings = get_settings()
    counter = getattr(tutor, "token_counter", None) or _default_counter()
    content_count = counter.count_text(data.content)
    if content_count.count > settings.chat_max_student_message_tokens:
        raise HTTPException(status_code=422, detail={
            "error": "message_too_large", "tokens": content_count.count,
            "maximum": settings.chat_max_student_message_tokens,
            "tokenizer_name": content_count.tokenizer_name,
        })

    with conn.cursor() as cur:
        # Transaction-scoped session serialization keeps allocation, history,
        # assistant ordering and summary checkpoint atomic under concurrency.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (str(session_id),))
        cur.execute("""
            SELECT lecture_id, memory_summary, summarized_until_message_order,
                   next_message_order
            FROM chat_sessions WHERE id = %s AND student_id = %s FOR UPDATE
        """, (session_id, current_user["id"]))
        session = cur.fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        lecture_id, summary, checkpoint, next_order = session
        lecture_title = _require_lecture_access(conn, current_user["id"], lecture_id)

        cur.execute("""
            SELECT id, content FROM chat_messages
            WHERE session_id = %s AND role = 'user' AND idempotency_key = %s
        """, (session_id, idempotency_key))
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] != data.content:
                raise HTTPException(status_code=409, detail={
                    "error": "idempotency_key_reused_with_different_message"
                })
            cur.execute(f"SELECT {MESSAGE_COLUMNS} FROM chat_messages WHERE id = %s",
                        (existing[0],))
            user_message = _stored_message(cur.fetchone())
            cur.execute(f"""
                SELECT {MESSAGE_COLUMNS} FROM chat_messages
                WHERE reply_to_message_id = %s AND role = 'assistant'
            """, (existing[0],))
            assistant_row = cur.fetchone()
            if assistant_row is None:
                raise HTTPException(status_code=409, detail={
                    "error": "request_in_progress_or_failed",
                    "user_message_id": existing[0],
                })
            assistant_message = _stored_message(assistant_row)
            restored_passages = [retrieval.Passage(
                chunk_id=item.chunk_id, lecture_id=item.lecture_id,
                lecture_title=lecture_title, text=item.text,
                start_ts=item.start_ts, end_ts=item.end_ts,
                distance=item.distance,
            ) for item in (assistant_message.citations or [])]
            restored = SimpleNamespace(segments=retrieval.to_segments(restored_passages))
            segments = _segments(restored)
            return _turn_response(user_message, assistant_message, segments=segments)

        cur.execute("""
            SELECT id, message_order, role, content, token_count, tokenizer_name,
                   citations
            FROM chat_messages
            WHERE session_id = %s AND status = 'completed'
            ORDER BY message_order DESC LIMIT %s
        """, (session_id, settings.chat_history_load_limit))
        history = _memory_rows(cur.fetchall())

        cur.execute("""
            UPDATE chat_sessions
            SET next_message_order = next_message_order + 2, updated_at = now()
            WHERE id = %s
        """, (session_id,))
        cur.execute(f"""
            INSERT INTO chat_messages
                (session_id, message_order, role, content, token_count,
                 tokenizer_name, status, idempotency_key)
            VALUES (%s, %s, 'user', %s, %s, %s, 'pending', %s)
            RETURNING {MESSAGE_COLUMNS}
        """, (session_id, next_order, data.content, content_count.count,
              content_count.tokenizer_name, idempotency_key))
        user_row = cur.fetchone()

    continuity_ids = []
    for message in reversed(history):
        if message.role == "assistant" and message.citations:
            continuity_ids = [item["chunk_id"] for item in message.citations[:3]]
            break

    started_at = time.monotonic()
    try:
        result = tutor.ask(
            conn, question=data.content, lecture_id=lecture_id, history=history,
            summary=bound_text(summary, settings.chat_summary_tokens, counter),
            continuity_chunk_ids=continuity_ids,
        )
    except PromptTooLarge as error:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE chat_messages SET status = 'failed', failure_code = 'prompt_too_large'
                WHERE id = %s
            """, (user_row[0],))
        conn.commit()
        raise HTTPException(status_code=422, detail=str(error))
    except Exception as error:
        logger.exception("chat generation failed session=%s", session_id)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE chat_messages SET status = 'failed', failure_code = 'generation_failed'
                WHERE id = %s
            """, (user_row[0],))
        conn.commit()
        raise HTTPException(status_code=502, detail="تعذر إنشاء الرد دلوقتي. جرّب تاني.") from error

    answer_count = counter.count_text(result.answer)
    citations = _citations(result)
    citation_payload = [citation.model_dump(mode="json") for citation in citations]
    with conn.cursor() as cur:
        cur.execute(f"""
            UPDATE chat_messages
            SET standalone_query = %s, status = 'completed', model_name = %s,
                prompt_version = %s, input_tokens = %s, output_tokens = %s
            WHERE id = %s
            RETURNING {MESSAGE_COLUMNS}
        """, (result.standalone_query or data.content, result.rewrite_model_name,
              prompts.REWRITE_PROMPT_VERSION, result.rewrite_input_tokens,
              result.rewrite_output_tokens, user_row[0]))
        saved_user = cur.fetchone()
        cur.execute(f"""
            INSERT INTO chat_messages
                (session_id, message_order, role, content, citations, token_count,
                 tokenizer_name, model_name, prompt_version, input_tokens,
                 output_tokens, status, reply_to_message_id, grounded)
            VALUES (%s, %s, 'assistant', %s, %s, %s, %s, %s, %s, %s, %s,
                    'completed', %s, %s)
            RETURNING {MESSAGE_COLUMNS}
        """, (session_id, next_order + 1, result.answer, Jsonb(citation_payload),
              answer_count.count, answer_count.tokenizer_name, result.model_name,
              result.prompt_version, result.input_tokens, result.output_tokens,
              user_row[0], result.grounded))
        saved_assistant = cur.fetchone()

    _update_summary(conn, session_id, summary, checkpoint, tutor, counter, settings)
    conn.commit()
    logger.info(
        "chat_completed session=%s rewrite_model=%s answer_model=%s "
        "prompt_version=%s rewrite_input_tokens=%s rewrite_output_tokens=%s "
        "answer_input_tokens=%s answer_output_tokens=%s grounded=%s latency_ms=%s",
        session_id, result.rewrite_model_name, result.model_name,
        result.prompt_version, result.rewrite_input_tokens,
        result.rewrite_output_tokens, result.input_tokens, result.output_tokens,
        result.grounded,
        round((time.monotonic() - started_at) * 1000),
    )
    return _turn_response(
        _stored_message(saved_user), _stored_message(saved_assistant),
        segments=_segments(result), notice=result.notice,
        prompt_count=result.prompt_token_count, estimated=result.prompt_tokens_estimated,
        prompt_tokenizer_name=result.prompt_tokenizer_name,
    )


@router.post("/chat", response_model=ChatResponse)
def chat(data: ChatRequest, conn=Depends(get_conn), tutor=Depends(get_tutor),
         current_user=Depends(get_current_user)):
    result = tutor.ask(
        conn, question=data.message, lecture_id=data.lecture_id,
        history=[(message.role, message.content) for message in data.history],
    )
    return ChatResponse(answer=result.answer, grounded=result.grounded,
                        segments=_segments(result), citations=_citations(result),
                        notice=result.notice)
