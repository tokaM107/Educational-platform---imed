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
from sqlalchemy.dialects.postgresql import INET, JSONB
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
    is_suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'), comment='When true, NestJS refuses login and refresh for this student or teacher. Default false so FastAPI-created rows stay able to sign in.')
    password_hash: Mapped[Optional[str]] = mapped_column(Text, comment='argon2id hash. Written only by the NestJS API; NULL means the account cannot log in yet.')
    birth_date: Mapped[Optional[datetime.date]] = mapped_column(Date, comment='Calendar date of birth. Written by NestJS student signup; nullable so existing FastAPI rows stay valid.')
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    phone_verified_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    auth_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)

    access_code_batches: Mapped[list['AccessCodeBatches']] = relationship('AccessCodeBatches', back_populates='instructor')
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
    exam_attempts: Mapped[list['ExamAttempts']] = relationship('ExamAttempts', back_populates='user')
    lectures: Mapped[list['Lectures']] = relationship('Lectures', back_populates='doctor')
    chat_sessions: Mapped[list['ChatSessions']] = relationship('ChatSessions', back_populates='student')
    reports: Mapped[list['Reports']] = relationship('Reports', back_populates='student')
    video_events: Mapped[list['VideoEvents']] = relationship('VideoEvents', back_populates='student')
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
    course_items: Mapped[list['CourseItems']] = relationship('CourseItems', back_populates='course')
    lectures_course_doctor: Mapped[list['Lectures']] = relationship('Lectures', foreign_keys='[Lectures.course_id, Lectures.doctor_id]', back_populates='course_doctor')
    lectures_course: Mapped[list['Lectures']] = relationship('Lectures', foreign_keys='[Lectures.course_id]', back_populates='course')
    reports: Mapped[list['Reports']] = relationship('Reports', back_populates='course')


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

    exam: Mapped['Exams'] = relationship('Exams', back_populates='exam_attempts')
    user: Mapped['Users'] = relationship('Users', back_populates='exam_attempts')


class ExamQuestions(Base):
    __tablename__ = 'exam_questions'
    __table_args__ = (
        CheckConstraint('points > 0', name='exam_questions_points_check'),
        CheckConstraint("type::text = ANY (ARRAY['single_choice'::character varying, 'multi_choice'::character varying, 'true_false'::character varying]::text[])", name='exam_questions_type_check'),
        ForeignKeyConstraint(['exam_id'], ['public.exams.id'], ondelete='CASCADE', name='exam_questions_exam_id_fkey'),
        PrimaryKeyConstraint('id', name='exam_questions_pkey'),
        UniqueConstraint('exam_id', 'order_index', name='exam_questions_exam_order_key'),
        Index('idx_exam_questions_exam', 'exam_id', 'order_index'),
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

    exam: Mapped['Exams'] = relationship('Exams', back_populates='exam_questions')
    exam_options: Mapped[list['ExamOptions']] = relationship('ExamOptions', back_populates='question')


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
    questions: Mapped[list['Questions']] = relationship('Questions', back_populates='lecture')
    reports: Mapped[list['Reports']] = relationship('Reports', back_populates='lecture')
    transcript_chunks: Mapped[list['TranscriptChunks']] = relationship('TranscriptChunks', back_populates='lecture')
    video_events: Mapped[list['VideoEvents']] = relationship('VideoEvents', back_populates='lecture')


class ChatSessions(Base):
    __tablename__ = 'chat_sessions'
    __table_args__ = (
        CheckConstraint('next_message_order > 0', name='chat_sessions_next_message_order_check'),
        CheckConstraint('summarized_until_message_order >= 0', name='chat_sessions_summary_checkpoint_check'),
        CheckConstraint('summary_token_count >= 0', name='chat_sessions_summary_token_count_check'),
        ForeignKeyConstraint(['lecture_id'], ['public.lectures.id'], ondelete='CASCADE', name='chat_sessions_lecture_id_fkey'),
        ForeignKeyConstraint(['student_id'], ['public.users.id'], ondelete='CASCADE', name='chat_sessions_student_id_fkey'),
        PrimaryKeyConstraint('id', name='chat_sessions_pkey'),
        Index('idx_chat_sessions_student_lecture_updated', 'student_id', 'lecture_id', 'updated_at', 'id'),
        Index('idx_chat_sessions_student_updated', 'student_id', 'updated_at', 'id'),
        {'comment': 'FastAPI-owned student chat sessions. RLS enabled with no '
                'policies.',
     'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    lecture_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    memory_summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''::text"), comment='Bounded conversational state only; never medical evidence.')
    summary_token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    summarized_until_message_order: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text('0'), comment='Atomic high-water mark for messages incorporated into memory_summary.')
    next_message_order: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text('1'))
    title: Mapped[Optional[str]] = mapped_column(Text)
    summary_tokenizer_name: Mapped[Optional[str]] = mapped_column(Text)

    lecture: Mapped['Lectures'] = relationship('Lectures', back_populates='chat_sessions')
    student: Mapped['Users'] = relationship('Users', back_populates='chat_sessions')
    chat_messages: Mapped[list['ChatMessages']] = relationship('ChatMessages', back_populates='session')


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


class ChatMessages(Base):
    __tablename__ = 'chat_messages'
    __table_args__ = (
        CheckConstraint('(input_tokens IS NULL OR input_tokens >= 0) AND (output_tokens IS NULL OR output_tokens >= 0)', name='chat_messages_provider_tokens_check'),
        CheckConstraint("role::text = ANY (ARRAY['user'::character varying, 'assistant'::character varying]::text[])", name='chat_messages_role_check'),
        CheckConstraint("status::text = ANY (ARRAY['pending'::character varying, 'completed'::character varying, 'failed'::character varying]::text[])", name='chat_messages_status_check'),
        CheckConstraint('token_count >= 0', name='chat_messages_token_count_check'),
        ForeignKeyConstraint(['reply_to_message_id'], ['public.chat_messages.id'], ondelete='SET NULL', name='chat_messages_reply_to_message_id_fkey'),
        ForeignKeyConstraint(['session_id'], ['public.chat_sessions.id'], ondelete='CASCADE', name='chat_messages_session_id_fkey'),
        PrimaryKeyConstraint('id', name='chat_messages_pkey'),
        Index('idx_chat_messages_session_created', 'session_id', 'created_at'),
        Index('idx_chat_messages_session_order_desc', 'session_id', 'message_order'),
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

    reply_to_message: Mapped[Optional['ChatMessages']] = relationship('ChatMessages', remote_side=[id], back_populates='reply_to_message_reverse')
    reply_to_message_reverse: Mapped[list['ChatMessages']] = relationship('ChatMessages', remote_side=[reply_to_message_id], back_populates='reply_to_message')
    session: Mapped['ChatSessions'] = relationship('ChatSessions', back_populates='chat_messages')


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
