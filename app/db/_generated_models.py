# AUTO-GENERATED — DO NOT EDIT — run `make db-gen`
#
# Reflected from the database by scripts/gen_models.py. Every hand-written
# addition — relationships, helpers, business logic, the pgvector overrides —
# belongs in app/db/models.py, which imports from here.
#
# CI regenerates this file against a database built from the migrations and
# fails if the result differs from what is committed. A diff here means the
# database moved and the migrations did not, or the other way round.

from typing import Any, Optional
import datetime
import decimal
import uuid

from pgvector.sqlalchemy.vector import VECTOR
from sqlalchemy import BigInteger, Boolean, CHAR, CheckConstraint, Date, DateTime, Double, ForeignKeyConstraint, Index, Integer, Numeric, PrimaryKeyConstraint, SmallInteger, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class Admins(Base):
    __tablename__ = 'admins'
    __table_args__ = (
        CheckConstraint("role::text = ANY (ARRAY['admin'::character varying, 'super_admin'::character varying]::text[])", name='admins_role_check'),
        PrimaryKeyConstraint('id', name='admins_pkey'),
        UniqueConstraint('email', name='admins_email_key'),
        {'comment': 'Staff accounts (admin / super_admin). Owned by the NestJS API, '
                'entirely separate from the shared users table. RLS enabled with '
                'no policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'admin'::character varying"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    access_code_batches: Mapped[list['AccessCodeBatches']] = relationship('AccessCodeBatches', back_populates='admins')
    access_codes: Mapped[list['AccessCodes']] = relationship('AccessCodes', back_populates='admins')
    courses: Mapped[list['Courses']] = relationship('Courses', back_populates='admins')
    enrollments: Mapped[list['Enrollments']] = relationship('Enrollments', back_populates='admins')


class CollegeStages(Base):
    __tablename__ = 'college_stages'
    __table_args__ = (
        CheckConstraint('year_number >= 1 AND year_number <= 7', name='college_stages_year_number_check'),
        PrimaryKeyConstraint('id', name='college_stages_pkey'),
        UniqueConstraint('faculty', 'year_number', name='college_stages_faculty_year_number_key'),
        Index('idx_college_stages_display_order', 'display_order'),
        {'comment': 'College (university) stages (FR-2.2), e.g. "Medicine — 1st year". '
                'Owned by the NestJS API. RLS enabled with no policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name_en: Mapped[str] = mapped_column(String(255), nullable=False)
    faculty: Mapped[str] = mapped_column(String(50), nullable=False)
    year_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('true'))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    name_ar: Mapped[Optional[str]] = mapped_column(String(255))

    categories: Mapped[list['Categories']] = relationship('Categories', back_populates='college_stage')


class EssayGradeReviews(Base):
    __tablename__ = 'essay_grade_reviews'
    __table_args__ = (
        CheckConstraint("(decision::text = ANY (ARRAY['approve_provisional'::character varying, 'override_score'::character varying]::text[])) AND final_score IS NOT NULL AND final_score >= 0::numeric OR decision::text = 'return_for_regrade'::text AND final_score IS NULL", name='essay_grade_reviews_decision_check'),
        CheckConstraint("decision::text = ANY (ARRAY['approve_provisional'::character varying, 'override_score'::character varying, 'return_for_regrade'::character varying]::text[])", name='essay_grade_reviews_decision_value_check'),
        CheckConstraint('notes IS NULL OR char_length(notes) <= 10000', name='essay_grade_reviews_notes_check'),
        ForeignKeyConstraint(['grading_run_id'], ['public.essay_grading_runs.id'], ondelete='RESTRICT', name='essay_grade_reviews_grading_run_id_fkey'),
        ForeignKeyConstraint(['reviewer_id'], ['public.users.id'], ondelete='RESTRICT', name='essay_grade_reviews_reviewer_id_fkey'),
        ForeignKeyConstraint(['submission_id'], ['public.essay_submissions.id'], ondelete='RESTRICT', name='essay_grade_reviews_submission_id_fkey'),
        ForeignKeyConstraint(['supersedes_review_id'], ['public.essay_grade_reviews.id'], ondelete='RESTRICT', name='essay_grade_reviews_supersedes_review_id_fkey'),
        PrimaryKeyConstraint('id', name='essay_grade_reviews_pkey'),
        Index('idx_essay_grade_reviews_reviewer', 'reviewer_id', 'reviewed_at'),
        Index('idx_essay_grade_reviews_submission', 'submission_id', 'reviewed_at', 'id'),
        {'comment': 'Append-only doctor decisions. Superseding a decision inserts '
                'another row; it never edits history.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    grading_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reviewer_id: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    reviewed_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    final_score: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(10, 2))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    supersedes_review_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    grading_run: Mapped['EssayGradingRuns'] = relationship('EssayGradingRuns', back_populates='essay_grade_reviews')
    reviewer: Mapped['Users'] = relationship('Users', back_populates='essay_grade_reviews')
    submission: Mapped['EssaySubmissions'] = relationship('EssaySubmissions', foreign_keys=[submission_id], back_populates='essay_grade_reviews_submission')
    supersedes_review: Mapped[Optional['EssayGradeReviews']] = relationship('EssayGradeReviews', remote_side=[id], back_populates='supersedes_review_reverse')
    supersedes_review_reverse: Mapped[list['EssayGradeReviews']] = relationship('EssayGradeReviews', remote_side=[supersedes_review_id], back_populates='supersedes_review')
    essay_submissions_final_review: Mapped[list['EssaySubmissions']] = relationship('EssaySubmissions', foreign_keys='[EssaySubmissions.final_review_id]', back_populates='final_review')


class EssayGradingRuns(Base):
    __tablename__ = 'essay_grading_runs'
    __table_args__ = (
        CheckConstraint('evaluator_input_tokens IS NULL OR evaluator_input_tokens >= 0', name='essay_grading_runs_evaluator_input_tokens_check'),
        CheckConstraint('evaluator_latency_ms IS NULL OR evaluator_latency_ms >= 0', name='essay_grading_runs_evaluator_latency_ms_check'),
        CheckConstraint('evaluator_output_tokens IS NULL OR evaluator_output_tokens >= 0', name='essay_grading_runs_evaluator_output_tokens_check'),
        CheckConstraint('evaluator_retry_count >= 0', name='essay_grading_runs_evaluator_retry_count_check'),
        CheckConstraint("jsonb_typeof(evaluator_retry_errors) = 'array'::text", name='essay_grading_runs_evaluator_retry_errors_check'),
        CheckConstraint('max_points_snapshot > 0::numeric', name='essay_grading_runs_max_points_snapshot_check'),
        CheckConstraint('review_reason IS NULL OR char_length(review_reason) <= 1000', name='essay_grading_runs_review_reason_check'),
        CheckConstraint('run_number > 0', name='essay_grading_runs_run_number_check'),
        CheckConstraint("run_status::text = 'completed'::text AND needs_review = false AND evaluator_model_identifier IS NOT NULL AND evaluator_prompt_version IS NOT NULL AND evaluator_raw_response IS NOT NULL AND evaluator_parsed_response IS NOT NULL AND provisional_score IS NOT NULL AND provisional_score >= 0::numeric AND provisional_score <= max_points_snapshot AND error_code IS NULL AND error_detail IS NULL OR run_status::text = 'needs_review'::text AND needs_review = true AND review_reason IS NOT NULL AND evaluator_model_identifier IS NOT NULL AND evaluator_prompt_version IS NOT NULL AND evaluator_raw_response IS NOT NULL AND evaluator_parsed_response IS NOT NULL AND provisional_score IS NOT NULL AND provisional_score >= 0::numeric AND provisional_score <= max_points_snapshot AND error_code IS NULL AND error_detail IS NULL OR run_status::text = 'failed'::text AND provisional_score IS NULL AND evaluator_parsed_response IS NULL AND error_code IS NOT NULL AND error_detail IS NOT NULL", name='essay_grading_runs_state_check'),
        CheckConstraint("run_status::text = ANY (ARRAY['completed'::character varying, 'needs_review'::character varying, 'failed'::character varying]::text[])", name='essay_grading_runs_run_status_check'),
        ForeignKeyConstraint(['submission_id'], ['public.essay_submissions.id'], ondelete='RESTRICT', name='essay_grading_runs_submission_id_fkey'),
        PrimaryKeyConstraint('id', name='essay_grading_runs_pkey'),
        UniqueConstraint('submission_id', 'run_number', name='essay_grading_runs_submission_number_key'),
        Index('idx_essay_grading_runs_submission', 'submission_id', 'run_number'),
        {'comment': 'Append-only evaluator/scorer executions. A retry or regrade '
                'creates another run.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    run_status: Mapped[str] = mapped_column(String(20), nullable=False)
    evaluator_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    evaluator_retry_errors: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    scoring_version: Mapped[str] = mapped_column(String(100), nullable=False, server_default=text("'equal-weight-decimal-v1'::character varying"))
    max_points_snapshot: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    evaluator_model_identifier: Mapped[Optional[str]] = mapped_column(Text)
    evaluator_prompt_version: Mapped[Optional[str]] = mapped_column(Text)
    evaluator_latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    evaluator_input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    evaluator_output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    evaluator_raw_response: Mapped[Optional[str]] = mapped_column(Text, comment='Provider structured response only; never hidden reasoning or chain-of-thought.')
    evaluator_parsed_response: Mapped[Optional[dict]] = mapped_column(JSONB)
    provisional_score: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(10, 2))
    review_reason: Mapped[Optional[str]] = mapped_column(Text)
    error_code: Mapped[Optional[str]] = mapped_column(String(100))
    error_detail: Mapped[Optional[str]] = mapped_column(Text)

    essay_grade_reviews: Mapped[list['EssayGradeReviews']] = relationship('EssayGradeReviews', back_populates='grading_run')
    submission: Mapped['EssaySubmissions'] = relationship('EssaySubmissions', back_populates='essay_grading_runs')
    essay_criterion_results: Mapped[list['EssayCriterionResults']] = relationship('EssayCriterionResults', back_populates='grading_run')


class EssaySubmissions(Base):
    __tablename__ = 'essay_submissions'
    __table_args__ = (
        CheckConstraint('char_length(btrim(answer_text)) >= 1 AND char_length(btrim(answer_text)) <= 50000', name='essay_submissions_answer_text_check'),
        CheckConstraint("status::text = 'finalized'::text AND final_score IS NOT NULL AND final_score >= 0::numeric AND final_review_id IS NOT NULL AND finalized_at IS NOT NULL OR status::text <> 'finalized'::text AND final_score IS NULL AND final_review_id IS NULL AND finalized_at IS NULL", name='essay_submissions_final_state_check'),
        CheckConstraint("status::text = ANY (ARRAY['submitted'::character varying, 'grading'::character varying, 'graded'::character varying, 'needs_review'::character varying, 'grading_failed'::character varying, 'finalized'::character varying]::text[])", name='essay_submissions_status_check'),
        ForeignKeyConstraint(['exam_attempt_id'], ['public.exam_attempts.id'], ondelete='RESTRICT', name='essay_submissions_exam_attempt_id_fkey'),
        ForeignKeyConstraint(['exam_question_id'], ['public.exam_questions.id'], ondelete='RESTRICT', name='essay_submissions_exam_question_id_fkey'),
        ForeignKeyConstraint(['final_review_id'], ['public.essay_grade_reviews.id'], ondelete='RESTRICT', name='essay_submissions_final_review_fkey'),
        ForeignKeyConstraint(['question_version_id', 'exam_question_id'], ['public.essay_question_versions.id', 'public.essay_question_versions.exam_question_id'], ondelete='RESTRICT', name='essay_submissions_version_question_fkey'),
        PrimaryKeyConstraint('id', name='essay_submissions_pkey'),
        UniqueConstraint('exam_attempt_id', 'exam_question_id', name='essay_submissions_attempt_question_key'),
        UniqueConstraint('idempotency_key', name='essay_submissions_idempotency_key_key'),
        Index('idx_essay_submissions_attempt', 'exam_attempt_id'),
        Index('idx_essay_submissions_question_status', 'exam_question_id', 'status', 'submitted_at'),
        {'comment': 'Student essay evidence for one exam attempt and exact released '
                'question version. Answer text is immutable.',
     'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, server_default=text('gen_random_uuid()'))
    exam_attempt_id: Mapped[int] = mapped_column(Integer, nullable=False)
    exam_question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    question_version_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'submitted'::character varying"))
    submitted_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    grading_started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    graded_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    finalized_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    final_score: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(10, 2))
    final_review_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    essay_grade_reviews_submission: Mapped[list['EssayGradeReviews']] = relationship('EssayGradeReviews', foreign_keys='[EssayGradeReviews.submission_id]', back_populates='submission')
    essay_grading_runs: Mapped[list['EssayGradingRuns']] = relationship('EssayGradingRuns', back_populates='submission')
    exam_attempt: Mapped['ExamAttempts'] = relationship('ExamAttempts', back_populates='essay_submissions')
    exam_question: Mapped['ExamQuestions'] = relationship('ExamQuestions', back_populates='essay_submissions')
    final_review: Mapped[Optional['EssayGradeReviews']] = relationship('EssayGradeReviews', foreign_keys=[final_review_id], back_populates='essay_submissions_final_review')
    essay_question_versions: Mapped['EssayQuestionVersions'] = relationship('EssayQuestionVersions', back_populates='essay_submissions')


class PreCollegeStages(Base):
    __tablename__ = 'pre_college_stages'
    __table_args__ = (
        CheckConstraint("stage::text = ANY (ARRAY['primary'::character varying, 'preparatory'::character varying, 'secondary'::character varying]::text[])", name='pre_college_stages_stage_check'),
        CheckConstraint('year_number >= 1 AND year_number <= 6', name='pre_college_stages_year_number_check'),
        PrimaryKeyConstraint('id', name='pre_college_stages_pkey'),
        UniqueConstraint('stage', 'year_number', name='pre_college_stages_stage_year_number_key'),
        Index('idx_pre_college_stages_display_order', 'display_order'),
        {'comment': 'Pre-college (school) stages (FR-2.2), e.g. "3rd secondary". Owned '
                'by the NestJS API. RLS enabled with no policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name_en: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    year_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('true'))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    name_ar: Mapped[Optional[str]] = mapped_column(String(255))

    categories: Mapped[list['Categories']] = relationship('Categories', back_populates='pre_college_stage')


class QueryEmbeddings(Base):
    __tablename__ = 'query_embeddings'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='query_embeddings_pkey'),
        UniqueConstraint('query_hash', 'model', 'dim', name='query_embeddings_query_hash_model_dim_key'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(VECTOR(1536), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))


class Subjects(Base):
    __tablename__ = 'subjects'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='subjects_pkey'),
        UniqueConstraint('name', name='subjects_name_key'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    courses: Mapped[list['Courses']] = relationship('Courses', back_populates='subject')


class Topics(Base):
    __tablename__ = 'topics'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='topics_pkey'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    retention_assessment_results: Mapped[list['RetentionAssessmentResults']] = relationship('RetentionAssessmentResults', back_populates='topic')
    checkpoint_questions: Mapped[list['CheckpointQuestions']] = relationship('CheckpointQuestions', back_populates='topic')
    questions: Mapped[list['Questions']] = relationship('Questions', back_populates='topic')
    chat_messages: Mapped[list['ChatMessages']] = relationship('ChatMessages', back_populates='topic')
    exam_questions: Mapped[list['ExamQuestions']] = relationship('ExamQuestions', back_populates='topic_')


class Users(Base):
    __tablename__ = 'users'
    __table_args__ = (
        CheckConstraint("phone::text ~ '^\\+[1-9][0-9]{7,14}$'::text", name='users_phone_e164'),
        CheckConstraint("role::text = ANY (ARRAY['student'::character varying::text, 'doctor'::character varying::text])", name='users_role_check'),
        PrimaryKeyConstraint('id', name='users_pkey'),
        UniqueConstraint('auth_user_id', name='users_auth_user_id_key'),
        UniqueConstraint('email', name='users_email_key'),
        Index('idx_users_phone', 'phone', unique=True),
        Index('users_email_lower_idx'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    is_suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'), comment='When true, NestJS refuses login and refresh for this student or teacher. Default false so FastAPI-created rows stay able to sign in.')
    password_hash: Mapped[Optional[str]] = mapped_column(Text, comment='argon2id hash. Written only by the NestJS API; NULL means the account cannot log in yet.')
    birth_date: Mapped[Optional[datetime.date]] = mapped_column(Date, comment='Calendar date of birth. Written by NestJS student signup; nullable so existing FastAPI rows stay valid.')
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    phone_verified_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    auth_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)

    essay_grade_reviews: Mapped[list['EssayGradeReviews']] = relationship('EssayGradeReviews', back_populates='reviewer')
    access_code_batches: Mapped[list['AccessCodeBatches']] = relationship('AccessCodeBatches', back_populates='instructor')
    llm_daily_usage: Mapped[list['LlmDailyUsage']] = relationship('LlmDailyUsage', back_populates='user')
    password_reset_codes: Mapped[list['PasswordResetCodes']] = relationship('PasswordResetCodes', back_populates='user')
    refresh_tokens: Mapped[list['RefreshTokens']] = relationship('RefreshTokens', back_populates='user')
    subscriptions_doctor: Mapped[list['Subscriptions']] = relationship('Subscriptions', foreign_keys='[Subscriptions.doctor_id]', back_populates='doctor')
    subscriptions_student: Mapped[list['Subscriptions']] = relationship('Subscriptions', foreign_keys='[Subscriptions.student_id]', back_populates='student')
    access_codes_instructor: Mapped[list['AccessCodes']] = relationship('AccessCodes', foreign_keys='[AccessCodes.instructor_id]', back_populates='instructor')
    access_codes_redeemed_by_user: Mapped[list['AccessCodes']] = relationship('AccessCodes', foreign_keys='[AccessCodes.redeemed_by_user_id]', back_populates='redeemed_by_user')
    courses: Mapped[list['Courses']] = relationship('Courses', back_populates='doctor')
    code_redemption_attempts: Mapped[list['CodeRedemptionAttempts']] = relationship('CodeRedemptionAttempts', back_populates='user')
    enrollments: Mapped[list['Enrollments']] = relationship('Enrollments', back_populates='student')
    report_narratives: Mapped[list['ReportNarratives']] = relationship('ReportNarratives', back_populates='student')
    retention_assessment_results: Mapped[list['RetentionAssessmentResults']] = relationship('RetentionAssessmentResults', back_populates='student')
    weekly_analytics_snapshots: Mapped[list['WeeklyAnalyticsSnapshots']] = relationship('WeeklyAnalyticsSnapshots', back_populates='student')
    exam_attempts: Mapped[list['ExamAttempts']] = relationship('ExamAttempts', back_populates='user')
    lectures: Mapped[list['Lectures']] = relationship('Lectures', back_populates='doctor')
    assessment_question_results: Mapped[list['AssessmentQuestionResults']] = relationship('AssessmentQuestionResults', back_populates='student')
    assessment_question_attempts: Mapped[list['AssessmentQuestionAttempts']] = relationship('AssessmentQuestionAttempts', back_populates='student')
    chat_sessions: Mapped[list['ChatSessions']] = relationship('ChatSessions', back_populates='student')
    essay_question_versions: Mapped[list['EssayQuestionVersions']] = relationship('EssayQuestionVersions', back_populates='users')
    reports: Mapped[list['Reports']] = relationship('Reports', back_populates='student')
    student_study_sessions: Mapped[list['StudentStudySessions']] = relationship('StudentStudySessions', back_populates='student')
    video_events: Mapped[list['VideoEvents']] = relationship('VideoEvents', back_populates='student')
    checkpoint_attempts: Mapped[list['CheckpointAttempts']] = relationship('CheckpointAttempts', back_populates='student')
    essay_question_releases: Mapped[list['EssayQuestionReleases']] = relationship('EssayQuestionReleases', back_populates='users')
    notifications_student: Mapped[list['Notifications']] = relationship('Notifications', foreign_keys='[Notifications.student_id]', back_populates='student')
    notifications_user: Mapped[list['Notifications']] = relationship('Notifications', foreign_keys='[Notifications.user_id]', back_populates='user')
    question_attempts: Mapped[list['QuestionAttempts']] = relationship('QuestionAttempts', back_populates='student')


class AccessCodeBatches(Base):
    __tablename__ = 'access_code_batches'
    __table_args__ = (
        CheckConstraint('quantity > 0 AND quantity <= 5000', name='access_code_batches_quantity_check'),
        CheckConstraint('released_at IS NULL OR expires_at IS NULL OR expires_at > released_at', name='access_code_batches_window_check'),
        CheckConstraint("target_type::text = ANY (ARRAY['course'::character varying, 'session'::character varying, 'book'::character varying]::text[])", name='access_code_batches_target_type_check'),
        ForeignKeyConstraint(['created_by'], ['public.admins.id'], ondelete='SET NULL', name='access_code_batches_created_by_fkey'),
        ForeignKeyConstraint(['instructor_id'], ['public.users.id'], ondelete='RESTRICT', name='access_code_batches_instructor_id_fkey'),
        PrimaryKeyConstraint('id', name='access_code_batches_pkey'),
        Index('idx_access_code_batches_instructor', 'instructor_id'),
        Index('idx_access_code_batches_target', 'target_type', 'target_id'),
        {'comment': 'A generation run of access codes. Owned by the NestJS API. RLS '
                'enabled with no policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False, comment='course | session | book. Only course resolves today; session and book are accepted values that fail at redemption with a clear message, so adding them later needs no migration.')
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    instructor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    code_prefix: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'IMED'::character varying"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    released_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_by: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    admins: Mapped[Optional['Admins']] = relationship('Admins', back_populates='access_code_batches')
    instructor: Mapped['Users'] = relationship('Users', back_populates='access_code_batches')
    access_codes: Mapped[list['AccessCodes']] = relationship('AccessCodes', back_populates='batch')


class Categories(Base):
    __tablename__ = 'categories'
    __table_args__ = (
        CheckConstraint('(pre_college_stage_id IS NULL) <> (college_stage_id IS NULL)', name='categories_exactly_one_stage'),
        ForeignKeyConstraint(['college_stage_id'], ['public.college_stages.id'], ondelete='RESTRICT', name='categories_college_stage_id_fkey'),
        ForeignKeyConstraint(['parent_id'], ['public.categories.id'], ondelete='RESTRICT', name='categories_parent_id_fkey'),
        ForeignKeyConstraint(['pre_college_stage_id'], ['public.pre_college_stages.id'], ondelete='RESTRICT', name='categories_pre_college_stage_id_fkey'),
        PrimaryKeyConstraint('id', name='categories_pkey'),
        UniqueConstraint('slug', name='categories_slug_key'),
        Index('idx_categories_college_stage_id', 'college_stage_id'),
        Index('idx_categories_parent_id', 'parent_id'),
        Index('idx_categories_pre_college_stage_id', 'pre_college_stage_id'),
        {'comment': 'Subject category tree (FR-2.1). Owned by the NestJS API. RLS '
                'enabled with no policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name_en: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('true'))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    name_ar: Mapped[Optional[str]] = mapped_column(String(255))
    parent_id: Mapped[Optional[int]] = mapped_column(Integer)
    pre_college_stage_id: Mapped[Optional[int]] = mapped_column(Integer, comment='Set when this category hangs off a pre-college stage. Mutually exclusive with college_stage_id; exactly one of the two must be set.')
    college_stage_id: Mapped[Optional[int]] = mapped_column(Integer, comment='Set when this category hangs off a college stage. Mutually exclusive with pre_college_stage_id; exactly one of the two must be set.')

    college_stage: Mapped[Optional['CollegeStages']] = relationship('CollegeStages', back_populates='categories')
    parent: Mapped[Optional['Categories']] = relationship('Categories', remote_side=[id], back_populates='parent_reverse')
    parent_reverse: Mapped[list['Categories']] = relationship('Categories', remote_side=[parent_id], back_populates='parent')
    pre_college_stage: Mapped[Optional['PreCollegeStages']] = relationship('PreCollegeStages', back_populates='categories')
    courses: Mapped[list['Courses']] = relationship('Courses', back_populates='category')


class LlmDailyUsage(Base):
    __tablename__ = 'llm_daily_usage'
    __table_args__ = (
        CheckConstraint("jsonb_typeof(feature_counts) = 'object'::text", name='llm_daily_usage_feature_counts_check'),
        CheckConstraint('query_count > 0', name='llm_daily_usage_query_count_check'),
        ForeignKeyConstraint(['user_id'], ['public.users.id'], ondelete='CASCADE', name='llm_daily_usage_user_id_fkey'),
        PrimaryKeyConstraint('user_id', 'usage_date', name='llm_daily_usage_pkey'),
        Index('idx_llm_daily_usage_date', 'usage_date'),
        {'comment': 'FastAPI-owned atomic per-user LLM request totals by UTC day. RLS '
                'enabled with no policies.',
     'schema': 'public'}
    )

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usage_date: Mapped[datetime.date] = mapped_column(Date, primary_key=True, server_default=text("((CURRENT_TIMESTAMP AT TIME ZONE 'UTC'::text))::date"))
    query_count: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_counts: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"), comment='Aggregate request units by controlled feature name; contains no prompt or answer text.')
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    user: Mapped['Users'] = relationship('Users', back_populates='llm_daily_usage')


class PasswordResetCodes(Base):
    __tablename__ = 'password_reset_codes'
    __table_args__ = (
        ForeignKeyConstraint(['user_id'], ['public.users.id'], ondelete='CASCADE', name='password_reset_codes_user_id_fkey'),
        PrimaryKeyConstraint('id', name='password_reset_codes_pkey'),
        Index('idx_password_reset_codes_expires_at', 'expires_at'),
        Index('idx_password_reset_codes_user_id', 'user_id', 'created_at'),
        {'comment': 'Password reset codes (argon2id hashes only). Owned by the NestJS '
                'API. RLS enabled with no policies: unreachable except by a '
                'privileged role.',
     'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text('0'))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    consumed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    ip: Mapped[Optional[str]] = mapped_column(Text)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped['Users'] = relationship('Users', back_populates='password_reset_codes')


class RefreshTokens(Base):
    __tablename__ = 'refresh_tokens'
    __table_args__ = (
        ForeignKeyConstraint(['user_id'], ['public.users.id'], ondelete='CASCADE', name='refresh_tokens_user_id_fkey'),
        PrimaryKeyConstraint('id', name='refresh_tokens_pkey'),
        UniqueConstraint('replaced_by_token_id', name='refresh_tokens_replaced_by_token_id_key'),
        UniqueConstraint('token_hash', name='refresh_tokens_token_hash_key'),
        Index('refresh_tokens_expires_at_idx', 'expires_at'),
        Index('refresh_tokens_user_id_device_id_idx', 'user_id', 'device_id', postgresql_where='(revoked_at IS NULL)'),
        Index('refresh_tokens_user_id_idx', 'user_id'),
        {'comment': 'Session refresh tokens (SHA-256 digests only). Owned by the '
                'NestJS API. RLS enabled with no policies: unreachable except by a '
                'privileged role.',
     'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    replaced_by_token_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    ip: Mapped[Optional[str]] = mapped_column(Text)
    device_id: Mapped[Optional[str]] = mapped_column(Text, comment='Stable identifier for the device holding this session. From the X-Device-Id header when the client sends one, otherwise a server-side hash of user agent and IP. NULL for rows predating device binding.')
    device_label: Mapped[Optional[str]] = mapped_column(Text, comment='Human-readable device name for session lists and the admin dashboard. Display only — never used for authorization.')

    user: Mapped['Users'] = relationship('Users', back_populates='refresh_tokens')


class Subscriptions(Base):
    __tablename__ = 'subscriptions'
    __table_args__ = (
        ForeignKeyConstraint(['doctor_id'], ['public.users.id'], name='subscriptions_doctor_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], name='subscriptions_student_id_fkey'),
        PrimaryKeyConstraint('id', name='subscriptions_pkey'),
        UniqueConstraint('student_id', 'doctor_id', name='subscriptions_student_id_doctor_id_key'),
        Index('idx_subscriptions_doctor', 'doctor_id'),
        Index('idx_subscriptions_student', 'student_id'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    doctor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    subscribed_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    doctor: Mapped['Users'] = relationship('Users', foreign_keys=[doctor_id], back_populates='subscriptions_doctor')
    student: Mapped['Users'] = relationship('Users', foreign_keys=[student_id], back_populates='subscriptions_student')


class AccessCodes(Base):
    __tablename__ = 'access_codes'
    __table_args__ = (
        CheckConstraint("status::text <> 'redeemed'::text OR redeemed_by_user_id IS NOT NULL AND redeemed_at IS NOT NULL", name='access_codes_redeemed_check'),
        CheckConstraint("status::text <> 'revoked'::text OR revoked_at IS NOT NULL", name='access_codes_revoked_check'),
        CheckConstraint("status::text = ANY (ARRAY['unused'::character varying, 'redeemed'::character varying, 'revoked'::character varying]::text[])", name='access_codes_status_check'),
        CheckConstraint("target_type::text = ANY (ARRAY['course'::character varying, 'session'::character varying, 'book'::character varying]::text[])", name='access_codes_target_type_check'),
        ForeignKeyConstraint(['batch_id'], ['public.access_code_batches.id'], ondelete='CASCADE', name='access_codes_batch_id_fkey'),
        ForeignKeyConstraint(['instructor_id'], ['public.users.id'], ondelete='RESTRICT', name='access_codes_instructor_id_fkey'),
        ForeignKeyConstraint(['redeemed_by_user_id'], ['public.users.id'], ondelete='SET NULL', name='access_codes_redeemed_by_user_id_fkey'),
        ForeignKeyConstraint(['revoked_by'], ['public.admins.id'], ondelete='SET NULL', name='access_codes_revoked_by_fkey'),
        PrimaryKeyConstraint('id', name='access_codes_pkey'),
        UniqueConstraint('code_hash', name='access_codes_code_hash_key'),
        Index('idx_access_codes_batch', 'batch_id'),
        Index('idx_access_codes_display_prefix', 'code_display_prefix'),
        Index('idx_access_codes_instructor', 'instructor_id'),
        Index('idx_access_codes_redeemed_by', 'redeemed_by_user_id', postgresql_where='(redeemed_by_user_id IS NOT NULL)'),
        Index('idx_access_codes_status', 'status'),
        Index('idx_access_codes_target', 'target_type', 'target_id'),
        {'comment': 'One sellable code. Owned by the NestJS API. RLS enabled with no '
                'policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(Integer, nullable=False)
    code_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, comment='HMAC-SHA256(normalised code, server pepper), hex. The plaintext is never stored anywhere and is shown once, at generation.')
    code_display_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    instructor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'unused'::character varying"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    released_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    redeemed_by_user_id: Mapped[Optional[int]] = mapped_column(Integer)
    redeemed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    redeemed_ip: Mapped[Optional[Any]] = mapped_column(INET)
    redeemed_device_fingerprint: Mapped[Optional[str]] = mapped_column(String(128))
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    revoked_by: Mapped[Optional[int]] = mapped_column(Integer)
    revoke_reason: Mapped[Optional[str]] = mapped_column(Text)

    batch: Mapped['AccessCodeBatches'] = relationship('AccessCodeBatches', back_populates='access_codes')
    instructor: Mapped['Users'] = relationship('Users', foreign_keys=[instructor_id], back_populates='access_codes_instructor')
    redeemed_by_user: Mapped[Optional['Users']] = relationship('Users', foreign_keys=[redeemed_by_user_id], back_populates='access_codes_redeemed_by_user')
    admins: Mapped[Optional['Admins']] = relationship('Admins', back_populates='access_codes')
    code_redemption_attempts: Mapped[list['CodeRedemptionAttempts']] = relationship('CodeRedemptionAttempts', back_populates='code')
    enrollments: Mapped[list['Enrollments']] = relationship('Enrollments', back_populates='access_code')


class Courses(Base):
    __tablename__ = 'courses'
    __table_args__ = (
        CheckConstraint('(price IS NULL) = (currency IS NULL)', name='courses_price_currency_check'),
        CheckConstraint('academic_year IS NULL OR academic_year >= 1 AND academic_year <= 7', name='courses_academic_year_check'),
        CheckConstraint("course_level IS NULL OR (course_level::text = ANY (ARRAY['beginner'::character varying, 'all_levels'::character varying, 'advanced'::character varying]::text[]))", name='courses_course_level_check'),
        CheckConstraint("language IS NULL OR (language::text = ANY (ARRAY['ar'::character varying, 'en'::character varying]::text[]))", name='courses_language_check'),
        CheckConstraint("status::text <> 'published'::text OR published_at IS NOT NULL", name='courses_published_at_check'),
        CheckConstraint("status::text = ANY (ARRAY['draft'::character varying, 'published'::character varying, 'archived'::character varying]::text[])", name='courses_status_check'),
        ForeignKeyConstraint(['category_id'], ['public.categories.id'], ondelete='RESTRICT', name='courses_category_id_fkey'),
        ForeignKeyConstraint(['created_by'], ['public.admins.id'], ondelete='SET NULL', name='courses_created_by_fkey'),
        ForeignKeyConstraint(['doctor_id'], ['public.users.id'], name='courses_doctor_id_fkey'),
        ForeignKeyConstraint(['subject_id'], ['public.subjects.id'], name='courses_subject_id_fkey'),
        PrimaryKeyConstraint('id', name='courses_pkey'),
        UniqueConstraint('id', 'doctor_id', name='courses_id_doctor_key'),
        UniqueConstraint('slug', name='courses_slug_key'),
        Index('idx_courses_academic_year', 'academic_year'),
        Index('idx_courses_category', 'category_id'),
        Index('idx_courses_doctor', 'doctor_id'),
        Index('idx_courses_status_published_at', 'status', 'published_at'),
        Index('idx_courses_subject', 'subject_id'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doctor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'::character varying"), comment='draft | published | archived. Only published courses are publicly listable. Existing FastAPI rows defaulted to draft on migration.')
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    subject_id: Mapped[Optional[int]] = mapped_column(Integer)
    academic_year: Mapped[Optional[int]] = mapped_column(SmallInteger)
    slug: Mapped[Optional[str]] = mapped_column(String(255))
    subtitle: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    category_id: Mapped[Optional[int]] = mapped_column(Integer, comment='NestJS catalog taxonomy. Coexists with the FastAPI-owned subject_id; neither replaces the other.')
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(10, 2))
    currency: Mapped[Optional[str]] = mapped_column(CHAR(3))
    published_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_by: Mapped[Optional[int]] = mapped_column(Integer)
    language: Mapped[Optional[str]] = mapped_column(String(10), comment='Course delivery language: ar | en. Nullable for legacy FastAPI rows.')
    course_level: Mapped[Optional[str]] = mapped_column(String(20), comment='Catalog level: beginner | all_levels | advanced. Nullable for legacy rows.')
    thumbnail_storage_key: Mapped[Optional[str]] = mapped_column(Text, comment='Private imed_media object key. Never serialized; API returns a signed URL.')

    category: Mapped[Optional['Categories']] = relationship('Categories', back_populates='courses')
    admins: Mapped[Optional['Admins']] = relationship('Admins', back_populates='courses')
    doctor: Mapped['Users'] = relationship('Users', back_populates='courses')
    subject: Mapped[Optional['Subjects']] = relationship('Subjects', back_populates='courses')
    course_modules: Mapped[list['CourseModules']] = relationship('CourseModules', back_populates='course')
    enrollments: Mapped[list['Enrollments']] = relationship('Enrollments', back_populates='course')
    exams: Mapped[list['Exams']] = relationship('Exams', back_populates='course')
    modules: Mapped[list['Modules']] = relationship('Modules', back_populates='course')
    report_narratives: Mapped[list['ReportNarratives']] = relationship('ReportNarratives', back_populates='course')
    retention_assessment_results: Mapped[list['RetentionAssessmentResults']] = relationship('RetentionAssessmentResults', back_populates='course')
    weekly_analytics_snapshots: Mapped[list['WeeklyAnalyticsSnapshots']] = relationship('WeeklyAnalyticsSnapshots', back_populates='course')
    course_items: Mapped[list['CourseItems']] = relationship('CourseItems', back_populates='course')
    lectures_course_doctor: Mapped[list['Lectures']] = relationship('Lectures', foreign_keys='[Lectures.course_id, Lectures.doctor_id]', back_populates='course_doctor')
    lectures_course: Mapped[list['Lectures']] = relationship('Lectures', foreign_keys='[Lectures.course_id]', back_populates='course')
    assessment_question_results: Mapped[list['AssessmentQuestionResults']] = relationship('AssessmentQuestionResults', back_populates='course')
    checkpoint_questions: Mapped[list['CheckpointQuestions']] = relationship('CheckpointQuestions', back_populates='course')
    course_weekly_assignments: Mapped[list['CourseWeeklyAssignments']] = relationship('CourseWeeklyAssignments', back_populates='course')
    reports: Mapped[list['Reports']] = relationship('Reports', back_populates='course')
    student_study_sessions: Mapped[list['StudentStudySessions']] = relationship('StudentStudySessions', back_populates='course')
    checkpoint_attempts: Mapped[list['CheckpointAttempts']] = relationship('CheckpointAttempts', back_populates='course')


class CodeRedemptionAttempts(Base):
    __tablename__ = 'code_redemption_attempts'
    __table_args__ = (
        CheckConstraint("result::text = ANY (ARRAY['success'::character varying, 'idempotent_success'::character varying, 'rate_limited'::character varying, 'invalid'::character varying, 'revoked'::character varying, 'not_released'::character varying, 'expired'::character varying, 'already_redeemed'::character varying, 'target_unsupported'::character varying, 'target_unavailable'::character varying, 'already_enrolled'::character varying]::text[])", name='code_redemption_attempts_result_check'),
        ForeignKeyConstraint(['code_id'], ['public.access_codes.id'], ondelete='SET NULL', name='code_redemption_attempts_code_id_fkey'),
        ForeignKeyConstraint(['user_id'], ['public.users.id'], ondelete='SET NULL', name='code_redemption_attempts_user_id_fkey'),
        PrimaryKeyConstraint('id', name='code_redemption_attempts_pkey'),
        Index('idx_code_redemption_attempts_created', 'created_at'),
        Index('idx_code_redemption_attempts_fingerprint', 'device_fingerprint', 'created_at', postgresql_where='(device_fingerprint IS NOT NULL)'),
        Index('idx_code_redemption_attempts_hash', 'code_hash_attempted', 'created_at'),
        Index('idx_code_redemption_attempts_ip', 'ip', 'created_at'),
        Index('idx_code_redemption_attempts_user', 'user_id', 'created_at'),
        {'comment': 'Audit log of every redemption attempt, success or failure '
                '(FR-3.6). Owned by the NestJS API. RLS enabled with no policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_hash_attempted: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    result: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    code_id: Mapped[Optional[int]] = mapped_column(Integer)
    user_id: Mapped[Optional[int]] = mapped_column(Integer)
    ip: Mapped[Optional[Any]] = mapped_column(INET)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    device_fingerprint: Mapped[Optional[str]] = mapped_column(String(128))

    code: Mapped[Optional['AccessCodes']] = relationship('AccessCodes', back_populates='code_redemption_attempts')
    user: Mapped[Optional['Users']] = relationship('Users', back_populates='code_redemption_attempts')


class CourseModules(Base):
    __tablename__ = 'course_modules'
    __table_args__ = (
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='course_modules_course_id_fkey'),
        PrimaryKeyConstraint('id', name='course_modules_pkey'),
        UniqueConstraint('course_id', 'order_index', name='course_modules_course_order_key'),
        Index('idx_course_modules_course', 'course_id', 'order_index'),
        {'comment': 'Ordered NestJS-owned course modules (chapters). Distinct from the '
                'FastAPI-owned modules table. RLS enabled with no policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    course: Mapped['Courses'] = relationship('Courses', back_populates='course_modules')
    course_items: Mapped[list['CourseItems']] = relationship('CourseItems', back_populates='module')


class Enrollments(Base):
    __tablename__ = 'enrollments'
    __table_args__ = (
        CheckConstraint("source::text = ANY (ARRAY['access_code'::character varying, 'admin_grant'::character varying, 'free'::character varying]::text[])", name='enrollments_source_check'),
        CheckConstraint("status::text = ANY (ARRAY['active'::character varying, 'revoked'::character varying, 'expired'::character varying]::text[])", name='enrollments_status_check'),
        ForeignKeyConstraint(['access_code_id'], ['public.access_codes.id'], ondelete='SET NULL', name='enrollments_access_code_id_fkey'),
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='enrollments_course_id_fkey'),
        ForeignKeyConstraint(['granted_by'], ['public.admins.id'], ondelete='SET NULL', name='enrollments_granted_by_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], name='enrollments_student_id_fkey'),
        PrimaryKeyConstraint('id', name='enrollments_pkey'),
        UniqueConstraint('student_id', 'course_id', name='enrollments_student_id_course_id_key'),
        Index('idx_enrollments_access_code', 'access_code_id', postgresql_where='(access_code_id IS NOT NULL)'),
        Index('idx_enrollments_student', 'student_id'),
        Index('idx_enrollments_student_active', 'student_id', 'status'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    enrolled_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'admin_grant'::character varying"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'::character varying"))
    granted_by: Mapped[Optional[int]] = mapped_column(Integer)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), comment='NULL means lifetime access. Access is live when status = active AND (expires_at IS NULL OR expires_at > now()).')
    access_code_id: Mapped[Optional[int]] = mapped_column(Integer)

    access_code: Mapped[Optional['AccessCodes']] = relationship('AccessCodes', back_populates='enrollments')
    course: Mapped['Courses'] = relationship('Courses', back_populates='enrollments')
    admins: Mapped[Optional['Admins']] = relationship('Admins', back_populates='enrollments')
    student: Mapped['Users'] = relationship('Users', back_populates='enrollments')


class Exams(Base):
    __tablename__ = 'exams'
    __table_args__ = (
        CheckConstraint('duration_minutes > 0', name='exams_duration_check'),
        CheckConstraint('max_attempts IS NULL OR max_attempts > 0', name='exams_max_attempts_check'),
        CheckConstraint('pass_score >= 0 AND pass_score <= 100', name='exams_pass_score_check'),
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='exams_course_id_fkey'),
        PrimaryKeyConstraint('id', name='exams_pkey'),
        Index('idx_exams_course', 'course_id'),
        {'comment': 'Course-scoped exams. Owned by the NestJS API. Distinct from the '
                'FastAPI-owned lecture-scoped questions table. RLS enabled, no '
                'policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    pass_score: Mapped[int] = mapped_column(Integer, nullable=False)
    shuffle_questions: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'))
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    max_attempts: Mapped[Optional[int]] = mapped_column(Integer)

    course: Mapped['Courses'] = relationship('Courses', back_populates='exams')
    course_items: Mapped[list['CourseItems']] = relationship('CourseItems', back_populates='exam')
    exam_attempts: Mapped[list['ExamAttempts']] = relationship('ExamAttempts', back_populates='exam')
    exam_questions: Mapped[list['ExamQuestions']] = relationship('ExamQuestions', back_populates='exam')


class Modules(Base):
    __tablename__ = 'modules'
    __table_args__ = (
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='modules_course_id_fkey'),
        PrimaryKeyConstraint('id', name='modules_pkey'),
        UniqueConstraint('course_id', 'title', name='modules_course_id_title_key'),
        Index('idx_modules_course', 'course_id', 'position'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text('0'))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    course: Mapped['Courses'] = relationship('Courses', back_populates='modules')
    lectures: Mapped[list['Lectures']] = relationship('Lectures', back_populates='module')


class ReportNarratives(Base):
    __tablename__ = 'report_narratives'
    __table_args__ = (
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='report_narratives_course_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], name='report_narratives_student_id_fkey'),
        PrimaryKeyConstraint('id', name='report_narratives_pkey'),
        UniqueConstraint('student_id', 'course_id', 'week_start', name='report_narratives_student_id_course_id_week_start_key'),
        Index('idx_report_narratives_week', 'week_start'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    week_start: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    narrative: Mapped[dict] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    course: Mapped['Courses'] = relationship('Courses', back_populates='report_narratives')
    student: Mapped['Users'] = relationship('Users', back_populates='report_narratives')


class RetentionAssessmentResults(Base):
    __tablename__ = 'retention_assessment_results'
    __table_args__ = (
        CheckConstraint("source_type::text = ANY (ARRAY['checkpoint'::character varying, 'quiz'::character varying, 'exam'::character varying, 'practice'::character varying]::text[])", name='retention_assessment_results_source_type_check'),
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='retention_assessment_results_course_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], ondelete='CASCADE', name='retention_assessment_results_student_id_fkey'),
        ForeignKeyConstraint(['topic_id'], ['public.topics.id'], ondelete='CASCADE', name='retention_assessment_results_topic_id_fkey'),
        PrimaryKeyConstraint('id', name='retention_assessment_results_pkey'),
        UniqueConstraint('student_id', 'source_type', 'source_result_id', name='retention_assessment_results_student_id_source_type_source__key'),
        Index('idx_retention_student_course_topic_date', 'student_id', 'course_id', 'topic_id', 'assessed_at'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    topic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_result_id: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    assessed_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    course: Mapped['Courses'] = relationship('Courses', back_populates='retention_assessment_results')
    student: Mapped['Users'] = relationship('Users', back_populates='retention_assessment_results')
    topic: Mapped['Topics'] = relationship('Topics', back_populates='retention_assessment_results')


class WeeklyAnalyticsSnapshots(Base):
    __tablename__ = 'weekly_analytics_snapshots'
    __table_args__ = (
        CheckConstraint("jsonb_typeof(payload) = 'object'::text", name='weekly_analytics_snapshots_payload_check'),
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='weekly_analytics_snapshots_course_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], ondelete='CASCADE', name='weekly_analytics_snapshots_student_id_fkey'),
        PrimaryKeyConstraint('id', name='weekly_analytics_snapshots_pkey'),
        UniqueConstraint('student_id', 'course_id', 'week_start', 'report_version', name='weekly_analytics_snapshots_student_id_course_id_week_start__key'),
        Index('idx_weekly_snapshots_student_course_week', 'student_id', 'course_id', 'week_start', 'generated_at'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    week_start: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    report_version: Mapped[str] = mapped_column(String(40), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    course: Mapped['Courses'] = relationship('Courses', back_populates='weekly_analytics_snapshots')
    student: Mapped['Users'] = relationship('Users', back_populates='weekly_analytics_snapshots')


class CourseItems(Base):
    __tablename__ = 'course_items'
    __table_args__ = (
        CheckConstraint("type::text = 'video'::text AND video_ref IS NOT NULL AND pdf_storage_key IS NULL AND exam_id IS NULL OR type::text = 'pdf'::text AND pdf_storage_key IS NOT NULL AND video_ref IS NULL AND exam_id IS NULL OR type::text = 'exam'::text AND exam_id IS NOT NULL AND video_ref IS NULL AND pdf_storage_key IS NULL", name='course_items_type_columns_check'),
        CheckConstraint("type::text = ANY (ARRAY['video'::character varying, 'pdf'::character varying, 'exam'::character varying]::text[])", name='course_items_type_check'),
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='course_items_course_id_fkey'),
        ForeignKeyConstraint(['exam_id'], ['public.exams.id'], ondelete='RESTRICT', name='course_items_exam_id_fkey'),
        ForeignKeyConstraint(['module_id'], ['public.course_modules.id'], ondelete='CASCADE', name='course_items_module_id_fkey'),
        PrimaryKeyConstraint('id', name='course_items_pkey'),
        UniqueConstraint('module_id', 'order_index', name='course_items_module_order_key'),
        Index('idx_course_items_course', 'course_id', 'order_index'),
        Index('idx_course_items_module', 'module_id', 'order_index'),
        {'comment': 'Ordered course content (video | pdf | exam) grouped by '
                'course_modules. Owned by the NestJS API. RLS enabled with no '
                'policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    is_preview: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    module_id: Mapped[int] = mapped_column(Integer, nullable=False, comment='Nest-owned course_modules row. Distinct from FastAPI modules.id.')
    video_provider: Mapped[Optional[str]] = mapped_column(String(30))
    video_ref: Mapped[Optional[str]] = mapped_column(Text)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    pdf_storage_key: Mapped[Optional[str]] = mapped_column(Text, comment='Internal storage key. Never serialized to a client — PDFs are streamed through the API, never handed out as a bucket URL.')
    pdf_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    pdf_page_count: Mapped[Optional[int]] = mapped_column(Integer)
    exam_id: Mapped[Optional[int]] = mapped_column(Integer)

    course: Mapped['Courses'] = relationship('Courses', back_populates='course_items')
    exam: Mapped[Optional['Exams']] = relationship('Exams', back_populates='course_items')
    module: Mapped['CourseModules'] = relationship('CourseModules', back_populates='course_items')
    chat_sessions: Mapped[list['ChatSessions']] = relationship('ChatSessions', back_populates='video')
    checkpoint_questions: Mapped[list['CheckpointQuestions']] = relationship('CheckpointQuestions', back_populates='video')
    course_weekly_assignments: Mapped[list['CourseWeeklyAssignments']] = relationship('CourseWeeklyAssignments', back_populates='video')
    student_study_sessions: Mapped[list['StudentStudySessions']] = relationship('StudentStudySessions', back_populates='video')
    transcript_chunks: Mapped[list['TranscriptChunks']] = relationship('TranscriptChunks', back_populates='video')
    video_events: Mapped[list['VideoEvents']] = relationship('VideoEvents', back_populates='video')


class ExamAttempts(Base):
    __tablename__ = 'exam_attempts'
    __table_args__ = (
        CheckConstraint('submitted_at IS NULL AND score IS NULL AND passed IS NULL OR submitted_at IS NOT NULL AND score IS NOT NULL AND passed IS NOT NULL', name='exam_attempts_graded_check'),
        ForeignKeyConstraint(['exam_id'], ['public.exams.id'], ondelete='CASCADE', name='exam_attempts_exam_id_fkey'),
        ForeignKeyConstraint(['user_id'], ['public.users.id'], ondelete='CASCADE', name='exam_attempts_user_id_fkey'),
        PrimaryKeyConstraint('id', name='exam_attempts_pkey'),
        Index('idx_exam_attempts_exam_user', 'exam_id', 'user_id'),
        Index('idx_exam_attempts_user', 'user_id'),
        {'comment': 'Owned by the NestJS API. RLS enabled with no policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    answers: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    submitted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    score: Mapped[Optional[int]] = mapped_column(Integer)
    passed: Mapped[Optional[bool]] = mapped_column(Boolean)

    essay_submissions: Mapped[list['EssaySubmissions']] = relationship('EssaySubmissions', back_populates='exam_attempt')
    exam: Mapped['Exams'] = relationship('Exams', back_populates='exam_attempts')
    user: Mapped['Users'] = relationship('Users', back_populates='exam_attempts')
    assessment_question_results: Mapped[list['AssessmentQuestionResults']] = relationship('AssessmentQuestionResults', back_populates='exam_attempt')
    assessment_question_attempts: Mapped[list['AssessmentQuestionAttempts']] = relationship('AssessmentQuestionAttempts', back_populates='exam_attempt')


class ExamQuestions(Base):
    __tablename__ = 'exam_questions'
    __table_args__ = (
        CheckConstraint("cognitive_level IS NULL OR (cognitive_level::text = ANY (ARRAY['recall'::character varying, 'understanding'::character varying, 'application'::character varying]::text[]))", name='exam_questions_cognitive_level_check'),
        CheckConstraint('points > 0', name='exam_questions_points_check'),
        CheckConstraint("type::text = ANY (ARRAY['single_choice'::character varying, 'multi_choice'::character varying, 'true_false'::character varying, 'essay'::character varying]::text[])", name='exam_questions_type_check'),
        ForeignKeyConstraint(['exam_id'], ['public.exams.id'], ondelete='CASCADE', name='exam_questions_exam_id_fkey'),
        ForeignKeyConstraint(['topic_id'], ['public.topics.id'], ondelete='SET NULL', name='exam_questions_topic_id_fkey'),
        PrimaryKeyConstraint('id', name='exam_questions_pkey'),
        UniqueConstraint('exam_id', 'order_index', name='exam_questions_exam_order_key'),
        Index('idx_exam_questions_exam', 'exam_id', 'order_index'),
        Index('idx_exam_questions_topic', 'topic_id', postgresql_where='(topic_id IS NOT NULL)'),
        {'comment': 'Owned by the NestJS API. RLS enabled with no policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[int] = mapped_column(Integer, nullable=False)
    text_: Mapped[str] = mapped_column('text', Text, nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    learning_objective: Mapped[Optional[str]] = mapped_column(Text)
    cognitive_level: Mapped[Optional[str]] = mapped_column(String(20))
    difficulty: Mapped[Optional[str]] = mapped_column(String(20))
    topic: Mapped[Optional[str]] = mapped_column(Text)
    topic_id: Mapped[Optional[int]] = mapped_column(Integer)

    essay_submissions: Mapped[list['EssaySubmissions']] = relationship('EssaySubmissions', back_populates='exam_question')
    exam: Mapped['Exams'] = relationship('Exams', back_populates='exam_questions')
    assessment_question_results: Mapped[list['AssessmentQuestionResults']] = relationship('AssessmentQuestionResults', back_populates='exam_question')
    assessment_question_attempts: Mapped[list['AssessmentQuestionAttempts']] = relationship('AssessmentQuestionAttempts', back_populates='exam_question')
    topic_: Mapped[Optional['Topics']] = relationship('Topics', back_populates='exam_questions')
    essay_question_versions: Mapped[list['EssayQuestionVersions']] = relationship('EssayQuestionVersions', back_populates='exam_question')
    exam_options: Mapped[list['ExamOptions']] = relationship('ExamOptions', back_populates='question')
    essay_question_releases: Mapped[list['EssayQuestionReleases']] = relationship('EssayQuestionReleases', back_populates='exam_question')


class Lectures(Base):
    __tablename__ = 'lectures'
    __table_args__ = (
        ForeignKeyConstraint(['course_id', 'doctor_id'], ['public.courses.id', 'public.courses.doctor_id'], onupdate='CASCADE', name='lectures_course_doctor_fkey'),
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], name='lectures_course_id_fkey'),
        ForeignKeyConstraint(['doctor_id'], ['public.users.id'], name='lectures_doctor_id_fkey'),
        ForeignKeyConstraint(['module_id'], ['public.modules.id'], ondelete='SET NULL', name='lectures_module_id_fkey'),
        PrimaryKeyConstraint('id', name='lectures_pkey'),
        UniqueConstraint('bunny_video_id', name='lectures_bunny_video_id_key'),
        Index('idx_lectures_bunny_video', 'bunny_video_id', postgresql_where='(bunny_video_id IS NOT NULL)'),
        Index('idx_lectures_course', 'course_id'),
        Index('idx_lectures_module', 'module_id'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doctor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    course_id: Mapped[Optional[int]] = mapped_column(Integer)
    module_id: Mapped[Optional[int]] = mapped_column(Integer)
    video_url: Mapped[Optional[str]] = mapped_column(Text)
    bunny_video_id: Mapped[Optional[str]] = mapped_column(Text)

    course_doctor: Mapped[Optional['Courses']] = relationship('Courses', foreign_keys=[course_id, doctor_id], back_populates='lectures_course_doctor')
    course: Mapped[Optional['Courses']] = relationship('Courses', foreign_keys=[course_id], back_populates='lectures_course')
    doctor: Mapped['Users'] = relationship('Users', back_populates='lectures')
    module: Mapped[Optional['Modules']] = relationship('Modules', back_populates='lectures')
    chat_sessions: Mapped[list['ChatSessions']] = relationship('ChatSessions', back_populates='lecture')
    checkpoint_questions: Mapped[list['CheckpointQuestions']] = relationship('CheckpointQuestions', back_populates='lecture')
    course_weekly_assignments: Mapped[list['CourseWeeklyAssignments']] = relationship('CourseWeeklyAssignments', back_populates='lecture')
    questions: Mapped[list['Questions']] = relationship('Questions', back_populates='lecture')
    reports: Mapped[list['Reports']] = relationship('Reports', back_populates='lecture')
    student_study_sessions: Mapped[list['StudentStudySessions']] = relationship('StudentStudySessions', back_populates='lecture')
    transcript_chunks: Mapped[list['TranscriptChunks']] = relationship('TranscriptChunks', back_populates='lecture')
    video_events: Mapped[list['VideoEvents']] = relationship('VideoEvents', back_populates='lecture')


class AssessmentQuestionAttempts(Base):
    __tablename__ = 'assessment_question_attempts'
    __table_args__ = (
        CheckConstraint('NOT (skipped AND timed_out) AND (answered_at IS NOT NULL AND is_correct IS NOT NULL AND selected_option_ids IS NOT NULL AND NOT skipped AND NOT timed_out OR answered_at IS NULL AND is_correct IS NULL AND selected_option_ids IS NULL AND (skipped OR timed_out))', name='assessment_question_attempts_outcome'),
        CheckConstraint('answered_at IS NULL AND response_time_ms IS NULL OR answered_at IS NOT NULL AND response_time_ms IS NOT NULL', name='assessment_question_attempts_response_time'),
        CheckConstraint('attempt_number > 0', name='assessment_question_attempts_attempt_number_check'),
        CheckConstraint('response_time_ms IS NULL OR response_time_ms >= 0', name='assessment_question_attempts_response_time_ms_check'),
        ForeignKeyConstraint(['exam_attempt_id'], ['public.exam_attempts.id'], ondelete='CASCADE', name='assessment_question_attempts_exam_attempt_id_fkey'),
        ForeignKeyConstraint(['exam_question_id'], ['public.exam_questions.id'], ondelete='CASCADE', name='assessment_question_attempts_exam_question_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], ondelete='CASCADE', name='assessment_question_attempts_student_id_fkey'),
        PrimaryKeyConstraint('id', name='assessment_question_attempts_pkey'),
        UniqueConstraint('exam_attempt_id', 'exam_question_id', 'attempt_number', name='assessment_question_attempts_exam_attempt_id_exam_question__key'),
        Index('idx_assessment_question_attempts_exam_student', 'exam_attempt_id', 'student_id'),
        Index('idx_assessment_question_attempts_question_time', 'exam_question_id', 'answered_at'),
        Index('idx_assessment_question_attempts_student_question_attempt', 'student_id', 'exam_question_id', 'attempt_number', 'created_at'),
        {'comment': 'Raw per-question response evidence; teacher analytics are derived, never stored here.',
         'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    exam_attempt_id: Mapped[int] = mapped_column(Integer, nullable=False)
    exam_question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_number: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text('1'), comment='Retry sequence within one exam attempt and question; starts at 1.')
    shown_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'))
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    selected_option_ids: Mapped[Optional[list[int]]] = mapped_column(ARRAY(Integer()))
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean)
    answered_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer, comment='Client-observed duration from question shown to answered, in milliseconds.')

    exam_attempt: Mapped['ExamAttempts'] = relationship('ExamAttempts', back_populates='assessment_question_attempts')
    exam_question: Mapped['ExamQuestions'] = relationship('ExamQuestions', back_populates='assessment_question_attempts')
    student: Mapped['Users'] = relationship('Users', back_populates='assessment_question_attempts')


class AssessmentQuestionResults(Base):
    __tablename__ = 'assessment_question_results'
    __table_args__ = (
        CheckConstraint('response_time_ms IS NULL OR response_time_ms >= 0', name='assessment_question_results_response_time_ms_check'),
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='assessment_question_results_course_id_fkey'),
        ForeignKeyConstraint(['exam_attempt_id'], ['public.exam_attempts.id'], ondelete='CASCADE', name='assessment_question_results_exam_attempt_id_fkey'),
        ForeignKeyConstraint(['exam_question_id'], ['public.exam_questions.id'], ondelete='CASCADE', name='assessment_question_results_exam_question_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], ondelete='CASCADE', name='assessment_question_results_student_id_fkey'),
        PrimaryKeyConstraint('id', name='assessment_question_results_pkey'),
        UniqueConstraint('exam_attempt_id', 'exam_question_id', name='assessment_question_results_exam_attempt_id_exam_question_i_key'),
        Index('idx_assessment_results_question', 'exam_question_id', 'assessed_at'),
        Index('idx_assessment_results_student_course_date', 'student_id', 'course_id', 'assessed_at'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    exam_attempt_id: Mapped[int] = mapped_column(Integer, nullable=False)
    exam_question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    assessed_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    selected_answer: Mapped[Optional[dict]] = mapped_column(JSONB)
    awarded_points: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(10, 3))
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer)

    course: Mapped['Courses'] = relationship('Courses', back_populates='assessment_question_results')
    exam_attempt: Mapped['ExamAttempts'] = relationship('ExamAttempts', back_populates='assessment_question_results')
    exam_question: Mapped['ExamQuestions'] = relationship('ExamQuestions', back_populates='assessment_question_results')
    student: Mapped['Users'] = relationship('Users', back_populates='assessment_question_results')


class ChatSessions(Base):
    __tablename__ = 'chat_sessions'
    __table_args__ = (
        CheckConstraint('(summary_input_tokens IS NULL OR summary_input_tokens >= 0) AND (summary_output_tokens IS NULL OR summary_output_tokens >= 0) AND (summary_total_tokens IS NULL OR summary_total_tokens >= 0)', name='chat_sessions_summary_provider_tokens_check'),
        CheckConstraint('next_message_order > 0', name='chat_sessions_next_message_order_check'),
        CheckConstraint('num_nonnulls(lecture_id, video_id) = 1', name='chat_sessions_one_content_source_check'),
        CheckConstraint('summarized_until_message_order >= 0', name='chat_sessions_summary_checkpoint_check'),
        CheckConstraint('summary_token_count >= 0', name='chat_sessions_summary_token_count_check'),
        ForeignKeyConstraint(['lecture_id'], ['public.lectures.id'], ondelete='CASCADE', name='chat_sessions_lecture_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], ondelete='CASCADE', name='chat_sessions_student_id_fkey'),
        ForeignKeyConstraint(['video_id'], ['public.course_items.id'], ondelete='CASCADE', name='chat_sessions_video_id_fkey'),
        PrimaryKeyConstraint('id', name='chat_sessions_pkey'),
        Index('idx_chat_sessions_student_lecture_updated', 'student_id', 'lecture_id', 'updated_at', 'id'),
        Index('idx_chat_sessions_student_updated', 'student_id', 'updated_at', 'id'),
        Index('idx_chat_sessions_student_video_updated', 'student_id', 'video_id', 'updated_at', 'id', postgresql_where='(video_id IS NOT NULL)'),
        {'comment': 'FastAPI-owned student chat sessions. RLS enabled with no '
                'policies.',
     'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    memory_summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"), comment='Bounded conversational state only; never medical evidence.')
    summary_token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    summarized_until_message_order: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text('0'), comment='Atomic high-water mark for messages incorporated into memory_summary.')
    next_message_order: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text('1'))
    lecture_id: Mapped[Optional[int]] = mapped_column(Integer)
    title: Mapped[Optional[str]] = mapped_column(Text)
    summary_tokenizer_name: Mapped[Optional[str]] = mapped_column(Text)
    summary_model_name: Mapped[Optional[str]] = mapped_column(Text)
    summary_prompt_version: Mapped[Optional[str]] = mapped_column(Text)
    summary_input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    summary_output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    summary_total_tokens: Mapped[Optional[int]] = mapped_column(Integer, comment='Provider-reported total tokens for the latest rolling-summary update.')
    video_id: Mapped[Optional[int]] = mapped_column(Integer, comment='The course_items.id for a video-scoped chat session. New sessions use this column.')

    lecture: Mapped[Optional['Lectures']] = relationship('Lectures', back_populates='chat_sessions')
    student: Mapped['Users'] = relationship('Users', back_populates='chat_sessions')
    video: Mapped[Optional['CourseItems']] = relationship('CourseItems', back_populates='chat_sessions')
    chat_messages: Mapped[list['ChatMessages']] = relationship('ChatMessages', back_populates='session')


class CheckpointQuestions(Base):
    __tablename__ = 'checkpoint_questions'
    __table_args__ = (
        CheckConstraint('"position" > 0', name='checkpoint_questions_position_check'),
        CheckConstraint('allowed_time_seconds > 0::numeric', name='checkpoint_questions_allowed_time_seconds_check'),
        CheckConstraint('checkpoint_timestamp_seconds >= 0::numeric', name='checkpoint_questions_checkpoint_timestamp_seconds_check'),
        CheckConstraint("jsonb_typeof(options) = 'array'::text", name='checkpoint_questions_options_check'),
        CheckConstraint('num_nonnulls(lecture_id, video_id) = 1', name='checkpoint_questions_one_source'),
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='checkpoint_questions_course_id_fkey'),
        ForeignKeyConstraint(['lecture_id'], ['public.lectures.id'], ondelete='CASCADE', name='checkpoint_questions_lecture_id_fkey'),
        ForeignKeyConstraint(['topic_id'], ['public.topics.id'], ondelete='SET NULL', name='checkpoint_questions_topic_id_fkey'),
        ForeignKeyConstraint(['video_id'], ['public.course_items.id'], ondelete='CASCADE', name='checkpoint_questions_video_id_fkey'),
        PrimaryKeyConstraint('id', name='checkpoint_questions_pkey'),
        UniqueConstraint('course_id', 'source_key', name='checkpoint_questions_course_id_source_key_key'),
        Index('idx_checkpoint_questions_course', 'course_id', 'lecture_id', 'video_id', 'position'),
        Index('idx_checkpoint_questions_lecture', 'lecture_id', 'checkpoint_timestamp_seconds', postgresql_where='(lecture_id IS NOT NULL)'),
        Index('idx_checkpoint_questions_topic', 'topic_id', postgresql_where='(topic_id IS NOT NULL)'),
        Index('idx_checkpoint_questions_video', 'video_id', 'checkpoint_timestamp_seconds', postgresql_where='(video_id IS NOT NULL)'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False)
    correct_answer: Mapped[str] = mapped_column(Text, nullable=False)
    checkpoint_timestamp_seconds: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    allowed_time_seconds: Mapped[decimal.Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    lecture_id: Mapped[Optional[int]] = mapped_column(Integer)
    video_id: Mapped[Optional[int]] = mapped_column(Integer)
    topic_id: Mapped[Optional[int]] = mapped_column(Integer)

    course: Mapped['Courses'] = relationship('Courses', back_populates='checkpoint_questions')
    lecture: Mapped[Optional['Lectures']] = relationship('Lectures', back_populates='checkpoint_questions')
    topic: Mapped[Optional['Topics']] = relationship('Topics', back_populates='checkpoint_questions')
    video: Mapped[Optional['CourseItems']] = relationship('CourseItems', back_populates='checkpoint_questions')
    checkpoint_attempts: Mapped[list['CheckpointAttempts']] = relationship('CheckpointAttempts', back_populates='checkpoint_question')


class CourseWeeklyAssignments(Base):
    __tablename__ = 'course_weekly_assignments'
    __table_args__ = (
        CheckConstraint('due_at > available_from', name='course_weekly_assignments_dates'),
        CheckConstraint('lecture_id IS NOT NULL OR video_id IS NOT NULL', name='course_weekly_assignments_source_present'),
        CheckConstraint('num_nonnulls(lecture_id, video_id) = 1', name='course_weekly_assignments_one_source'),
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='course_weekly_assignments_course_id_fkey'),
        ForeignKeyConstraint(['lecture_id'], ['public.lectures.id'], ondelete='CASCADE', name='course_weekly_assignments_lecture_id_fkey'),
        ForeignKeyConstraint(['video_id'], ['public.course_items.id'], ondelete='CASCADE', name='course_weekly_assignments_video_id_fkey'),
        PrimaryKeyConstraint('id', name='course_weekly_assignments_pkey'),
        Index('idx_weekly_assignments_course_week', 'course_id', 'week_start', 'due_at'),
        Index('uq_weekly_assignments_lecture', 'course_id', 'week_start', 'lecture_id', postgresql_where='(lecture_id IS NOT NULL)', unique=True),
        Index('uq_weekly_assignments_video', 'course_id', 'week_start', 'video_id', postgresql_where='(video_id IS NOT NULL)', unique=True),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    week_start: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    available_from: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    due_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('true'))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    lecture_id: Mapped[Optional[int]] = mapped_column(Integer)
    video_id: Mapped[Optional[int]] = mapped_column(Integer)

    course: Mapped['Courses'] = relationship('Courses', back_populates='course_weekly_assignments')
    lecture: Mapped[Optional['Lectures']] = relationship('Lectures', back_populates='course_weekly_assignments')
    video: Mapped[Optional['CourseItems']] = relationship('CourseItems', back_populates='course_weekly_assignments')


class EssayQuestionVersions(Base):
    __tablename__ = 'essay_question_versions'
    __table_args__ = (
        CheckConstraint('char_length(btrim(model_answer)) >= 1 AND char_length(btrim(model_answer)) <= 50000', name='essay_question_versions_model_answer_check'),
        CheckConstraint('char_length(btrim(question_text)) >= 1 AND char_length(btrim(question_text)) <= 10000', name='essay_question_versions_question_text_check'),
        CheckConstraint('criteria_input_tokens IS NULL OR criteria_input_tokens >= 0', name='essay_question_versions_criteria_input_tokens_check'),
        CheckConstraint('criteria_latency_ms IS NULL OR criteria_latency_ms >= 0', name='essay_question_versions_criteria_latency_ms_check'),
        CheckConstraint('criteria_output_tokens IS NULL OR criteria_output_tokens >= 0', name='essay_question_versions_criteria_output_tokens_check'),
        CheckConstraint('criteria_retry_count >= 0', name='essay_question_versions_criteria_retry_count_check'),
        CheckConstraint('criteria_review_reason IS NULL OR char_length(criteria_review_reason) <= 1000', name='essay_question_versions_review_reason_check'),
        CheckConstraint("criteria_status::text = 'ready'::text AND criteria_needs_review = false AND criteria_model_identifier IS NOT NULL AND criteria_prompt_version IS NOT NULL AND criteria_raw_response IS NOT NULL AND criteria_parsed_response IS NOT NULL AND criteria_error_code IS NULL AND criteria_error_detail IS NULL OR criteria_status::text = 'needs_review'::text AND criteria_needs_review = true AND criteria_review_reason IS NOT NULL AND criteria_model_identifier IS NOT NULL AND criteria_prompt_version IS NOT NULL AND criteria_raw_response IS NOT NULL AND criteria_parsed_response IS NOT NULL AND criteria_error_code IS NULL AND criteria_error_detail IS NULL OR criteria_status::text = 'failed'::text AND criteria_parsed_response IS NULL AND criteria_error_code IS NOT NULL AND criteria_error_detail IS NOT NULL", name='essay_question_versions_state_check'),
        CheckConstraint("criteria_status::text = ANY (ARRAY['ready'::character varying, 'needs_review'::character varying, 'failed'::character varying]::text[])", name='essay_question_versions_criteria_status_check'),
        CheckConstraint("jsonb_typeof(criteria_retry_errors) = 'array'::text", name='essay_question_versions_criteria_retry_errors_check'),
        CheckConstraint('max_points > 0::numeric', name='essay_question_versions_max_points_check'),
        CheckConstraint('version_number > 0', name='essay_question_versions_version_number_check'),
        ForeignKeyConstraint(['created_by'], ['public.users.id'], ondelete='RESTRICT', name='essay_question_versions_created_by_fkey'),
        ForeignKeyConstraint(['exam_question_id'], ['public.exam_questions.id'], ondelete='RESTRICT', name='essay_question_versions_exam_question_id_fkey'),
        PrimaryKeyConstraint('id', name='essay_question_versions_pkey'),
        UniqueConstraint('exam_question_id', 'version_number', name='essay_question_versions_number_key'),
        UniqueConstraint('id', 'exam_question_id', name='essay_question_versions_id_question_key'),
        Index('idx_essay_question_versions_question', 'exam_question_id', 'version_number'),
        {'comment': 'FastAPI-owned immutable essay question/model-answer and '
                'criteria-generation snapshots. RLS enabled with no policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    exam_question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    model_answer: Mapped[str] = mapped_column(Text, nullable=False, comment='ANSWER KEY. Trusted backend and owning doctor only; never serialize to a student.')
    max_points: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    criteria_status: Mapped[str] = mapped_column(String(20), nullable=False)
    criteria_needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'))
    criteria_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    criteria_retry_errors: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    criteria_review_reason: Mapped[Optional[str]] = mapped_column(Text)
    criteria_model_identifier: Mapped[Optional[str]] = mapped_column(Text)
    criteria_prompt_version: Mapped[Optional[str]] = mapped_column(Text)
    criteria_latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    criteria_input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    criteria_output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    criteria_raw_response: Mapped[Optional[str]] = mapped_column(Text, comment='Provider structured response only; never hidden reasoning or chain-of-thought.')
    criteria_parsed_response: Mapped[Optional[dict]] = mapped_column(JSONB)
    criteria_error_code: Mapped[Optional[str]] = mapped_column(String(100))
    criteria_error_detail: Mapped[Optional[str]] = mapped_column(Text)

    essay_submissions: Mapped[list['EssaySubmissions']] = relationship('EssaySubmissions', back_populates='essay_question_versions')
    users: Mapped['Users'] = relationship('Users', back_populates='essay_question_versions')
    exam_question: Mapped['ExamQuestions'] = relationship('ExamQuestions', back_populates='essay_question_versions')
    essay_criteria: Mapped[list['EssayCriteria']] = relationship('EssayCriteria', back_populates='question_version')
    essay_question_releases: Mapped[list['EssayQuestionReleases']] = relationship('EssayQuestionReleases', back_populates='essay_question_versions')


class ExamOptions(Base):
    __tablename__ = 'exam_options'
    __table_args__ = (
        ForeignKeyConstraint(['question_id'], ['public.exam_questions.id'], ondelete='CASCADE', name='exam_options_question_id_fkey'),
        PrimaryKeyConstraint('id', name='exam_options_pkey'),
        UniqueConstraint('question_id', 'order_index', name='exam_options_question_order_key'),
        Index('idx_exam_options_question', 'question_id'),
        {'comment': 'Owned by the NestJS API. RLS enabled with no policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    text_: Mapped[str] = mapped_column('text', Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'), comment='THE ANSWER KEY. Must never reach a student response. The API serves exam options through a student schema that has no such field, rather than by omitting it at the call site.')
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    question: Mapped['ExamQuestions'] = relationship('ExamQuestions', back_populates='exam_options')


class Questions(Base):
    __tablename__ = 'questions'
    __table_args__ = (
        CheckConstraint("cognitive_level IS NULL OR (cognitive_level::text = ANY (ARRAY['recall'::character varying, 'understanding'::character varying, 'application'::character varying]::text[]))", name='questions_cognitive_level_check'),
        ForeignKeyConstraint(['lecture_id'], ['public.lectures.id'], ondelete='CASCADE', name='questions_lecture_id_fkey'),
        ForeignKeyConstraint(['topic_id'], ['public.topics.id'], name='questions_topic_id_fkey'),
        PrimaryKeyConstraint('id', name='questions_pkey'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lecture_id: Mapped[int] = mapped_column(Integer, nullable=False)
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False)
    correct_option: Mapped[str] = mapped_column(String(5), nullable=False)
    topic_id: Mapped[Optional[int]] = mapped_column(Integer)
    difficulty: Mapped[Optional[str]] = mapped_column(String(20))
    learning_objective: Mapped[Optional[str]] = mapped_column(Text)
    cognitive_level: Mapped[Optional[str]] = mapped_column(String(20))

    lecture: Mapped['Lectures'] = relationship('Lectures', back_populates='questions')
    topic: Mapped[Optional['Topics']] = relationship('Topics', back_populates='questions')
    question_attempts: Mapped[list['QuestionAttempts']] = relationship('QuestionAttempts', back_populates='question')


class Reports(Base):
    __tablename__ = 'reports'
    __table_args__ = (
        CheckConstraint("kind::text = ANY (ARRAY['module'::character varying::text, 'exam'::character varying::text])", name='reports_kind_check'),
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='reports_course_id_fkey'),
        ForeignKeyConstraint(['lecture_id'], ['public.lectures.id'], ondelete='CASCADE', name='reports_lecture_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], name='reports_student_id_fkey'),
        PrimaryKeyConstraint('id', name='reports_pkey'),
        Index('idx_reports_once', 'student_id', 'course_id', 'kind', unique=True),
        Index('idx_reports_student', 'student_id', 'generated_at'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    lecture_id: Mapped[Optional[int]] = mapped_column(Integer)

    course: Mapped['Courses'] = relationship('Courses', back_populates='reports')
    lecture: Mapped[Optional['Lectures']] = relationship('Lectures', back_populates='reports')
    student: Mapped['Users'] = relationship('Users', back_populates='reports')
    notifications: Mapped[list['Notifications']] = relationship('Notifications', back_populates='report')


class StudentStudySessions(Base):
    __tablename__ = 'student_study_sessions'
    __table_args__ = (
        CheckConstraint('last_event_at >= started_at AND (ended_at IS NULL OR ended_at >= started_at)', name='student_study_sessions_time_order'),
        CheckConstraint('lecture_id IS NOT NULL OR video_id IS NOT NULL', name='student_study_sessions_source_present'),
        CheckConstraint('num_nonnulls(lecture_id, video_id) = 1', name='student_study_sessions_one_source'),
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='student_study_sessions_course_id_fkey'),
        ForeignKeyConstraint(['lecture_id'], ['public.lectures.id'], ondelete='CASCADE', name='student_study_sessions_lecture_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], ondelete='CASCADE', name='student_study_sessions_student_id_fkey'),
        ForeignKeyConstraint(['video_id'], ['public.course_items.id'], ondelete='CASCADE', name='student_study_sessions_video_id_fkey'),
        PrimaryKeyConstraint('id', name='student_study_sessions_pkey'),
        Index('idx_study_sessions_lecture', 'lecture_id', 'started_at', postgresql_where='(lecture_id IS NOT NULL)'),
        Index('idx_study_sessions_student_course_date', 'student_id', 'course_id', 'started_at'),
        Index('idx_study_sessions_video', 'video_id', 'started_at', postgresql_where='(video_id IS NOT NULL)'),
        Index('uq_study_sessions_lecture', 'student_id', 'session_key', 'lecture_id', postgresql_where='(lecture_id IS NOT NULL)', unique=True),
        Index('uq_study_sessions_video', 'student_id', 'session_key', 'video_id', postgresql_where='(video_id IS NOT NULL)', unique=True),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    session_key: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    last_event_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    lecture_id: Mapped[Optional[int]] = mapped_column(Integer)
    video_id: Mapped[Optional[int]] = mapped_column(Integer)
    ended_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    course: Mapped['Courses'] = relationship('Courses', back_populates='student_study_sessions')
    lecture: Mapped[Optional['Lectures']] = relationship('Lectures', back_populates='student_study_sessions')
    student: Mapped['Users'] = relationship('Users', back_populates='student_study_sessions')
    video: Mapped[Optional['CourseItems']] = relationship('CourseItems', back_populates='student_study_sessions')


class TranscriptChunks(Base):
    __tablename__ = 'transcript_chunks'
    __table_args__ = (
        CheckConstraint('num_nonnulls(lecture_id, video_id) = 1', name='transcript_chunks_one_content_source_check'),
        ForeignKeyConstraint(['lecture_id'], ['public.lectures.id'], ondelete='CASCADE', name='transcript_chunks_lecture_id_fkey'),
        ForeignKeyConstraint(['video_id'], ['public.course_items.id'], ondelete='CASCADE', name='transcript_chunks_video_id_fkey'),
        PrimaryKeyConstraint('id', name='transcript_chunks_pkey'),
        Index('idx_transcript_chunks_embedding', 'embedding', postgresql_ops={'embedding': 'vector_cosine_ops'}, postgresql_using='hnsw'),
        Index('idx_transcript_chunks_lecture', 'lecture_id'),
        Index('idx_transcript_chunks_video', 'video_id', postgresql_where='(video_id IS NOT NULL)'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text_: Mapped[str] = mapped_column('text', Text, nullable=False)
    start_ts: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ts: Mapped[int] = mapped_column(Integer, nullable=False)
    lecture_id: Mapped[Optional[int]] = mapped_column(Integer)
    embedding: Mapped[Optional[Any]] = mapped_column(VECTOR(1536))
    video_id: Mapped[Optional[int]] = mapped_column(Integer, comment='The course_items.id for transcript evidence belonging to a modern course video.')

    lecture: Mapped[Optional['Lectures']] = relationship('Lectures', back_populates='transcript_chunks')
    video: Mapped[Optional['CourseItems']] = relationship('CourseItems', back_populates='transcript_chunks')


class VideoEvents(Base):
    __tablename__ = 'video_events'
    __table_args__ = (
        CheckConstraint("event_type::text = ANY (ARRAY['play'::character varying::text, 'pause'::character varying::text, 'seek'::character varying::text, 'skip'::character varying::text, 'complete'::character varying::text, 'rewatch_segment'::character varying::text, 'heartbeat'::character varying::text, 'tab_hidden'::character varying::text, 'tab_visible'::character varying::text])", name='video_events_event_type_check'),
        CheckConstraint('num_nonnulls(lecture_id, video_id) = 1', name='video_events_one_content_source_check'),
        CheckConstraint('playback_rate IS NULL OR playback_rate >= 0.25 AND playback_rate <= 4.00', name='video_events_playback_rate_check'),
        ForeignKeyConstraint(['lecture_id'], ['public.lectures.id'], ondelete='CASCADE', name='video_events_lecture_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], name='video_events_student_id_fkey'),
        ForeignKeyConstraint(['video_id'], ['public.course_items.id'], ondelete='CASCADE', name='video_events_video_id_fkey'),
        PrimaryKeyConstraint('id', name='video_events_pkey'),
        Index('idx_video_events_lecture', 'lecture_id'),
        Index('idx_video_events_session', 'student_id', 'lecture_id', 'session_id', 'created_at'),
        Index('idx_video_events_student', 'student_id'),
        Index('idx_video_events_student_video_session', 'student_id', 'video_id', 'session_id', 'created_at', postgresql_where='(video_id IS NOT NULL)'),
        Index('uq_video_events_student_client_event', 'student_id', 'client_event_id', postgresql_where='(client_event_id IS NOT NULL)', unique=True),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    lecture_id: Mapped[Optional[int]] = mapped_column(Integer)
    video_ts: Mapped[Optional[float]] = mapped_column(Double(53))
    session_id: Mapped[Optional[str]] = mapped_column(String(64))
    video_id: Mapped[Optional[int]] = mapped_column(Integer)
    client_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    playback_rate: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric(4, 2))

    lecture: Mapped[Optional['Lectures']] = relationship('Lectures', back_populates='video_events')
    student: Mapped['Users'] = relationship('Users', back_populates='video_events')
    video: Mapped[Optional['CourseItems']] = relationship('CourseItems', back_populates='video_events')


class CheckpointAttempts(Base):
    __tablename__ = 'checkpoint_attempts'
    __table_args__ = (
        CheckConstraint('NOT (skipped AND timed_out) AND (answered_at IS NOT NULL AND selected_answer IS NOT NULL AND is_correct IS NOT NULL AND NOT skipped AND NOT timed_out OR answered_at IS NULL AND selected_answer IS NULL AND is_correct IS NULL AND (skipped OR timed_out))', name='checkpoint_attempts_outcome'),
        CheckConstraint('answered_at IS NULL AND response_time_ms IS NULL OR answered_at IS NOT NULL AND response_time_ms IS NOT NULL', name='checkpoint_attempts_response_time'),
        CheckConstraint('attempt_number > 0', name='checkpoint_attempts_attempt_number_check'),
        CheckConstraint('response_time_ms IS NULL OR response_time_ms >= 0', name='checkpoint_attempts_response_time_ms_check'),
        ForeignKeyConstraint(['checkpoint_question_id'], ['public.checkpoint_questions.id'], ondelete='CASCADE', name='checkpoint_attempts_checkpoint_question_id_fkey'),
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='checkpoint_attempts_course_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], ondelete='CASCADE', name='checkpoint_attempts_student_id_fkey'),
        PrimaryKeyConstraint('id', name='checkpoint_attempts_pkey'),
        UniqueConstraint('student_id', 'checkpoint_question_id', 'session_id', 'attempt_number', name='checkpoint_attempts_student_id_checkpoint_question_id_sessi_key'),
        Index('idx_checkpoint_attempts_question', 'checkpoint_question_id', 'shown_at'),
        Index('idx_checkpoint_attempts_student_course_date', 'student_id', 'course_id', 'shown_at'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint_question_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    shown_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'))
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'))
    attempt_number: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text('1'))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    answered_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer)
    selected_answer: Mapped[Optional[str]] = mapped_column(Text)
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean)

    checkpoint_question: Mapped['CheckpointQuestions'] = relationship('CheckpointQuestions', back_populates='checkpoint_attempts')
    course: Mapped['Courses'] = relationship('Courses', back_populates='checkpoint_attempts')
    student: Mapped['Users'] = relationship('Users', back_populates='checkpoint_attempts')
    chat_messages: Mapped[list['ChatMessages']] = relationship('ChatMessages', back_populates='trigger_checkpoint_attempt')


class EssayCriteria(Base):
    __tablename__ = 'essay_criteria'
    __table_args__ = (
        CheckConstraint('"position" > 0 AND "position" <= 50', name='essay_criteria_position_check'),
        CheckConstraint('char_length(btrim(claim)) >= 1 AND char_length(btrim(claim)) <= 2000', name='essay_criteria_claim_check'),
        CheckConstraint("criterion_key::text ~ '^C[1-9][0-9]*$'::text", name='essay_criteria_criterion_key_check'),
        ForeignKeyConstraint(['question_version_id'], ['public.essay_question_versions.id'], ondelete='RESTRICT', name='essay_criteria_question_version_id_fkey'),
        PrimaryKeyConstraint('id', name='essay_criteria_pkey'),
        UniqueConstraint('id', 'question_version_id', name='essay_criteria_id_version_key'),
        UniqueConstraint('question_version_id', 'criterion_key', name='essay_criteria_version_key'),
        UniqueConstraint('question_version_id', 'position', name='essay_criteria_version_position_key'),
        Index('idx_essay_criteria_version', 'question_version_id', 'position'),
        {'comment': 'Atomic criteria extracted once for an immutable essay question '
                'version.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    question_version_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    criterion_key: Mapped[str] = mapped_column(String(20), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    question_version: Mapped['EssayQuestionVersions'] = relationship('EssayQuestionVersions', back_populates='essay_criteria')
    essay_criterion_results: Mapped[list['EssayCriterionResults']] = relationship('EssayCriterionResults', back_populates='criterion')


class EssayQuestionReleases(Base):
    __tablename__ = 'essay_question_releases'
    __table_args__ = (
        CheckConstraint('release_note IS NULL OR char_length(release_note) <= 2000', name='essay_question_releases_release_note_check'),
        ForeignKeyConstraint(['exam_question_id'], ['public.exam_questions.id'], ondelete='RESTRICT', name='essay_question_releases_exam_question_id_fkey'),
        ForeignKeyConstraint(['question_version_id', 'exam_question_id'], ['public.essay_question_versions.id', 'public.essay_question_versions.exam_question_id'], ondelete='RESTRICT', name='essay_question_releases_version_fkey'),
        ForeignKeyConstraint(['released_by'], ['public.users.id'], ondelete='RESTRICT', name='essay_question_releases_released_by_fkey'),
        PrimaryKeyConstraint('id', name='essay_question_releases_pkey'),
        Index('idx_essay_question_releases_active', 'exam_question_id', 'id'),
        {'comment': 'Append-only publication history. Highest id per exam_question_id '
                'is active for new submissions.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    exam_question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    question_version_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    released_by: Mapped[int] = mapped_column(Integer, nullable=False)
    released_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    release_note: Mapped[Optional[str]] = mapped_column(Text)

    exam_question: Mapped['ExamQuestions'] = relationship('ExamQuestions', back_populates='essay_question_releases')
    essay_question_versions: Mapped['EssayQuestionVersions'] = relationship('EssayQuestionVersions', back_populates='essay_question_releases')
    users: Mapped['Users'] = relationship('Users', back_populates='essay_question_releases')


class Notifications(Base):
    __tablename__ = 'notifications'
    __table_args__ = (
        ForeignKeyConstraint(['report_id'], ['public.reports.id'], ondelete='CASCADE', name='notifications_report_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], name='notifications_student_id_fkey'),
        ForeignKeyConstraint(['user_id'], ['public.users.id'], name='notifications_user_id_fkey'),
        PrimaryKeyConstraint('id', name='notifications_pkey'),
        Index('idx_notifications_inbox', 'user_id', 'read_at', 'created_at'),
        Index('idx_notifications_once', 'user_id', 'report_id', postgresql_where='(report_id IS NOT NULL)', unique=True),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    body: Mapped[Optional[str]] = mapped_column(Text)
    report_id: Mapped[Optional[int]] = mapped_column(Integer)
    student_id: Mapped[Optional[int]] = mapped_column(Integer)
    read_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    report: Mapped[Optional['Reports']] = relationship('Reports', back_populates='notifications')
    student: Mapped[Optional['Users']] = relationship('Users', foreign_keys=[student_id], back_populates='notifications_student')
    user: Mapped['Users'] = relationship('Users', foreign_keys=[user_id], back_populates='notifications_user')


class QuestionAttempts(Base):
    __tablename__ = 'question_attempts'
    __table_args__ = (
        ForeignKeyConstraint(['question_id'], ['public.questions.id'], ondelete='CASCADE', name='question_attempts_question_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], name='question_attempts_student_id_fkey'),
        PrimaryKeyConstraint('id', name='question_attempts_pkey'),
        Index('idx_question_attempts_option', 'question_id', 'selected_option', postgresql_where='(selected_option IS NOT NULL)'),
        Index('idx_question_attempts_student', 'student_id'),
        Index('idx_question_attempts_student_date', 'student_id', 'answered_at'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    answered_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    selected_option: Mapped[Optional[str]] = mapped_column(String(5))

    question: Mapped['Questions'] = relationship('Questions', back_populates='question_attempts')
    student: Mapped['Users'] = relationship('Users', back_populates='question_attempts')


class ChatMessages(Base):
    __tablename__ = 'chat_messages'
    __table_args__ = (
        CheckConstraint('(input_tokens IS NULL OR input_tokens >= 0) AND (output_tokens IS NULL OR output_tokens >= 0)', name='chat_messages_provider_tokens_check'),
        CheckConstraint("role::text = ANY (ARRAY['user'::character varying, 'assistant'::character varying]::text[])", name='chat_messages_role_check'),
        CheckConstraint("status::text = ANY (ARRAY['pending'::character varying, 'completed'::character varying, 'failed'::character varying]::text[])", name='chat_messages_status_check'),
        CheckConstraint('token_count >= 0', name='chat_messages_token_count_check'),
        CheckConstraint('total_tokens IS NULL OR total_tokens >= 0', name='chat_messages_total_tokens_check'),
        ForeignKeyConstraint(['reply_to_message_id'], ['public.chat_messages.id'], ondelete='SET NULL', name='chat_messages_reply_to_message_id_fkey'),
        ForeignKeyConstraint(['session_id'], ['public.chat_sessions.id'], ondelete='CASCADE', name='chat_messages_session_id_fkey'),
        ForeignKeyConstraint(['topic_id'], ['public.topics.id'], ondelete='SET NULL', name='chat_messages_topic_id_fkey'),
        ForeignKeyConstraint(['trigger_checkpoint_attempt_id'], ['public.checkpoint_attempts.id'], ondelete='SET NULL', name='chat_messages_trigger_checkpoint_attempt_id_fkey'),
        PrimaryKeyConstraint('id', name='chat_messages_pkey'),
        Index('idx_chat_messages_session_created', 'session_id', 'created_at'),
        Index('idx_chat_messages_session_order_desc', 'session_id', 'message_order'),
        Index('idx_chat_messages_topic', 'topic_id', 'created_at', postgresql_where='(topic_id IS NOT NULL)'),
        Index('uq_chat_messages_assistant_reply', 'reply_to_message_id', postgresql_where="(((role)::text = 'assistant'::text) AND (reply_to_message_id IS NOT NULL))", unique=True),
        Index('uq_chat_messages_session_order', 'session_id', 'message_order', unique=True),
        Index('uq_chat_messages_user_idempotency', 'session_id', 'idempotency_key', postgresql_where="(((role)::text = 'user'::text) AND (idempotency_key IS NOT NULL))", unique=True),
        {'comment': 'FastAPI-owned chat messages and retrieval citations. RLS enabled '
                'with no policies.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    message_order: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='Stable order allocated under a session row lock; timestamps are not ordering keys.')
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    tokenizer_name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'legacy-unknown'::text"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'completed'::character varying"))
    standalone_query: Mapped[Optional[str]] = mapped_column(Text)
    citations: Mapped[Optional[dict]] = mapped_column(JSONB)
    model_name: Mapped[Optional[str]] = mapped_column(Text)
    prompt_version: Mapped[Optional[str]] = mapped_column(Text)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    grounded: Mapped[Optional[bool]] = mapped_column(Boolean)
    failure_code: Mapped[Optional[str]] = mapped_column(Text)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), comment='Client retry key, unique per session for user messages.')
    reply_to_message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, comment='Provider-reported prompt + candidate + thinking tokens when available.')
    topic_id: Mapped[Optional[int]] = mapped_column(Integer)
    trigger_checkpoint_attempt_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    reply_to_message: Mapped[Optional['ChatMessages']] = relationship('ChatMessages', remote_side=[id], back_populates='reply_to_message_reverse')
    reply_to_message_reverse: Mapped[list['ChatMessages']] = relationship('ChatMessages', remote_side=[reply_to_message_id], back_populates='reply_to_message')
    session: Mapped['ChatSessions'] = relationship('ChatSessions', back_populates='chat_messages')
    topic: Mapped[Optional['Topics']] = relationship('Topics', back_populates='chat_messages')
    trigger_checkpoint_attempt: Mapped[Optional['CheckpointAttempts']] = relationship('CheckpointAttempts', back_populates='chat_messages')


class EssayCriterionResults(Base):
    __tablename__ = 'essay_criterion_results'
    __table_args__ = (
        CheckConstraint('awarded_points = round(weight * status_factor, 10)', name='essay_criterion_results_points_check'),
        CheckConstraint('awarded_points >= 0::numeric', name='essay_criterion_results_awarded_points_check'),
        CheckConstraint('char_length(btrim(reason)) >= 1 AND char_length(btrim(reason)) <= 1000', name='essay_criterion_results_reason_check'),
        CheckConstraint('evidence IS NULL OR char_length(evidence) <= 5000', name='essay_criterion_results_evidence_check'),
        CheckConstraint("status::text = 'yes'::text AND status_factor = 1.0 OR status::text = 'partial'::text AND status_factor = 0.5 OR (status::text = ANY (ARRAY['no'::character varying, 'contradicted'::character varying]::text[])) AND status_factor = 0.0", name='essay_criterion_results_factor_check'),
        CheckConstraint("status::text = ANY (ARRAY['yes'::character varying, 'partial'::character varying, 'no'::character varying, 'contradicted'::character varying]::text[])", name='essay_criterion_results_status_check'),
        CheckConstraint('weight > 0::numeric', name='essay_criterion_results_weight_check'),
        ForeignKeyConstraint(['criterion_id'], ['public.essay_criteria.id'], ondelete='RESTRICT', name='essay_criterion_results_criterion_id_fkey'),
        ForeignKeyConstraint(['grading_run_id'], ['public.essay_grading_runs.id'], ondelete='RESTRICT', name='essay_criterion_results_grading_run_id_fkey'),
        PrimaryKeyConstraint('id', name='essay_criterion_results_pkey'),
        UniqueConstraint('grading_run_id', 'criterion_id', name='essay_criterion_results_run_criterion_key'),
        Index('idx_essay_criterion_results_run', 'grading_run_id'),
        {'comment': 'One deterministic contribution for every criterion in a '
                'successful grading run.',
     'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    grading_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    criterion_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[decimal.Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    status_factor: Mapped[decimal.Decimal] = mapped_column(Numeric(2, 1), nullable=False)
    awarded_points: Mapped[decimal.Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    evidence: Mapped[Optional[str]] = mapped_column(Text)

    criterion: Mapped['EssayCriteria'] = relationship('EssayCriteria', back_populates='essay_criterion_results')
    grading_run: Mapped['EssayGradingRuns'] = relationship('EssayGradingRuns', back_populates='essay_criterion_results')
