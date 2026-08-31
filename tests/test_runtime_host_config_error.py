import asyncio
from pathlib import Path

import pytest

from open_alarm.backend.db.database import apply_migrations, connect
from open_alarm.backend.domain.models import AlarmLifecycle
from open_alarm.backend.runtime.host import RuntimeHost
from open_alarm.backend.runtime.system_alarms import RUNTIME_CONFIG_ALARM_ID


async def _discard_notification(_item: object) -> None:
    return None


def test_runtime_config_load_failure_keeps_host_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = connect(tmp_path / "config-error.db")
    apply_migrations(connection)
    host = RuntimeHost(connection, notification_dispatcher=_discard_notification)

    def fail_load(_connection: object) -> object:
        raise ValueError("broken active revision")

    monkeypatch.setattr(
        "open_alarm.backend.runtime.host.load_active_compiled_config",
        fail_load,
    )
    asyncio.run(host.reload())

    status = host.status_payload()
    assert status["configured"] is False
    assert status["running"] is False
    assert status["reason"] == "Active configuration could not be loaded"
    assert status["config_error"] == "broken active revision"
    assert host.system_alarms.alarm_state(RUNTIME_CONFIG_ALARM_ID).lifecycle == (
        AlarmLifecycle.ACTIVE_UNACK
    )

    monkeypatch.setattr(
        "open_alarm.backend.runtime.host.load_active_compiled_config",
        lambda _connection: None,
    )
    asyncio.run(host.reload())

    recovered = host.status_payload()
    assert recovered["reason"] == "No active configuration revision"
    assert recovered["config_error"] is None
    assert host.system_alarms.alarm_state(RUNTIME_CONFIG_ALARM_ID).lifecycle == AlarmLifecycle.NORMAL

    runtime_events = connection.execute(
        "SELECT event_type FROM runtime_event ORDER BY event_id"
    ).fetchall()
    assert ("CONFIG_LOAD_FAILED",) in runtime_events
    connection.close()
