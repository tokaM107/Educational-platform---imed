"""Response models for the instructor's post-exam view.

Percentages are `float | None`: None means there was no denominator — nobody
answered that question yet — which is a different statement from 0% and is shown
differently.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class OptionCount(BaseModel):
    """One option offered by a question, and how many chose it.

    Every option is listed even at zero picks: a distractor nobody touches is a
    finding of its own, because the question is really a narrower choice than it
    looks.
    """

    option: str

    # None for a recorded choice matching no option on the question — kept
    # visible rather than dropped.
    text: str | None

    is_correct: bool
    picks: int
    first_picks: int
    percent: float | None


class TopDistractor(BaseModel):
    """The wrong option the class converged on, when one of them dominates."""

    option: str
    text: str | None
    percent: float | None
    picks: int


class QuestionStat(BaseModel):
    question_id: int
    stem: str
    topic: str
    difficulty: str | None

    students_answered: int
    students_correct: int
    attempts: int

    # A question counts as correct for a student if any attempt was right.
    correct_percent: float | None

    # The stricter reading: right first time, retries excluded.
    first_attempt_percent: float | None

    # Above 1.0 means the class needed more than one go on average.
    attempts_per_student: float | None

    # False when too few students answered for the percentage to mean anything.
    reliable: bool

    # "as_labelled" | "easier_than_labelled" | "harder_than_labelled" | None
    calibration: str | None

    # The answer key. This is why /api/exams is an instructor endpoint and must
    # be behind authentication before launch.
    correct_option: str | None
    options: list[OptionCount] = Field(default_factory=list)

    # Attempts the distribution was built from. Lower than `attempts` wherever
    # answers predate the selected_option column, so a partial picture never
    # reads as a complete one.
    answers_recorded: int = 0

    top_distractor: TopDistractor | None = None


class TopicStat(BaseModel):
    topic: str
    questions: int
    students_answered: int
    attempts: int
    correct_percent: float | None
    reliable: bool


class StudentStat(BaseModel):
    student_id: int
    name: str
    email: str

    questions_answered: int
    questions_correct: int
    attempts: int

    # Out of every question in the exam: unanswered counts as wrong.
    score_percent: float | None

    # Out of what they actually attempted.
    accuracy_percent: float | None

    completed: bool


class ScoreBucket(BaseModel):
    low: int
    high: int
    students: int


class ExamSummary(BaseModel):
    total_questions: int

    # None when the lecture belongs to no course, so there is no class to count.
    cohort_size: int | None
    students_attempted: int
    students_completed: int
    participation_percent: float | None
    total_attempts: int

    average_score: float | None
    median_score: float | None
    average_score_completed: float | None
    average_accuracy: float | None

    pass_mark: float
    pass_rate: float | None

    # Excluded from every figure above, reported so nothing vanishes silently.
    attempts_from_non_enrolled: int


class ExamStats(BaseModel):
    lecture_id: int
    lecture_title: str
    course_id: int | None
    course_title: str | None
    doctor_name: str | None
    pass_mark: float

    summary: ExamSummary
    score_distribution: list[ScoreBucket] = Field(default_factory=list)
    questions: list[QuestionStat] = Field(default_factory=list)
    topics: list[TopicStat] = Field(default_factory=list)
    roster: list[StudentStat] = Field(default_factory=list)

    hardest: int | None = None
    easiest: int | None = None


class ExamListing(BaseModel):
    """One lecture that has questions, for the picker."""

    lecture_id: int
    lecture_title: str
    course_id: int | None
    course_title: str | None
    total_questions: int
    students_attempted: int
    attempts: int
    last_answered: datetime | None
