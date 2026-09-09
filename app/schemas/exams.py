"""Teacher assessment analytics response contracts."""

from datetime import datetime
from pydantic import BaseModel, Field


class DescriptiveStats(BaseModel):
    mean: float | None = None
    median: float | None = None
    p25: float | None = None
    p75: float | None = None
    sample_size: int = 0


class OptionStat(BaseModel):
    option_id: int
    text: str
    is_correct: bool
    order: int
    picks: int
    percent: float | None = None
    high_performer_percent: float | None = None
    low_performer_percent: float | None = None
    classification: str


class ResponseTimeStat(DescriptiveStats):
    correct_median: float | None = None
    incorrect_median: float | None = None
    first_attempt_median: float | None = None


class QuestionStat(BaseModel):
    question_id: int
    stem: str
    type: str
    points: int
    order: int
    topic: str
    learning_objective: str | None = None
    declared_difficulty: str | None = None
    students_answered: int
    students_shown: int
    students_correct: int
    attempts: int
    correct_percent: float | None = None
    first_attempt_percent: float | None = None
    final_accuracy: float | None = None
    retry_gain: float | None = None
    retry_rate: float | None = None
    attempts_per_student: float | None = None
    skip_timeout_rate: float | None = None
    empirical_difficulty: str | None = None
    confidence: str
    discrimination: float | None = None
    discrimination_label: str
    discrimination_sample_size: int
    response_time: ResponseTimeStat
    options: list[OptionStat] = Field(default_factory=list)
    review_priority_score: float
    review_priority: str
    review_reasons: list[str] = Field(default_factory=list)


class TopicStat(BaseModel):
    topic: str
    questions: int
    participating_students: int
    attempts: int
    first_attempt_accuracy: float | None = None
    final_accuracy: float | None = None
    retry_gain: float | None = None
    retry_rate: float | None = None
    median_response_time: float | None = None
    students_below_mastery_percent: float | None = None
    confidence: str
    conclusive: bool
    evidence_note: str | None = None


class StudentStat(BaseModel):
    student_id: int
    name: str
    email: str
    started: bool
    completed: bool
    questions_answered: int
    questions_correct: int
    score_percent: float | None = None
    attempted_accuracy: float | None = None
    first_attempt_accuracy: float | None = None
    retries: int
    retry_rate: float | None = None
    exam_attempts: int
    duration_seconds: float | None = None
    weakest_topics: list[str] = Field(default_factory=list)
    support_category: str
    primary_issue: str


class ScoreBucket(BaseModel):
    low: int
    high: int
    students: int


class ExamSummary(BaseModel):
    total_questions: int
    cohort_size: int
    students_attempted: int
    students_completed: int
    participation_percent: float | None = None
    completion_percent: float | None = None
    abandonment_percent: float | None = None
    average_score: float | None = None
    median_score: float | None = None
    pass_mark: float
    pass_rate: float | None = None
    average_accuracy: float | None = None
    first_attempt_accuracy: float | None = None
    final_accuracy: float | None = None
    learning_gain: float | None = None
    average_attempts_per_question: float | None = None
    students_needing_retry_percent: float | None = None
    total_response_events: int
    attempts_from_non_enrolled: int
    duration: DescriptiveStats
    response_time: DescriptiveStats
    difficulty_distribution: dict[str, int]
    confidence: str


class MisconceptionCluster(BaseModel):
    topic: str
    questions: int
    dominant_distractor_questions: int
    low_first_attempt_questions: int
    confidence: str
    message: str


class ExamStats(BaseModel):
    exam_id: int
    exam_title: str
    course_id: int
    course_title: str
    doctor_name: str
    pass_mark: float
    summary: ExamSummary
    score_distribution: list[ScoreBucket] = Field(default_factory=list)
    questions: list[QuestionStat] = Field(default_factory=list)
    topics: list[TopicStat] = Field(default_factory=list)
    roster: list[StudentStat] = Field(default_factory=list)
    misconception_clusters: list[MisconceptionCluster] = Field(default_factory=list)
    teaching_actions: list[str] = Field(default_factory=list)
    hardest: int | None = None
    easiest: int | None = None
    methodology: dict


class ExamListing(BaseModel):
    exam_id: int
    exam_title: str
    course_id: int
    course_title: str
    total_questions: int
    students_attempted: int
    attempts: int
    last_answered: datetime | None = None
