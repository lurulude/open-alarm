# Open Alarm

**SCADA-style alarm management for Home Assistant.**

Open Alarm adds a dedicated, persistent alarm engine to Home Assistant. Engineer analog, digital and device/quality alarms in one table, then operate them with acknowledgement, hysteresis, delays, shelving, suppression, out-of-service controls, history and notification groups.

## Beta.1 highlights

- Analog HiHi / Hi / Lo / LoLo alarms with hysteresis and restart-safe ON/OFF delays
- Digital alarms with debounce and ON/OFF delays
- Device/quality alarms for unavailable, unknown, missing, stale and bad-quality sources
- Save → Review changes → Activate engineering workflow
- Searchable Home Assistant entity picker with friendly names, values and units
- Alarm browser, acknowledgement and operator-control history
- Named Home Assistant `notify.*` notification groups with persistent retry/delay handling
- English and Finnish operator UI
- `sensor.open_alarm_unacknowledged` and `binary_sensor.open_alarm_attention` for Home Assistant dashboards/automations
- Optional always-visible `⚠ N` Home Assistant corner indicator

Open Alarm runs through Home Assistant Ingress and stores its runtime/configuration data in the App data directory using SQLite/WAL. Beta.1 supports `aarch64` and `amd64` and currently builds the App image from source during installation.

> [!WARNING]
> Open Alarm is Beta software and is not a certified safety system. Do not use it as the sole protective layer for life-safety or equipment-protection functions.

See **Documentation** after installation for configuration, operation, backup, troubleshooting and optional corner-indicator setup.

Open Alarm is licensed under the **Apache License 2.0**; the complete license is included in the repository root.
