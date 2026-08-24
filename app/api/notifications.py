"""In-site notifications.

Polled by the browser rather than pushed: the requirement is "tell them next time
they are on the site", which a table with a `read_at` column does exactly, with
no broker to keep running and nothing lost if the browser was closed when the
report was written.

An inbox is addressed to exactly one person, so no route here takes a user id.
Whose inbox it is is settled by the token.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_conn, get_current_user
from app.schemas.notifications import Inbox
from app.services import notifications


router = APIRouter(
    prefix="/api/notifications",
    tags=["Notifications"],
)


@router.get("", response_model=Inbox)
def list_notifications(
    unread_only: bool = False,
    limit: int = 30,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    """The caller's own notifications, newest first, with the unread count.

    A student sees reports about themselves, a doctor sees one per student who
    finished something on their course — the difference falls out of who the
    notification was written to, not out of a parameter.
    """

    return Inbox(
        **notifications.inbox(
            conn, current_user["id"], unread_only, min(limit, 100)
        )
    )


@router.post("/{notification_id}/read")
def read_notification(
    notification_id: int,
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):
    """Mark one of the caller's own notifications read.

    Scoped by user as well as by id: ids are sequential, and without the scope
    anybody could mark somebody else's unread report as read and quietly make it
    disappear from their bell.
    """

    changed = notifications.mark_read(
        conn,
        notification_id=notification_id,
        user_id=current_user["id"],
    )

    # Nothing changed means the notification is not theirs, does not exist, or
    # was already read. The three are not distinguished: saying which would
    # confirm the existence of other people's notifications.
    return {"marked_read": changed}


@router.post("/read-all")
def read_all(
    conn=Depends(get_conn),
    current_user=Depends(get_current_user),
):

    return {
        "marked_read": notifications.mark_read(conn, user_id=current_user["id"])
    }
