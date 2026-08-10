from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.roles import UserRole
from app.database import Base


class ApiKey(Base):
    """A non-interactive credential for CI pipelines and the CLI.

    Deliberately not a `SoftDeleteMixin` model: `revoked_at` *is* this table's
    soft delete and carries clearer meaning. Revoked rows are never purged —
    they are the record of which credential did what.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Lookup handle. Safe to display; the secret half never leaves the mint call.
    key_prefix: Mapped[str] = mapped_column(
        String(16), unique=True, nullable=False, index=True
    )
    # sha256 hex of the secret. SHA-256 rather than bcrypt because the secret is
    # 256 bits of CSPRNG output — there is no dictionary for a slow KDF to defend
    # against, and bcrypt would cost ~100ms on every CI request.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # NULL = unscoped (every project the owner can reach).
    project_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(50), nullable=False, default=UserRole.TESTER
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
