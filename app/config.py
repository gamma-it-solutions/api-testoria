from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://testoria:testoria@localhost:5432/testoria"
    TEST_DATABASE_URL: str = (
        "postgresql+asyncpg://testoria:testoria@localhost:5432/testoria_test"
    )

    # Security
    SECRET_KEY: str = "change-me-to-a-random-32-char-string"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    # API keys — non-interactive credentials for CI pipelines and the CLI.
    # The effective role of a key is min(key.role, owner.role, API_KEY_MAX_ROLE),
    # so capping at `tester` here is what keeps every require_role(LEAD, ADMIN)
    # route closed to API keys without a per-route allowlist.
    API_KEY_MAX_ROLE: str = "tester"
    API_KEY_DEFAULT_TTL_DAYS: int = 90
    # `last_used_at` is an operator convenience, not an audit record — throttled
    # so a busy CI key does not cost one UPDATE per request.
    API_KEY_LAST_USED_THROTTLE_SECONDS: int = 60

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # File storage — legacy disk path (used only by storage_backend='local' rows)
    UPLOAD_DIR: str = "uploads"

    # Object storage (MinIO / S3)
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    # Used only for signing URLs returned to browsers; falls back to S3_ENDPOINT_URL.
    # In prod, S3_ENDPOINT_URL points at the internal docker hostname (http://minio:9000)
    # and S3_PUBLIC_ENDPOINT_URL at the public hostname proxied to MinIO.
    S3_PUBLIC_ENDPOINT_URL: str | None = None
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET_ATTACHMENTS: str = "testoria-attachments"
    S3_REGION: str = "us-east-1"
    S3_PRESIGN_TTL_SECONDS: int = 900  # 15 minutes — for live API responses
    S3_REPORT_PRESIGN_TTL_SECONDS: int = 604800  # 7 days — embedded in exported reports

    # Attachment constraints
    MAX_ATTACHMENT_BYTES: int = 10 * 1024 * 1024  # 10 MB per file
    MAX_ATTACHMENTS_PER_BULK: int = 10
    REPORT_ATTACHMENT_MAX_PER_RESULT: int = 3

    # Pagination
    DEFAULT_PAGE_SIZE: int = 50
    MAX_PAGE_SIZE: int = 100

    # Initial admin seed (used by scripts/seed.py only)
    ADMIN_USERNAME: str = "admin"
    ADMIN_EMAIL: str = "admin@testoria.local"
    ADMIN_PASSWORD: str | None = None

    # Centrifugo
    CENTRIFUGO_URL: str = "http://localhost:8001"
    CENTRIFUGO_API_KEY: str = "centrifugo-api-key"
    CENTRIFUGO_TOKEN_SECRET: str = "centrifugo-token-secret"

    # Email — Gmail SMTP + App Password via aiosmtplib (STARTTLS).
    # EMAIL_ENABLED=False (the default) makes the sender a logged no-op: outbox
    # rows still queue and drain so the path is exercised without hitting Gmail.
    EMAIL_ENABLED: bool = False
    EMAIL_SMTP_HOST: str = "smtp.gmail.com"
    EMAIL_SMTP_PORT: int = 587
    EMAIL_SMTP_USER: str = ""
    EMAIL_SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = "Testoria <no-reply@testoria.local>"

    # Public base URL of the frontend — used to build set-password / reset links.
    FRONTEND_BASE_URL: str = "http://localhost:5173"

    # Single-use password token TTLs (Redis-backed).
    EMAIL_INVITE_TOKEN_TTL_SECONDS: int = 86400  # 24h — welcome set-password invite
    EMAIL_RESET_TOKEN_TTL_SECONDS: int = 3600  # 1h — forgot-password reset

    # Outbox drain worker tuning.
    EMAIL_OUTBOX_POLL_SECONDS: int = 10
    EMAIL_OUTBOX_BATCH_SIZE: int = 50
    EMAIL_SEND_PACE_MS: int = 200  # delay between sends on one connection
    EMAIL_MAX_ATTEMPTS: int = 5

    # Debug
    DEBUG: bool = False


settings = Settings()
