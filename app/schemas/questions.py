from pydantic import BaseModel


class Question(BaseModel):
    id: int
    lecture_id: int
    topic_id: int | None
    stem: str
    options: list[str]
    difficulty: str | None


class QuestionAttemptCreate(BaseModel):
    """An answer to one question.

    No `student_id`: the attempt is recorded against whoever is authenticated.
    Attempts are what the exam statistics and the "what did they get wrong"
    half of the report are built from, so a body naming its own student could
    write answers — right or wrong — into another student's record.
    """

    selected_option: str
