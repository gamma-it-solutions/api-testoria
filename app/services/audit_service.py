from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def log_action(
    db: AsyncSession,
    user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int | None,
    changes: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    """Record an audit log entry.

    Args:
        db: Active database session.
        user_id: ID of the user performing the action, or None for system actions.
        action: Action type (CREATE, UPDATE, DELETE, LOGIN, LOGOUT).
        entity_type: The domain entity being acted on (e.g. "Project", "TestCase").
        entity_id: Primary key of the affected entity, if applicable.
        changes: Optional dict of changed fields for UPDATE actions.
        request: Starlette Request object to extract IP and user-agent from.
    """
    ip_address: str | None = None
    user_agent: str | None = None
    if request is not None:
        if request.client is not None:
            ip_address = request.client.host
        user_agent = request.headers.get("user-agent")

    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        changes=changes,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(entry)
