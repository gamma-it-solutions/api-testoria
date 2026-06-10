from datetime import datetime

from sqlalchemy import ColumnElement, DateTime
from sqlalchemy.orm import Mapped, mapped_column


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


def not_deleted(model: type[SoftDeleteMixin]) -> ColumnElement[bool]:
    return model.deleted_at.is_(None)
