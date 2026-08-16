from pydantic import BaseModel


class Lecture(BaseModel):
    id: int
    title: str
    doctor_id: int

    # Where the browser should load the video from (served by this API)
    video_url: str

    # Ingest status, handy for the demo UI
    chunk_count: int
    duration_ts: int
    has_video: bool
