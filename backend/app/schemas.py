from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=2, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class RegistrationRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    enrollment_number: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=10, max_length=128)
    confirm_password: str = Field(min_length=10, max_length=128)


class OTPVerify(BaseModel):
    challenge_id: str = Field(min_length=10, max_length=64)
    otp: str = Field(pattern=r"^[0-9]{6}$")


class PasswordResetRequest(BaseModel):
    identifier: str = Field(min_length=2, max_length=254)


class PasswordResetVerify(BaseModel):
    challenge_id: str = Field(min_length=10, max_length=64)
    otp: str = Field(pattern=r"^[0-9]{6}$")
    new_password: str = Field(min_length=10, max_length=128)
    confirm_new_password: str = Field(min_length=10, max_length=128)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)
    confirm_new_password: str = Field(min_length=10, max_length=128)


class ExamCodeRequest(BaseModel):
    code: str = Field(pattern=r"^[0-9]{6}$")


class ExamSettingUpdate(BaseModel):
    enabled: bool | None = None
    exam_name: str | None = Field(default=None, min_length=2, max_length=200)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    results_published: bool | None = None
    official_submission_policy: str | None = Field(default=None, pattern=r"^(BEST|LATEST|ADMIN_SELECTED)$")
    passing_percentage: float | None = Field(default=None, ge=0, le=100)


class GenerateExamCodeRequest(BaseModel):
    valid_minutes: int = Field(default=180, ge=5, le=1440)


class RecoveryCreate(BaseModel):
    request_type: str = Field(pattern=r"^ACCOUNT_RECOVERY$")
    full_name: str = Field(default="", max_length=160)
    enrollment_number: str = Field(default="", max_length=64)
    email: str = Field(default="", max_length=254)
    new_email: str = Field(default="", max_length=254)
    reason: str = Field(min_length=3, max_length=3000)


class RecoveryComplete(BaseModel):
    request_id: str
    token: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class RecoveryDecision(BaseModel):
    remarks: str = Field(default="", max_length=3000)
    token_valid_minutes: int = Field(default=60, ge=5, le=1440)
    approved_email: str | None = Field(default=None, max_length=254)


class UserDecision(BaseModel):
    remarks: str = Field(default="", max_length=2000)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    email: EmailStr | None = None
    enrollment_number: str | None = Field(default=None, min_length=2, max_length=64)
    status: str | None = Field(default=None, pattern=r"^(PENDING|APPROVED|REJECTED|DISABLED)$")
    active: bool | None = None


class AdminUserCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    enrollment_number: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=10, max_length=128)
    role: str = Field(default="student", pattern=r"^(student|admin)$")
    approved: bool = True


class AdminAccountCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    confirm_password: str = Field(min_length=10, max_length=128)


class AdminPasswordReset(BaseModel):
    new_password: str = Field(min_length=10, max_length=128)
    confirm_password: str = Field(min_length=10, max_length=128)


class ProblemCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    statement: str = Field(min_length=1, max_length=100_000)
    input_format: str = Field(default="", max_length=20_000)
    output_format: str = Field(default="", max_length=20_000)
    constraints: str = Field(default="", max_length=20_000)
    time_limit_ms: int = Field(default=2000, ge=100, le=30_000)
    memory_limit_mb: int = Field(default=256, ge=32, le=2048)
    active: bool = True
    accepting_submissions: bool = True
    is_practice: bool = False
    exam_mode_enabled: bool = True
    exam_duration_minutes: int = Field(default=60, ge=1, le=1440)
    submission_limit: int | None = Field(default=None, ge=1, le=1000)


class ProblemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    statement: str | None = Field(default=None, min_length=1, max_length=100_000)
    input_format: str | None = Field(default=None, max_length=20_000)
    output_format: str | None = Field(default=None, max_length=20_000)
    constraints: str | None = Field(default=None, max_length=20_000)
    time_limit_ms: int | None = Field(default=None, ge=100, le=30_000)
    memory_limit_mb: int | None = Field(default=None, ge=32, le=2048)
    active: bool | None = None
    accepting_submissions: bool | None = None
    is_practice: bool | None = None
    exam_mode_enabled: bool | None = None
    exam_duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    submission_limit: int | None = Field(default=None, ge=1, le=1000)


class TestCaseCreate(BaseModel):
    position: int = Field(ge=1, le=10_000)
    input_data: str = Field(default="", max_length=1_000_000)
    expected_output: str = Field(default="", max_length=1_000_000)
    is_public: bool = False
    points: float = Field(default=1.0, ge=0, le=100_000)


class FlagCreate(BaseModel):
    session_id: str | None = None
    problem_id: int | None = None
    submission_id: str | None = None
    event_type: str = Field(min_length=2, max_length=64)
    details: str = Field(default="", max_length=1000)
    client_timestamp: str = Field(default="", max_length=64)


class ManualGrade(BaseModel):
    manual_score: float | None = Field(default=None, ge=0, le=100_000)
    notes: str = Field(default="", max_length=5000)


class AuditRemark(BaseModel):
    remarks: str = Field(default="", max_length=2000)


class AccessModeUpdate(BaseModel):
    mode: str = Field(pattern=r"^(ALL|APPROVED|REJECTED)$")


class AccessIPCreate(BaseModel):
    ip_value: str = Field(min_length=2, max_length=64)
    note: str = Field(default="", max_length=200)

class RegistrationDomainUpdate(BaseModel):
    mode: str = Field(pattern=r"^(ALL|GMAIL|IITRPR|CUSTOM)$")
    custom_domain: str = Field(default="", max_length=254)


class ResultEmailRequest(BaseModel):
    problem_ids: list[int] = Field(default_factory=list, min_length=1, max_length=200)
    user_id: int | None = None


class ResultSelection(BaseModel):
    problem_ids: list[int] = Field(default_factory=list, min_length=1, max_length=200)
