from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "College Code Judge")
    environment: str = os.getenv("APP_ENV", "development")
    host: str = os.getenv("APP_HOST", "127.0.0.1")
    port: int = int(os.getenv("APP_PORT", "8000"))

    data_dir: Path = Path(os.getenv("DATA_DIR", str(BACKEND_DIR / "data"))).resolve()
    database_url: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{(Path(os.getenv('DATA_DIR', str(BACKEND_DIR / 'data'))) / 'judge.db').resolve()}",
    )

    session_cookie_name: str = os.getenv("SESSION_COOKIE_NAME", "judge_session")
    csrf_cookie_name: str = os.getenv("CSRF_COOKIE_NAME", "judge_csrf")
    session_ttl_hours: int = int(os.getenv("SESSION_TTL_HOURS", "8"))
    cookie_secure: bool = env_bool("COOKIE_SECURE", False)
    cookie_domain: str | None = os.getenv("COOKIE_DOMAIN") or None
    trust_proxy_headers: bool = env_bool("TRUST_PROXY_HEADERS", True)
    enable_api_docs: bool = env_bool("ENABLE_API_DOCS", False)

    max_source_bytes: int = int(os.getenv("MAX_SOURCE_BYTES", str(256 * 1024)))
    max_test_bytes: int = int(os.getenv("MAX_TEST_BYTES", str(1024 * 1024)))
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "240"))
    login_rate_limit_per_minute: int = int(os.getenv("LOGIN_RATE_LIMIT_PER_MINUTE", "15"))
    min_submission_delay_seconds: int = int(os.getenv("MIN_SUBMISSION_DELAY_SECONDS", "3"))

    redis_url: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    judge_queue: str = os.getenv("JUDGE_QUEUE", "judge")

    institute_name: str = os.getenv("INSTITUTE_NAME", "Institute Programming Lab")

    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "005ajeet@gmail.com")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from_email: str = os.getenv("SMTP_FROM_EMAIL", os.getenv("SMTP_USERNAME", "005ajeet@gmail.com"))
    smtp_starttls: bool = env_bool("SMTP_STARTTLS", True)
    otp_expiry_minutes: int = int(os.getenv("OTP_EXPIRY_MINUTES", "10"))
    otp_max_attempts: int = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
