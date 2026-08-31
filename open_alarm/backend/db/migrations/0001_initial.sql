CREATE TABLE IF NOT EXISTS config_revision (
    revision_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    imported_at_utc TEXT NOT NULL,
    revision_hash TEXT NOT NULL UNIQUE,
    compiled_hash TEXT NOT NULL,
    engineering_source_hash TEXT,
    source_name TEXT,
    active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_config_revision_active
ON config_revision(active) WHERE active = 1;

CREATE TABLE IF NOT EXISTS tag_config (
    revision_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    value_type TEXT NOT NULL DEFAULT 'auto',
    stale_after_s REAL CHECK(stale_after_s IS NULL OR stale_after_s >= 0),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    config_json TEXT NOT NULL,
    PRIMARY KEY (revision_id, tag_id),
    FOREIGN KEY (revision_id) REFERENCES config_revision(revision_id)
);

CREATE INDEX IF NOT EXISTS ix_tag_config_entity_id ON tag_config(entity_id);

CREATE TABLE IF NOT EXISTS notification_policy_config (
    revision_id TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    route_key TEXT NOT NULL,
    notify_on_active INTEGER NOT NULL DEFAULT 1 CHECK(notify_on_active IN (0,1)),
    notify_on_return INTEGER NOT NULL DEFAULT 0 CHECK(notify_on_return IN (0,1)),
    notify_on_ack INTEGER NOT NULL DEFAULT 0 CHECK(notify_on_ack IN (0,1)),
    notify_delay_s REAL NOT NULL DEFAULT 0 CHECK(notify_delay_s >= 0),
    notification_channel TEXT,
    notification_group TEXT,
    critical INTEGER NOT NULL DEFAULT 0 CHECK(critical IN (0,1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    config_json TEXT NOT NULL,
    PRIMARY KEY (revision_id, policy_id),
    FOREIGN KEY (revision_id) REFERENCES config_revision(revision_id)
);

CREATE INDEX IF NOT EXISTS ix_notification_policy_route
ON notification_policy_config(route_key);

CREATE TABLE IF NOT EXISTS alarm_config (
    revision_id TEXT NOT NULL,
    alarm_id TEXT NOT NULL,
    source_tag_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('ANALOG','DIGITAL','DEVICE')),
    condition_json TEXT NOT NULL,
    hysteresis REAL,
    debounce_on_s REAL NOT NULL DEFAULT 0 CHECK(debounce_on_s >= 0),
    debounce_off_s REAL NOT NULL DEFAULT 0 CHECK(debounce_off_s >= 0),
    on_delay_s REAL NOT NULL DEFAULT 0 CHECK(on_delay_s >= 0),
    off_delay_s REAL NOT NULL DEFAULT 0 CHECK(off_delay_s >= 0),
    priority TEXT NOT NULL,
    category TEXT NOT NULL,
    config_json TEXT NOT NULL,
    alarm_group_id TEXT,
    message TEXT NOT NULL DEFAULT '',
    rtn_ack_required INTEGER NOT NULL DEFAULT 0 CHECK(rtn_ack_required IN (0,1)),
    latching INTEGER NOT NULL DEFAULT 0 CHECK(latching IN (0,1)),
    inhibit_by_json TEXT NOT NULL DEFAULT '[]',
    notification_policy_id TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    PRIMARY KEY (revision_id, alarm_id),
    FOREIGN KEY (revision_id) REFERENCES config_revision(revision_id)
);

CREATE TABLE IF NOT EXISTS alarm_state (
    alarm_id TEXT PRIMARY KEY,
    revision_id TEXT,
    origin TEXT NOT NULL DEFAULT 'ENGINEERING' CHECK(origin IN ('ENGINEERING','SYSTEM')),
    lifecycle TEXT NOT NULL CHECK(lifecycle IN
        ('NORMAL','PENDING_ON','ACTIVE_UNACK','ACTIVE_ACK','PENDING_OFF','RTN_UNACK')),
    condition_abnormal INTEGER NOT NULL DEFAULT 0 CHECK(condition_abnormal IN (0,1)),
    raw_value_json TEXT,
    qualified_value_json TEXT,
    pending_transition TEXT,
    pending_started_at_utc TEXT,
    pending_deadline_utc TEXT,
    pending_origin TEXT,
    active_since_utc TEXT,
    ack_user_id TEXT,
    ack_at_utc TEXT,
    returned_at_utc TEXT,
    latched INTEGER NOT NULL DEFAULT 0 CHECK(latched IN (0,1)),
    shelved_until_utc TEXT,
    suppressed INTEGER NOT NULL DEFAULT 0 CHECK(suppressed IN (0,1)),
    inhibited INTEGER NOT NULL DEFAULT 0 CHECK(inhibited IN (0,1)),
    inhibited_by_json TEXT,
    out_of_service INTEGER NOT NULL DEFAULT 0 CHECK(out_of_service IN (0,1)),
    updated_at_utc TEXT NOT NULL,
    debounce_pending_target INTEGER CHECK(
        debounce_pending_target IS NULL OR debounce_pending_target IN (0,1)
    ),
    debounce_pending_started_at_utc TEXT,
    debounce_pending_deadline_utc TEXT,
    FOREIGN KEY (revision_id, alarm_id) REFERENCES alarm_config(revision_id, alarm_id)
);

CREATE INDEX IF NOT EXISTS ix_alarm_state_lifecycle ON alarm_state(lifecycle);
CREATE INDEX IF NOT EXISTS ix_alarm_state_pending_deadline ON alarm_state(pending_deadline_utc);
CREATE INDEX IF NOT EXISTS ix_alarm_state_active_since ON alarm_state(active_since_utc);
CREATE INDEX IF NOT EXISTS ix_alarm_state_origin ON alarm_state(origin);
CREATE INDEX IF NOT EXISTS ix_alarm_state_inhibited ON alarm_state(inhibited);

CREATE TABLE IF NOT EXISTS alarm_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    alarm_id TEXT NOT NULL,
    revision_id TEXT,
    origin TEXT NOT NULL DEFAULT 'ENGINEERING' CHECK(origin IN ('ENGINEERING','SYSTEM')),
    event_type TEXT NOT NULL,
    event_at_utc TEXT NOT NULL,
    user_id TEXT,
    value_json TEXT,
    message TEXT,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS ix_alarm_event_alarm_time ON alarm_event(alarm_id, event_at_utc);
CREATE INDEX IF NOT EXISTS ix_alarm_event_type_time ON alarm_event(event_type, event_at_utc);
CREATE INDEX IF NOT EXISTS ix_alarm_event_origin_time ON alarm_event(origin, event_at_utc);

CREATE TABLE IF NOT EXISTS engineering_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id TEXT,
    action TEXT NOT NULL,
    object_type TEXT,
    object_id TEXT,
    user_id TEXT,
    at_utc TEXT NOT NULL,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS runtime_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    event_at_utc TEXT NOT NULL,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS ix_runtime_event_type_time ON runtime_event(event_type, event_at_utc);

CREATE TABLE IF NOT EXISTS app_user (
    user_id TEXT PRIMARY KEY,
    user_name TEXT,
    display_name TEXT,
    role TEXT NOT NULL CHECK(role IN ('VIEWER','OPERATOR','ENGINEER','ADMIN')),
    locale TEXT NOT NULL DEFAULT 'en' CHECK(locale IN ('en','fi')),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_app_user_role ON app_user(role);

CREATE TABLE IF NOT EXISTS operator_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    actor_user_id TEXT,
    target_user_id TEXT,
    at_utc TEXT NOT NULL,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS ix_operator_audit_target_time
ON operator_audit(target_user_id, at_utc);

CREATE TABLE IF NOT EXISTS engineering_draft (
    draft_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_revision_id TEXT,
    created_by TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY (base_revision_id) REFERENCES config_revision(revision_id)
);

CREATE INDEX IF NOT EXISTS ix_engineering_draft_updated
ON engineering_draft(updated_at_utc);

CREATE TABLE IF NOT EXISTS engineering_object (
    draft_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    row_order INTEGER NOT NULL DEFAULT 0,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY (draft_id, object_type, object_id),
    FOREIGN KEY (draft_id) REFERENCES engineering_draft(draft_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_engineering_object_type
ON engineering_object(draft_id, object_type, row_order, object_id);

CREATE TABLE IF NOT EXISTS config_source_object (
    revision_id TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    row_order INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (revision_id, object_type, object_id),
    FOREIGN KEY (revision_id) REFERENCES config_revision(revision_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_config_source_object_type
ON config_source_object(revision_id, object_type, row_order, object_id);

CREATE TABLE IF NOT EXISTS notification_outbox (
    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    alarm_id TEXT NOT NULL,
    revision_id TEXT,
    origin TEXT NOT NULL DEFAULT 'ENGINEERING' CHECK(origin IN ('ENGINEERING','SYSTEM')),
    event_type TEXT NOT NULL,
    route_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING','PROCESSING','SENT','FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    available_at_utc TEXT NOT NULL,
    locked_at_utc TEXT,
    sent_at_utc TEXT,
    last_error TEXT,
    created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_notification_outbox_ready
ON notification_outbox(status, available_at_utc, outbox_id);

CREATE INDEX IF NOT EXISTS ix_notification_outbox_alarm
ON notification_outbox(alarm_id, event_type, created_at_utc);
