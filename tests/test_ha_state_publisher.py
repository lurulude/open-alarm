import asyncio
import json

from open_alarm.backend.ha import state_publisher
from open_alarm.backend.ha.state_publisher import HomeAssistantAlarmStatePublisher


class _Response:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_publishes_count_and_attention_and_skips_unchanged(monkeypatch) -> None:
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return _Response()

    monkeypatch.setattr(state_publisher, "urlopen", fake_urlopen)
    publisher = HomeAssistantAlarmStatePublisher(
        api_url="http://supervisor/core/api",
        token="token",
        heartbeat_s=60,
    )

    assert asyncio.run(publisher.publish(3)) is True
    assert asyncio.run(publisher.publish(3)) is True
    assert len(calls) == 2

    count_request, timeout = calls[0]
    assert timeout == 5.0
    assert count_request.full_url.endswith("/states/sensor.open_alarm_unacknowledged")
    assert count_request.get_header("Authorization") == "Bearer token"
    count_payload = json.loads(count_request.data)
    assert count_payload["state"] == "3"
    assert count_payload["attributes"]["friendly_name"] == "Open Alarm unacknowledged"

    attention_request, _ = calls[1]
    assert attention_request.full_url.endswith("/states/binary_sensor.open_alarm_attention")
    attention_payload = json.loads(attention_request.data)
    assert attention_payload["state"] == "on"
    assert attention_payload["attributes"]["device_class"] == "problem"
    assert attention_payload["attributes"]["unacknowledged"] == 3


def test_zero_turns_attention_off_and_stop_marks_states_unavailable(monkeypatch) -> None:
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return _Response()

    monkeypatch.setattr(state_publisher, "urlopen", fake_urlopen)
    publisher = HomeAssistantAlarmStatePublisher(token="token")

    assert asyncio.run(publisher.publish(0)) is True
    assert json.loads(calls[1].data)["state"] == "off"

    calls.clear()
    assert asyncio.run(publisher.publish_unavailable()) is True
    assert [json.loads(request.data)["state"] for request in calls] == [
        "unavailable",
        "unavailable",
    ]


def test_missing_supervisor_token_is_best_effort(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    publisher = HomeAssistantAlarmStatePublisher(retry_interval_s=0)
    assert asyncio.run(publisher.publish(1)) is False
