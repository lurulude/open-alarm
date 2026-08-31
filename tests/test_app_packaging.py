from pathlib import Path


def test_run_script_uses_home_assistant_environment_launcher() -> None:
    run_script = Path("open_alarm/run.sh")
    first_line = run_script.read_text(encoding="utf-8").splitlines()[0]

    assert first_line == "#!/usr/bin/with-contenv bashio"


def test_app_requests_home_assistant_api_access() -> None:
    config = Path("open_alarm/config.yaml").read_text(encoding="utf-8")

    assert "homeassistant_api: true" in config
