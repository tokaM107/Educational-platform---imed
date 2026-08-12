from fastapi import APIRouter


router = APIRouter(
    prefix="/content",
    tags=["Content"]
)


@router.get("/lectures")
def get_lectures():

    # Temporary mock data
    # Later this will come from PostgreSQL.

    return [
        {
            "id": 1,
            "title": "Introduction to Stroke",
            "doctor_id": 1,
            "video_url": "https://example.com/video1"
        },
        {
            "id": 2,
            "title": "Stroke Risk Factors",
            "doctor_id": 1,
            "video_url": "https://example.com/video2"
        }
    ]