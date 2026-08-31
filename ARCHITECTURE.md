# Open Alarm Architecture

Open Alarm is a Home Assistant OS App built as a small, single-process alarm-management system:

- **Python 3.13 / FastAPI** backend;
- **React** frontend built into static assets;
- **SQLite/WAL** persistence in `/data/open_alarm.db`;
- Home Assistant **Ingress** for UI/API access;
- Home Assistant Supervisor/Core **WebSocket + REST API** integration;
- one runtime alarm engine monitoring only configured Home Assistant sources.

The architecture is deliberately conservative for Beta: one App, one database, one active revision and no distributed services.

## Design goals

Open Alarm is designed around a few non-negotiable behaviors:

1. Alarm lifecycle must be explicit and deterministic.
2. ON/OFF/debounce timing must survive App restarts without restarting timers from zero.
3. Bad source quality must never falsely clear an existing process alarm.
4. Engineering changes must be validated, immutable once reviewed, and activated transactionally.
5. Alarm state/history and notification enqueueing must be persisted before external delivery.
6. Operator-facing UI must not depend on generated internal IDs for meaning.
7. Home Assistant integration should be scoped to configured entities rather than repeatedly downloading all HA state.

Everything else is kept as simple as the Beta requirements allow.

## Process model

`open_alarm/run.sh` starts a single Uvicorn process on port `8099`:

```text
Home Assistant Ingress
        │
        ▼
  FastAPI / React
        │
        ├──────────────► SQLite /data/open_alarm.db
        │
        ├──────────────► Home Assistant Core WebSocket
        │                 configured source states
        │                 entity/device picker metadata
        │                 notify.send_message service calls
        │
        └──────────────► Home Assistant Core REST
                          published attention/count states
```

There is no background service outside the App container and no separate database server.

## Startup and shutdown

FastAPI lifespan startup performs the runtime boot sequence:

1. resolve `/data/open_alarm.db`;
2. open SQLite and apply schema migrations;
3. run database integrity checks;
4. construct `RuntimeHost`;
5. start notification worker and notification-action event listener;
6. start the periodic health/system-alarm loop;
7. load the active immutable configuration revision, if one exists;
8. construct a runtime controller and begin monitored Home Assistant subscriptions.

A fresh database with no active configuration is valid. The App remains healthy while runtime reports that no active revision is configured.

On normal shutdown, the runtime controller, notification workers and health task stop, the Home Assistant attention states are marked unavailable, SQLite WAL is checkpointed and the database connection closes.

## Configuration model

### User-facing model

Engineering intentionally exposes one main alarm table:

`EDIT → SAVE → REVIEW → ACTIVATE`

Each row represents one Home Assistant source. The user sees a numeric **Alarm ID** (`1`, `2`, `3`, …) that is assigned automatically.

Internal generated identities are deterministic:

- engineering row `1` source tag → `T1`;
- analog HiHi → `A1_HIHI`;
- analog Hi → `A1_HI`;
- analog Lo → `A1_LO`;
- analog LoLo → `A1_LOLO`;
- digital → `A1_DIGITAL`;
- device/quality → `A1_DEVICE`.

Those IDs are stable runtime/configuration identities. Operator views resolve them to friendly Home Assistant source names, custom messages and localized condition labels.

### Atomic draft save

`engineering/alarm_table.py` maps the user-facing table into internal `TAG`, `ALARM` and `NOTIFICATION_POLICY` draft objects.

A table save:

- validates duplicate IDs and row-level constraints;
- expands configured analog limits into separate runtime alarms;
- validates notification-group references;
- acquires `BEGIN IMMEDIATE`;
- verifies the draft's optimistic `updated_at` token;
- replaces the draft's generated objects;
- updates the draft timestamp;
- commits as one transaction.

A concurrent/stale save therefore fails instead of silently overwriting another editor's changes.

### Review / internal compiler

Review converts the saved draft into strongly typed configuration definitions, runs the internal compiler and creates or reuses an immutable revision.

The compiler validates, among other things:

- entity-ID syntax;
- unique object IDs and references;
- supported alarm kind/condition combinations;
- finite non-negative timing/hysteresis values;
- notification-policy references;
- inhibition graph validity;
- analog/digital/device-specific rules.

Review does **not** perform a full Home Assistant `get_states` existence check. It validates entity-ID syntax. This prevents large Home Assistant installations from making Review depend on downloading an unbounded state snapshot. Runtime quality handling is responsible for missing/disappearing entities.

The compiler is an internal transformation, not a separate user-visible “Compile” step.

### Immutable revisions and activation

A successfully reviewed candidate revision is immutable and identified by its compiled content/source hash. Activation changes the active revision and reloads runtime monitoring.

Configuration activation is intentionally not blocked by currently active engineered alarms. For each previous runtime alarm state:

- compatible same-ID definitions can migrate live state to the new revision;
- incompatible same-ID definitions are reset and reevaluated;
- removed engineered alarms are retired/reset from current runtime state;
- historical alarm events are not deleted.

This allows threshold/engineering maintenance while process alarms are active.

## Alarm configuration types

### Analog

Analog rows can contain any subset of:

- HiHi → `HIGH_HIGH`;
- Hi → `HIGH`;
- Lo → `LOW`;
- LoLo → `LOW_LOW`.

At least one limit is required. When multiple limits are configured they must descend in process-value order:

`HiHi > Hi > Lo > LoLo`

Analog alarms use hysteresis and ON/OFF delays. Binary debounce is rejected for analog alarms.

### Digital

Digital alarms support:

- `EQUALS`;
- `NOT_EQUALS`.

They require an `alarm_value`. Digital alarms can use raw-state debounce plus alarm ON/OFF delays. Hysteresis is not used.

### Device / quality

Device alarms support:

- `UNAVAILABLE`;
- `UNKNOWN`;
- `MISSING`;
- `STALE`;
- `BAD_QUALITY`.

They use ON/OFF delays and do not use digital debounce.

## Runtime monitoring

### Scoped Home Assistant subscription

The active compiled configuration contains the source tags. `RuntimeController` asks `HomeAssistantWebSocketClient.stream_states()` to monitor only those entity IDs.

The preferred path is Home Assistant's filtered `subscribe_entities` WebSocket subscription. It provides an initial compact snapshot for the requested IDs and then incremental changes.

A compatibility fallback can subscribe to `state_changed` events. That fallback currently performs one `get_states` bootstrap and filters the configured entities locally. The primary runtime path is intentionally scoped to avoid large unbounded Home Assistant snapshots.

### Entity normalization

Incoming Home Assistant states are normalized into internal source state including:

- raw state/value;
- attributes;
- observation/change/update timestamps;
- quality/availability interpretation.

Runtime also captures display metadata such as `friendly_name` and `unit_of_measurement` when available. Alarm-relevant persisted state stores the last-known display metadata so the 1-second browser refresh path does not need to query Home Assistant repeatedly.

## Alarm lifecycle engine

Primary lifecycle states are:

```text
NORMAL
  │ abnormal
  ▼
PENDING_ON ───────────────┐
  │ ON deadline           │ condition reverses
  ▼                       │
ACTIVE_UNACK ◄────────────┘
  │ acknowledge
  ▼
ACTIVE_ACK
  │ return condition
  ▼
PENDING_OFF
  │ OFF deadline
  ├──────────────► NORMAL
  └──────────────► RTN_UNACK   (when return acknowledgement is required)
```

The exact transitions are implemented in `backend/domain/engine.py`; the browser/UI does not invent lifecycle state independently.

Control dimensions such as shelving, suppression, inhibition, out-of-service and latching are stored separately from the lifecycle enum.

## Analog qualification and hysteresis

For high alarms:

- activate condition: `value >= setpoint`;
- clear qualification only when `value < setpoint - hysteresis`.

For low alarms:

- activate condition: `value <= setpoint`;
- clear qualification only when `value > setpoint + hysteresis`.

The hysteresis band prevents chatter around the configured threshold.

ON/OFF delays require continuous qualification. If the source reverses before a pending deadline, the pending transition is cancelled.

## Digital debounce

Digital timing is separated into two layers:

1. raw state debounce (`debounce_on_s`, `debounce_off_s`);
2. alarm lifecycle delay (`on_delay_s`, `off_delay_s`).

Each layer requires continuous qualification. A reversal cancels the current pending interval rather than accumulating time across interruptions.

## Restart-safe deadlines

Alarm-relevant runtime state includes pending timestamps/deadlines. Digital debounce state is persisted when needed as well.

After restart:

1. persisted runtime state is reloaded;
2. Home Assistant supplies the current source state;
3. the engine compares the current qualification with the persisted pending state/deadline;
4. the transition continues, completes immediately if its original deadline has passed, or cancels if the source no longer qualifies.

The timer is not blindly restarted from zero.

Normal alarms with no alarm-relevant persisted state can remain memory-only to reduce write volume.

## Quality and failure behavior

Quality is explicit rather than treated as a normal process value.

Bad-quality conditions include:

- Home Assistant `unavailable`;
- `unknown`;
- missing entity/source;
- configured stale timeout exceeded;
- Home Assistant connection loss.

A bad-quality update does **not** clear an existing process alarm. The current process lifecycle is retained while a device/quality alarm or built-in system alarm represents the fault.

## Built-in system alarms

`runtime/system_alarms.py` defines non-engineer-editable alarms for failures in the alarm-management path:

| Alarm | Priority | Purpose |
| --- | --- | --- |
| `SYS_HA_CONNECTION_LOST` | P1 | Home Assistant runtime connection lost; short ON delay filters transient disconnects |
| `SYS_RUNTIME_CONFIG_ERROR` | P1 | active configuration cannot be loaded |
| `SYS_NOTIFICATION_WORKER_STOPPED` | P1 | persistent notification worker is not running |
| `SYS_NOTIFICATION_DELIVERY_FAILED` | P2 | notification delivery has failed |

System alarms use the same persisted lifecycle/history mechanisms but cannot be shelved, suppressed or placed out of service.

## Persistence

SQLite is the only database. The active path is `/data/open_alarm.db` and the connection uses WAL mode.

Persistent data includes:

- schema migration versions;
- engineering drafts and generated objects;
- immutable configuration revisions;
- active-revision pointer;
- alarm-relevant runtime state;
- pending alarm/debounce deadlines;
- source display metadata for current alarm state;
- shelving, suppression and out-of-service control state;
- alarm event history;
- operator/configuration audit;
- Open Alarm users, roles and locales;
- notification outbox/retry state;
- runtime start/stop/connectivity events.

New history events can store source friendly-name/unit metadata in event details so historical display does not depend on current Home Assistant metadata.

Logical multi-row changes use SQLite transactions. WAL is checkpointed on clean shutdown. The Home Assistant App declares a **cold backup** so normal App backup capture occurs with the App stopped.

Beta schema migrations are automatic at startup. Beta releases may still change the schema; migration/recovery notes belong in the changelog for releases that require operator attention.

## Alarm browser and canonical counts

The alarm browser has canonical SQL semantics for:

- active;
- unacknowledged;
- returned-unacknowledged;
- shelved;
- inhibited;
- suppressed;
- out-of-service.

The **unacknowledged** predicate includes:

- `ACTIVE_UNACK`;
- `RTN_UNACK`;
- `PENDING_OFF` that originated from `ACTIVE_UNACK`;

and excludes alarms that are shelved, suppressed, inhibited or out of service.

This same count is reused for Home Assistant attention-state publishing and notification “other unacknowledged alarms” context. There is intentionally one definition rather than separate UI/notification/HA-state interpretations.

## Notifications

### Persistent outbox

Alarm transition handling can enqueue a notification in the same logical database operation as alarm state/history persistence. External delivery is performed later by `NotificationOutboxWorker`.

This avoids a state where Home Assistant receives a notification for a transition that the local database failed to commit.

The worker supports delayed sends and retries. Before delayed activation delivery, routing logic revalidates the current alarm/control state so returned, hidden or superseded alarms do not send stale activation messages.

### Notification groups

The engineering UI creates named notification policies/groups with:

- display name;
- operator-facing notification title;
- one or more `notify.*` entity targets;
- optional delay;
- locale captured from the engineer's Open Alarm locale at save time.

For grouped delivery the route is Home Assistant `notify.send_message` and `HomeAssistantNotificationDispatcher` intentionally emits only `title` and `message` plus the target notify entities.

That means the current Beta group path does **not** forward mobile-app-specific `data` such as deep-link URLs or actionable ACK buttons. The backend still contains generic notification/action plumbing, but the supported engineering group transport must not be documented as if those integration-specific actions are delivered.

### Operator text

Notification rendering prefers:

1. configured operator Message;
2. Home Assistant `friendly_name`;
3. entity ID fallback;
4. generic localized alarm label.

The generated analog condition is appended as localized operator text. Current value/unit or digital state presentation is included where applicable. Extra alarm context is limited to other unacknowledged operational alarms.

## Home Assistant attention-state publisher

`backend/ha/state_publisher.py` publishes:

- `sensor.open_alarm_unacknowledged`;
- `binary_sensor.open_alarm_attention`.

It uses the Supervisor/Core REST proxy with the App's existing `SUPERVISOR_TOKEN` and `homeassistant_api` permission.

The publisher:

- posts immediately when the unacknowledged count changes;
- heartbeats unchanged state periodically;
- rate-limits retries after delivery failure;
- marks both states `unavailable` during a clean App shutdown.

The publisher is deliberately advisory: failure to publish these convenience states does not stop the alarm engine or make the App unhealthy.

## Optional corner indicator

`open_alarm/open_alarm_indicator.js` is not part of the App's React/Ingress DOM. It is a separate optional Home Assistant frontend module loaded by `frontend.extra_module_url` when the user chooses to install it under `/config/www`.

The module reads the two Home Assistant attention states and injects a fixed top-right button into the Home Assistant page:

- hidden at zero;
- red `⚠ N` when attention is required;
- amber `⚠ ?` when the published state is unavailable;
- click navigates to the Open Alarm Ingress panel.

The App intentionally does not request `/config` write permission to auto-install the module. This keeps a frontend convenience feature outside the core alarm engine's privilege boundary.

## Authentication and authorization

The App UI/API is Ingress-only except `/healthz`.

For requests that require a user:

1. Home Assistant authenticates the Ingress session;
2. Supervisor injects the authenticated user's `X-Remote-User-Id`, username and display-name headers;
3. Open Alarm accepts those headers only behind the production Ingress-source gate;
4. the first authenticated ingress user on a fresh database bootstraps the first Open Alarm Admin;
5. later users start with least privilege as Viewer;
6. API endpoints enforce Open Alarm roles.

Open Alarm deliberately does not perform a second per-request `config/auth/list` lookup. That command requires broader Home Assistant administrator authorization than the App needs for its normal Core API integration and caused valid Ingress requests to fail on clean installations.

Roles are ordered:

`Viewer < Operator < Engineer < Admin`

Examples:

- Viewer: alarm/history read;
- Operator: acknowledge/reset/shelve;
- Engineer: Engineering, suppress, out-of-service;
- Admin: activation and role administration.

The packaged Home Assistant panel uses `panel_admin: false`, so authenticated Home Assistant users can open it; Open Alarm roles determine what they can do inside the App.

Operator/configuration actions store the authenticated user ID and history presentation resolves known IDs to Open Alarm display names.

## Ingress and watchdog boundary

Normal frontend/API routes are served through Home Assistant Ingress. When ingress-source enforcement is enabled by the App environment, direct requests are rejected unless they come from the expected Ingress proxy.

`/healthz` is intentionally exempt so the Supervisor watchdog can perform a minimal process/database liveness probe on the App network.

The richer `/api/health` endpoint is part of the normal API surface and reports database/runtime status.

## Localization

Language-neutral data is stored for lifecycle states, event types, priorities, quality codes and generated IDs.

English and Finnish catalogs live under `backend/i18n/locales/`. English is the fallback and tests enforce translation-key parity.

Home Assistant friendly names are user/integration data and are displayed as supplied; Open Alarm does not translate entity names.

## Frontend

The React frontend is built in the Docker build stage and copied into `/app/frontend_dist`. FastAPI serves it as static content after API routers are registered.

The frontend polls/reads Open Alarm API data; it does not contain an independent alarm state machine. Lifecycle/control semantics remain backend-owned.

Operator presentation rules hide generated engineering alarm IDs as primary labels while retaining them for search/tooltips/troubleshooting.

## Code layout

```text
open_alarm/
├── backend/
│   ├── api/              HTTP API, role dependencies and request models
│   ├── auth/             Open Alarm users, Ingress identity and roles
│   ├── config/           typed config model and internal compiler
│   ├── db/               SQLite schema, queries, activation and persistence
│   ├── domain/           alarm lifecycle/evaluation rules
│   ├── engineering/      one-table draft mapping and revision preparation
│   ├── ha/               Home Assistant WS/REST clients and state publisher
│   ├── i18n/             English/Finnish translation catalogs
│   ├── notifications/    routing, outbox, delivery and HA notify adapter
│   └── runtime/          runtime controller, dispatcher and system alarms
├── frontend/             React source/build configuration
├── open_alarm_indicator.js
├── config.yaml           Home Assistant App manifest
├── Dockerfile
├── run.sh
├── DOCS.md
└── CHANGELOG.md
```

Repository root:

```text
README.md          product/release overview
ARCHITECTURE.md    this technical design
CONTRIBUTING.md    contributor workflow
SECURITY.md        vulnerability policy
LICENSE            Apache-2.0
repository.yaml    Home Assistant App repository metadata
```

## Testing and release gate

The normal CI gate is intentionally small but real:

### Backend

- install runtime/test dependencies;
- Ruff over backend + tests;
- full pytest suite.

### Frontend

- npm install;
- production React build;
- `node --check` on the optional corner-indicator module.

### Packaged App smoke

- Docker-build the real Home Assistant App image;
- verify expected frontend content is packaged;
- boot the image with a persistent `/data` volume;
- verify health/database startup;
- stop/remove/restart the container with the same data;
- verify schema migrations exist and SQLite integrity remains `ok`.

Cross-architecture image publication is intentionally not part of every Beta CI run because the repository currently builds the App locally on the target Home Assistant machine.

## Current architectural limitations

The following are deliberate Beta trade-offs rather than hidden capabilities:

- one SQLite database / one App process;
- no clustering or external database mode;
- source-built App installation instead of prebuilt GHCR images;
- first authenticated user on a fresh database performs the Open Alarm Admin bootstrap;
- generic notification groups are title/message only;
- optional global corner overlay depends on Home Assistant frontend DOM/navigation behavior;
- runtime compatibility fallback can still use one full `get_states` bootstrap if filtered `subscribe_entities` is unavailable;
- no promise of schema compatibility across all future Beta versions.

These constraints keep the Beta implementation understandable and testable while preserving the alarm-management safety fundamentals.
