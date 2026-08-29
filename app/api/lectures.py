"""Lecture listing and authenticated local/Bunny video playback."""

import logging
from pathlib import Path

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from app.api.deps import get_conn, get_current_user, get_current_user_streaming
from app.config import get_settings
from app.schemas.lectures import Lecture
from app.services import subscriptions
from rag import bunny


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/lectures",
    tags=["Lectures"],
)


LIST_SQL = """
    SELECT
        l.id,
        l.title,
        l.doctor_id,
        l.video_url,
        l.bunny_video_id,
        COUNT(c.id) AS chunk_count,
        COALESCE(MAX(c.end_ts), 0) AS duration_ts
    FROM lectures AS l
    LEFT JOIN transcript_chunks AS c ON c.lecture_id = l.id
    {where}
    GROUP BY l.id
    ORDER BY l.id
"""


def resolve_video_path(video_url):
    """Map whatever is stored in lectures.video_url onto a file on disk.

    Accepts a bare file name, a path relative to the repo, or an absolute
    path, and always resolves inside data/videos so a bad row cannot be used
    to read arbitrary files.
    """

    settings = get_settings()

    if not video_url:
        return None

    candidate = settings.video_dir / Path(video_url).name

    return candidate if candidate.is_file() else None


def to_lecture(row):

    (lecture_id, title, doctor_id, video_url, bunny_video_id,
     chunk_count, duration_ts) = row

    return Lecture(
        id=lecture_id,
        title=title,
        doctor_id=doctor_id,
        video_url=f"/api/lectures/{lecture_id}/video",
        chunk_count=chunk_count,
        duration_ts=duration_ts,
        has_video=bool(bunny_video_id) or resolve_video_path(video_url) is not None,
    )


@router.get("", response_model=list[Lecture])
def list_lectures(
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):

    with conn.cursor() as cur:
        cur.execute(LIST_SQL.format(where=""))
        return [to_lecture(row) for row in cur.fetchall()]


@router.get("/{lecture_id}", response_model=Lecture)
def get_lecture(
    lecture_id: int,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):

    with conn.cursor() as cur:

        cur.execute(
            LIST_SQL.format(where="WHERE l.id = %s"),
            (lecture_id,),
        )

        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Lecture not found")

    return to_lecture(row)


@router.get("/{lecture_id}/video")
def stream_video(
    lecture_id: int,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user_streaming),
):
    """Authorize playback, then use Bunny or the legacy local-file fallback.

    Behind the paywall: watching needs a subscription to the lecture's teacher,
    and the viewer is now the authenticated user rather than a `student_id` the
    caller chose. That is the change that turns this from a notice into a lock —
    the check itself is the one that was always here.

    Authentication comes from `get_current_user_streaming`, which additionally
    accepts the token as a query parameter, because a <video> element issues its
    own request and cannot be given an Authorization header. Set
    ENFORCE_SUBSCRIPTIONS=false to turn the paywall off in development; the
    login requirement stays either way. Bunny-hosted lectures redirect to the
    highest encoded rendition so the CDN, rather than this API process, serves
    range requests and video bytes.
    """

    student_id = current_user["id"]

    if get_settings().enforce_subscriptions:

        allowed, doctor_id, title = subscriptions.can_watch(conn, student_id, lecture_id)

        if not allowed:
            raise HTTPException(
                # 402: the lecture exists and is fine — it has not been paid for.
                status_code=402,
                detail={
                    "error": "subscription_required",
                    "message": "محتاج تشترك مع المحاضر عشان تفتح المحاضرة دي.",
                    "lecture_id": lecture_id,
                    "lecture_title": title,
                    "doctor_id": doctor_id,
                },
            )

    with conn.cursor() as cur:

        cur.execute(
            "SELECT video_url, bunny_video_id FROM lectures WHERE id = %s",
            (lecture_id,),
        )

        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Lecture not found")

    video_url = row[0]
    # Keep compatibility with older test doubles while the real SELECT always
    # returns both columns.
    bunny_video_id = row[1] if len(row) > 1 else None

    if bunny_video_id:
        try:
            video = bunny.get_video(bunny_video_id)
            if not bunny.is_finished(video):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "video_not_ready",
                        "message": "الفيديو لسه بيتجهز على Bunny، جرّب تاني بعد شوية.",
                        "bunny_status": bunny.status_name(video),
                    },
                )
            playback_url = bunny.rendition_url(video, prefer="highest")
        except HTTPException:
            raise
        except (bunny.BunnyError, requests.RequestException, RuntimeError) as error:
            logger.error(
                "Bunny playback lookup failed lecture=%s video=%s: %s",
                lecture_id,
                bunny_video_id,
                error,
            )
            raise HTTPException(
                status_code=502,
                detail="تعذر تحميل الفيديو من Bunny دلوقتي. جرّب تاني بعد شوية.",
            ) from error

        return RedirectResponse(
            playback_url,
            status_code=307,
            headers={
                "Cache-Control": "private, no-store",
                "Referrer-Policy": "no-referrer",
            },
        )

    path = resolve_video_path(video_url)

    if path is None:
        raise HTTPException(
            status_code=404,
            detail="Video file not found under data/videos",
        )

    return FileResponse(
        path,
        media_type="video/mp4",
        filename=path.name,
        content_disposition_type="inline",
    )
