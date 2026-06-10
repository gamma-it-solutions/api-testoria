from enum import StrEnum


class UserRole(StrEnum):
    NO_ACCESS = "no_access"
    READ_ONLY = "read_only"
    TESTER = "tester"
    LEAD = "lead"
    ADMIN = "admin"


ROLE_HIERARCHY: dict[UserRole, int] = {
    UserRole.NO_ACCESS: 0,
    UserRole.READ_ONLY: 1,
    UserRole.TESTER: 2,
    UserRole.LEAD: 3,
    UserRole.ADMIN: 4,
}

ROLE_METADATA: dict[UserRole, dict[str, object]] = {
    UserRole.NO_ACCESS: {
        "label": "No Access",
        "is_default": False,
        "is_deletable": True,
        "description": "Blocked at every protected route.",
    },
    UserRole.READ_ONLY: {
        "label": "Read Only",
        "is_default": False,
        "is_deletable": True,
        "description": "GET on all resources; no write operations.",
    },
    UserRole.TESTER: {
        "label": "Tester",
        "is_default": False,
        "is_deletable": True,
        "description": "Create/edit test cases, suites, results.",
    },
    UserRole.LEAD: {
        "label": "Lead",
        "is_default": True,
        "is_deletable": False,
        "description": "Full resource access; manages users (cannot manage Admins).",
    },
    UserRole.ADMIN: {
        "label": "Admin",
        "is_default": False,
        "is_deletable": True,
        "description": "Full access including user management.",
    },
}
