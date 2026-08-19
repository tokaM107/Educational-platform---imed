"""Response models for the weekly report.

Percentages are `float | None` throughout: None means the denominator is not
known (a lecture with no transcript has no length, a student with no sessions
has nothing to be away from), which is a different statement from 0 and is
printed differently on the page.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field


class Student(BaseModel):
    id: int
    name: str
    email: str


class Course(BaseModel):
    id: int
    title: str
    doctor_name: str


class Week(BaseModel):
    start: date
    end: date
    days: int


class ReportSubject(BaseModel):
    """A student/course pair a report can be built for."""

    student_id: int
    student_name: str
    course_id: int
    course_title: str
    doctor_name: str

    # None when this student has never opened a lecture on this course.
    last_activity: datetime | None


class Span(BaseModel):
    """A stretch of a lecture, in video seconds, with HH:MM:SS labels."""

    start: float
    end: float
    seconds: float
    start_label: str
    end_label: str


class QuestionScore(BaseModel):
    """Counted per question, not per attempt.

    Answering the same question wrong and then right is one question learned;
    `attempts` keeps the retries visible so both readings stay available.
    """

    questions_attempted: int
    questions_correct: int
    attempts: int
    accuracy: float | None


class TopicScore(BaseModel):
    topic: str
    questions_attempted: int
    questions_correct: int
    accuracy: float | None

    # False when there were too few questions to call it a strength or a gap.
    conclusive: bool


class LectureLine(BaseModel):
    lecture_id: int
    title: str
    duration_seconds: float

    # False when the lecture has no transcript, so its length is unknown: it can
    # be watched but not scored, and it stays out of every coverage denominator.
    duration_known: bool

    opened: bool
    sessions: int
    completed: bool
    first_opened: datetime | None
    last_opened: datetime | None

    # Time the video was running. Exceeds the lecture length when stretches
    # were replayed, which is why coverage is reported next to it.
    watch_time_seconds: float
    watch_percentage: float | None

    # How much of the lecture was seen at least once — rewatching counted once.
    covered_seconds: float
    coverage_percentage: float | None

    # First event to last, pauses and absences included.
    session_duration_seconds: float

    # Time the lecture page was not visible. Not watch time, and not a claim
    # about what the student was doing instead.
    time_away_seconds: float
    time_away_rate: float | None

    pause_count: int
    seek_count: int

    skipped_spans: list[Span] = Field(default_factory=list)
    rewatched_spans: list[Span] = Field(default_factory=list)

    questions: QuestionScore
    weak_topics: list[str] = Field(default_factory=list)


class DayLoad(BaseModel):
    """One day of the week, empty days included."""

    date: date
    watch_time_seconds: float
    active: bool


class Totals(BaseModel):
    lectures_registered: int
    lectures_opened: int
    lectures_completed: int
    lectures_untouched: int

    # Registered lectures with no transcript, hence no length. Excluded from
    # `lecture_material_seconds` and from `coverage_percentage`.
    lectures_without_length: int

    lecture_material_seconds: float
    watch_time_seconds: float
    covered_seconds: float
    coverage_percentage: float | None
    session_duration_seconds: float
    time_away_seconds: float
    time_away_rate: float | None

    pause_count: int
    seek_count: int
    active_days: int
    week_days: int
    daily: list[DayLoad] = Field(default_factory=list)

    questions_attempted: int
    questions_correct: int
    attempts: int
    accuracy: float | None


class FocusPoint(BaseModel):
    lecture: str
    what: str
    why: str


class Narrative(BaseModel):
    """The generated half. Everything else on the page is measured."""

    headline: str
    summary: str
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    focus: list[FocusPoint] = Field(default_factory=list)
    advice: list[str] = Field(default_factory=list)


class WeeklyReport(BaseModel):
    """One report. The same shape whatever produced it.

    A weekly check-in, a module a student has just finished and a quiz they have
    just completed all come out of the same Stage-1 figures; `kind` says which
    occasion it was written for, and is what the page and the prompt vary on.
    """

    # "weekly" | "module" | "exam"
    kind: str = "weekly"

    # Set on a stored report — the ones a completion froze. None for a weekly
    # report, which is recomputed on every read.
    report_id: int | None = None

    # Set on an exam report: the lecture whose questions were just finished.
    lecture_id: int | None = None
    lecture_title: str | None = None

    generated_at: datetime
    week: Week
    student: Student

    # None when the student is enrolled on nothing — an honest empty report
    # rather than a 404, because that is itself the finding.
    course: Course | None
    totals: Totals | None

    lectures: list[LectureLine] = Field(default_factory=list)
    topics: list[TopicScore] = Field(default_factory=list)
    strengths: list[TopicScore] = Field(default_factory=list)
    weaknesses: list[TopicScore] = Field(default_factory=list)

    # None when the model was unreachable; `notice` then says so and every
    # measured number is still present.
    narrative: Narrative | None = None
    notice: str | None = None
