"""Authenticated playback for video course items."""

import logging

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from app.api.deps import get_conn, get_current_user_streaming
from app.config import get_settings
from app.services import subscriptions
from rag import bunny


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/videos", tags=["Videos"])


@router.get("/{video_id}/video")
def stream_video(
    video_id: int,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user_streaming),
):
    """Authorize and stream a `course_items` video through Bunny."""

    allowed, doctor_id, title = subscriptions.can_watch_video(
        conn, current_user["id"], video_id
    )
    if doctor_id is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if get_settings().enforce_subscriptions and not allowed:
        raise HTTPException(status_code=402, detail={
            "error": "subscription_required",
            "message": "محتاج تشترك مع المحاضر عشان تفتح الفيديو ده.",
            "video_id": video_id,
            "video_title": title,
            "doctor_id": doctor_id,
        })

    with conn.cursor() as cur:
        cur.execute("""
            SELECT video_provider, video_ref
            FROM course_items
            WHERE id = %s AND type = 'video'
        """, (video_id,))
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Video not found")

    provider, video_ref = row
    if provider not in (None, "bunny"):
        raise HTTPException(status_code=409, detail={
            "error": "unsupported_video_provider",
            "video_provider": provider,
        })

    try:
        video = bunny.get_video(video_ref)
        if not bunny.is_finished(video):
            raise HTTPException(status_code=409, detail={
                "error": "video_not_ready",
                "message": "الفيديو لسه بيتجهز، جرّب تاني بعد شوية.",
                "bunny_status": bunny.status_name(video),
            })
        playback_url = bunny.rendition_url(video, prefer="highest")
    except HTTPException:
        raise
    except (bunny.BunnyError, requests.RequestException, RuntimeError) as error:
        logger.error("Bunny playback lookup failed video=%s: %s", video_id, error)
        raise HTTPException(
            status_code=502,
            detail="تعذر تحميل الفيديو دلوقتي. جرّب تاني بعد شوية.",
        ) from error

    return RedirectResponse(
        playback_url,
        status_code=307,
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
        },
    )
