from pydantic import BaseModel


class Question(BaseModel):
    id: int
    lecture_id: int
    topic_id: int | None
    stem: str
    options: list[str]
    difficulty: str | None


class QuestionAttemptCreate(BaseModel):
    student_id: int
    selected_option: str