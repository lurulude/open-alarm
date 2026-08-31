from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UserRole(str, Enum):
    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    ENGINEER = "ENGINEER"
    ADMIN = "ADMIN"


ROLE_RANK = {
    UserRole.VIEWER: 0,
    UserRole.OPERATOR: 1,
    UserRole.ENGINEER: 2,
    UserRole.ADMIN: 3,
}

SUPPORTED_USER_LOCALES = frozenset({"en", "fi"})


@dataclass(frozen=True, slots=True)
class AppUser:
    user_id: str
    user_name: str | None
    display_name: str | None
    role: UserRole
    locale: str

    def has_role(self, minimum: UserRole) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]
