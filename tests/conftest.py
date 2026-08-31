from __future__ import annotations

import pytest

from open_alarm.backend.ha.users import HomeAssistantAdminAuthorizer


@pytest.fixture(autouse=True)
def synthetic_ingress_admin(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """API tests use a verified HA admin unless they are testing HA authorization itself."""
    if request.node.path.name in {"test_ha_users.py", "test_ingress_admin_verification.py"}:
        return

    async def is_active_admin(self: HomeAssistantAdminAuthorizer, user_id: str) -> bool:
        del self, user_id
        return True

    monkeypatch.setattr(HomeAssistantAdminAuthorizer, "is_active_admin", is_active_admin)
