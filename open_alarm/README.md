# Open Alarm

**SCADA-style alarm management for Home Assistant.**

Open Alarm adds a dedicated, persistent alarm engine to Home Assistant. Engineer analog, digital and device/quality alarms in one table, then operate them with acknowledgement, hysteresis, delays, shelving, suppression, out-of-service controls, history and notification groups.

## Beta.3 highlights

- Fixes clean-install Ingress API failures caused by redundant Home Assistant administrator verification
- Makes the Open Alarm sidebar panel available to authenticated Home Assistant users
- Keeps Open Alarm's own Viewer / Operator / Engineer / Admin roles authoritative for application actions
- Analog HiHi / Hi / Lo / LoLo alarms with hysteresis and restart-safe ON/OFF delays
- Digital alarms with debounce and ON/OFF delays
- Device/quality alarms for unavailable, unknown, missing, stale and bad-quality sources
- Save → Review changes → Activate engineering workflow
- Searchable Home Assistant entity picker with friendly names, values and units
- Alarm browser, acknowledgement and operator-control history
- Named Home Assistant `notify.*` notification groups with persistent retry/delay handling
- English and Finnish operator UI
- Responsive phone/tablet layout for the Home Assistant frontend
- `sensor.open_alarm_unacknowledged` and `binary_sensor.open_alarm_attention` for Home Assistant dashboards/automations
- Optional always-visible browser `⚠ N` corner indicator
- Release-time Python/npm dependency license audit and packaged third-party notices

Open Alarm runs through Home Assistant Ingress and stores its runtime/configuration data in the App data directory using SQLite/WAL. Beta.3 supports `aarch64` and `amd64` and currently builds the App image from source during installation/update.

On a fresh database, the first authenticated Home Assistant user to open Open Alarm becomes the first Open Alarm Admin. Later users start as Viewer until an Open Alarm Admin assigns another role.

> [!WARNING]
> Open Alarm is Beta software and is not a certified safety system. Do not use it as the sole protective layer for life-safety or equipment-protection functions.

See **Documentation** after installation for configuration, operation, backup, troubleshooting and optional corner-indicator setup.

Open Alarm project source is licensed under **Apache License 2.0**. The App folder includes the full license and `THIRD_PARTY_NOTICES.md`; the release build also keeps actual installed dependency/license inventories under `/app/licenses/` in the built image.
