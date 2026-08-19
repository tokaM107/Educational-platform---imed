"""In-site notifications.

Polled by the browser rather than pushed: the requirement is "tell them next time
they are on the site", which a table with a `read_at` column does exactly, with
no broker to keep running and nothing lost if the browser was closed when the
report was written.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_conn
from app.schemas.notifications import Inbox
from app.services import notifications


router = APIRouter(
    prefix="/api/notifications",
    tags=["Notifications"],
)


@router.get("", response_model=Inbox)
def list_notifications(
    user_id: int,
    unread_only: bool = False,
    limit: int = 30,
    conn=Depends(get_conn),
):
    """This user's notifications, newest first, with the unread count.

    `user_id` is whoever is looking: a student sees reports about themselves, a
    doctor sees one per student who finished something on their course.
    """

    return Inbox(**notifications.inbox(conn, user_id, unread_only, min(limit, 100)))


@router.post("/{notification_id}/read")
def read_notification(notification_id: int, conn=Depends(get_conn)):

    changed = notifications.mark_read(conn, notification_id=notification_id)

    return {"marked_read": changed}


@router.post("/read-all")
def read_all(user_id: int, conn=Depends(get_conn)):

    return {"marked_read": notifications.mark_read(conn, user_id=user_id)}
