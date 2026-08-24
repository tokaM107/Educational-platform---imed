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
import uuid

from pgvector.sqlalchemy.vector import VECTOR
from sqlalchemy import Boolean, CHAR, CheckConstraint, Date, DateTime, Double, ForeignKeyConstraint, Index, Integer, PrimaryKeyConstraint, SmallInteger, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


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

    questions: Mapped[list['Questions']] = relationship('Questions', back_populates='topic')


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
    password_hash: Mapped[Optional[str]] = mapped_column(Text, comment='argon2id hash. Written only by the NestJS API; NULL means the account cannot log in yet.')
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    phone_verified_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    auth_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    birth_date: Mapped[Optional[datetime.date]] = mapped_column(Date, comment='Calendar date of birth. Written by NestJS student signup; nullable so existing FastAPI rows stay valid.')

    courses: Mapped[list['Courses']] = relationship('Courses', back_populates='doctor')
    password_reset_codes: Mapped[list['PasswordResetCodes']] = relationship('PasswordResetCodes', back_populates='user')
    refresh_tokens: Mapped[list['RefreshTokens']] = relationship('RefreshTokens', back_populates='user')
    subscriptions_doctor: Mapped[list['Subscriptions']] = relationship('Subscriptions', foreign_keys='[Subscriptions.doctor_id]', back_populates='doctor')
    subscriptions_student: Mapped[list['Subscriptions']] = relationship('Subscriptions', foreign_keys='[Subscriptions.student_id]', back_populates='student')
    enrollments: Mapped[list['Enrollments']] = relationship('Enrollments', back_populates='student')
    report_narratives: Mapped[list['ReportNarratives']] = relationship('ReportNarratives', back_populates='student')
    lectures: Mapped[list['Lectures']] = relationship('Lectures', back_populates='doctor')
    reports: Mapped[list['Reports']] = relationship('Reports', back_populates='student')
    video_events: Mapped[list['VideoEvents']] = relationship('VideoEvents', back_populates='student')
    notifications_student: Mapped[list['Notifications']] = relationship('Notifications', foreign_keys='[Notifications.student_id]', back_populates='student')
    notifications_user: Mapped[list['Notifications']] = relationship('Notifications', foreign_keys='[Notifications.user_id]', back_populates='user')
    question_attempts: Mapped[list['QuestionAttempts']] = relationship('QuestionAttempts', back_populates='student')


class Courses(Base):
    __tablename__ = 'courses'
    __table_args__ = (
        CheckConstraint('academic_year IS NULL OR academic_year >= 1 AND academic_year <= 7', name='courses_academic_year_check'),
        ForeignKeyConstraint(['doctor_id'], ['public.users.id'], name='courses_doctor_id_fkey'),
        ForeignKeyConstraint(['subject_id'], ['public.subjects.id'], name='courses_subject_id_fkey'),
        PrimaryKeyConstraint('id', name='courses_pkey'),
        UniqueConstraint('id', 'doctor_id', name='courses_id_doctor_key'),
        Index('idx_courses_academic_year', 'academic_year'),
        Index('idx_courses_doctor', 'doctor_id'),
        Index('idx_courses_subject', 'subject_id'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doctor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    subject_id: Mapped[Optional[int]] = mapped_column(Integer)
    academic_year: Mapped[Optional[int]] = mapped_column(SmallInteger)

    doctor: Mapped['Users'] = relationship('Users', back_populates='courses')
    subject: Mapped[Optional['Subjects']] = relationship('Subjects', back_populates='courses')
    enrollments: Mapped[list['Enrollments']] = relationship('Enrollments', back_populates='course')
    modules: Mapped[list['Modules']] = relationship('Modules', back_populates='course')
    report_narratives: Mapped[list['ReportNarratives']] = relationship('ReportNarratives', back_populates='course')
    lectures_course_doctor: Mapped[list['Lectures']] = relationship('Lectures', foreign_keys='[Lectures.course_id, Lectures.doctor_id]', back_populates='course_doctor')
    lectures_course: Mapped[list['Lectures']] = relationship('Lectures', foreign_keys='[Lectures.course_id]', back_populates='course')
    reports: Mapped[list['Reports']] = relationship('Reports', back_populates='course')


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
        Index('refresh_tokens_user_id_idx', 'user_id'),
        {'schema': 'public'}
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


class Enrollments(Base):
    __tablename__ = 'enrollments'
    __table_args__ = (
        ForeignKeyConstraint(['course_id'], ['public.courses.id'], ondelete='CASCADE', name='enrollments_course_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], name='enrollments_student_id_fkey'),
        PrimaryKeyConstraint('id', name='enrollments_pkey'),
        UniqueConstraint('student_id', 'course_id', name='enrollments_student_id_course_id_key'),
        Index('idx_enrollments_student', 'student_id'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[int] = mapped_column(Integer, nullable=False)
    enrolled_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    course: Mapped['Courses'] = relationship('Courses', back_populates='enrollments')
    student: Mapped['Users'] = relationship('Users', back_populates='enrollments')


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
    video_url: Mapped[Optional[str]] = mapped_column(Text)
    course_id: Mapped[Optional[int]] = mapped_column(Integer)
    module_id: Mapped[Optional[int]] = mapped_column(Integer)
    bunny_video_id: Mapped[Optional[str]] = mapped_column(Text)

    course_doctor: Mapped[Optional['Courses']] = relationship('Courses', foreign_keys=[course_id, doctor_id], back_populates='lectures_course_doctor')
    course: Mapped[Optional['Courses']] = relationship('Courses', foreign_keys=[course_id], back_populates='lectures_course')
    doctor: Mapped['Users'] = relationship('Users', back_populates='lectures')
    module: Mapped[Optional['Modules']] = relationship('Modules', back_populates='lectures')
    questions: Mapped[list['Questions']] = relationship('Questions', back_populates='lecture')
    reports: Mapped[list['Reports']] = relationship('Reports', back_populates='lecture')
    transcript_chunks: Mapped[list['TranscriptChunks']] = relationship('TranscriptChunks', back_populates='lecture')
    video_events: Mapped[list['VideoEvents']] = relationship('VideoEvents', back_populates='lecture')


class Questions(Base):
    __tablename__ = 'questions'
    __table_args__ = (
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


class TranscriptChunks(Base):
    __tablename__ = 'transcript_chunks'
    __table_args__ = (
        ForeignKeyConstraint(['lecture_id'], ['public.lectures.id'], ondelete='CASCADE', name='transcript_chunks_lecture_id_fkey'),
        PrimaryKeyConstraint('id', name='transcript_chunks_pkey'),
        Index('idx_transcript_chunks_embedding', 'embedding', postgresql_ops={'embedding': 'vector_cosine_ops'}, postgresql_using='hnsw'),
        Index('idx_transcript_chunks_lecture', 'lecture_id'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lecture_id: Mapped[int] = mapped_column(Integer, nullable=False)
    text_: Mapped[str] = mapped_column('text', Text, nullable=False)
    start_ts: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ts: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[Optional[Any]] = mapped_column(VECTOR(1536))

    lecture: Mapped['Lectures'] = relationship('Lectures', back_populates='transcript_chunks')


class VideoEvents(Base):
    __tablename__ = 'video_events'
    __table_args__ = (
        CheckConstraint("event_type::text = ANY (ARRAY['play'::character varying::text, 'pause'::character varying::text, 'seek'::character varying::text, 'skip'::character varying::text, 'complete'::character varying::text, 'rewatch_segment'::character varying::text, 'heartbeat'::character varying::text, 'tab_hidden'::character varying::text, 'tab_visible'::character varying::text])", name='video_events_event_type_check'),
        ForeignKeyConstraint(['lecture_id'], ['public.lectures.id'], ondelete='CASCADE', name='video_events_lecture_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], name='video_events_student_id_fkey'),
        PrimaryKeyConstraint('id', name='video_events_pkey'),
        Index('idx_video_events_lecture', 'lecture_id'),
        Index('idx_video_events_session', 'student_id', 'lecture_id', 'session_id', 'created_at'),
        Index('idx_video_events_student', 'student_id'),
        {'schema': 'public'}
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    lecture_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    video_ts: Mapped[Optional[float]] = mapped_column(Double(53))
    session_id: Mapped[Optional[str]] = mapped_column(String(64))

    lecture: Mapped['Lectures'] = relationship('Lectures', back_populates='video_events')
    student: Mapped['Users'] = relationship('Users', back_populates='video_events')


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
