from collections.abc import Callable
from enum import StrEnum

from fastapi import Depends

from app.core.exceptions import ForbiddenError
from app.core.roles import UserRole
from app.dependencies import get_current_user
from app.models.user import User


class Permission(StrEnum):
    VIEW_PROJECT = "view_project"
    EDIT_PROJECT = "edit_project"
    DELETE_PROJECT = "delete_project"
    CREATE_TEST_CASE = "create_test_case"
    EDIT_TEST_CASE = "edit_test_case"
    DELETE_TEST_CASE = "delete_test_case"
    EXECUTE_TEST = "execute_test"
    VIEW_REPORTS = "view_reports"
    MANAGE_USERS = "manage_users"


ROLE_PERMISSIONS: dict[UserRole, list[Permission]] = {
    UserRole.NO_ACCESS: [],
    UserRole.READ_ONLY: [
        Permission.VIEW_PROJECT,
        Permission.VIEW_REPORTS,
    ],
    UserRole.TESTER: [
        Permission.VIEW_PROJECT,
        Permission.CREATE_TEST_CASE,
        Permission.EDIT_TEST_CASE,
        Permission.EXECUTE_TEST,
        Permission.VIEW_REPORTS,
    ],
    UserRole.LEAD: [
        Permission.VIEW_PROJECT,
        Permission.EDIT_PROJECT,
        Permission.CREATE_TEST_CASE,
        Permission.EDIT_TEST_CASE,
        Permission.DELETE_TEST_CASE,
        Permission.EXECUTE_TEST,
        Permission.VIEW_REPORTS,
    ],
    UserRole.ADMIN: list(Permission),
}


def has_permission(user: User, permission: Permission) -> bool:
    """Check if a user has the given permission based on their role."""
    role = UserRole(user.role)
    return permission in ROLE_PERMISSIONS.get(role, [])


def require_permission(permission: Permission) -> Callable[..., object]:
    """FastAPI dependency that enforces a specific permission."""

    async def checker(current_user: User = Depends(get_current_user)) -> User:
        if not has_permission(current_user, permission):
            raise ForbiddenError()
        return current_user

    return checker
