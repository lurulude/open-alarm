export type UserRole = "VIEWER" | "OPERATOR" | "ENGINEER" | "ADMIN";

export type AppUser = { user_id: string; user_name: string | null; display_name: string | null; role: UserRole; locale: "en" | "fi"; };
export type Session = AppUser;
export type RuntimeStatus = { configured: boolean; running: boolean; connected: boolean; reason?: string | null; active_revision_id?: string; monitored_entities: number; subscription_mode?: string | null; };
export type AlarmRow = {
  alarm_id: string; origin: "ENGINEERING" | "SYSTEM"; lifecycle: string; condition_abnormal: boolean;
  priority: string | null; category: string | null; message: string | null; message_fi: string | null; message_key: string | null;
  kind: string | null; condition: string | null;
  source_tag_id: string | null; source_entity_id: string | null; source_friendly_name: string | null; source_unit: string | null;
  raw_value: unknown; active_since: string | null; ack_at: string | null;
  ack_user_id: string | null; returned_at: string | null; pending_deadline: string | null; latched: boolean;
  shelved_until: string | null; suppressed: boolean; inhibited: boolean; inhibited_by: string[];
  out_of_service: boolean;
};
export type AlarmEvent = {
  event_id: number; alarm_id: string; origin: string; event_type: string; event_at: string; user_id: string | null;
  user_display_name: string | null; value: unknown; message: string | null; message_fi: string | null; message_key: string | null;
  kind: string | null; condition: string | null; source_entity_id: string | null;
  source_friendly_name: string | null; source_unit: string | null;
};
export type TranslationBundle = { locale: string; language_tag: string; messages: Record<string, string>; };
export type EngineeringDraft = { draft_id: string; name: string; base_revision_id: string | null; created_by: string | null; created_at: string; updated_at: string; object_count: number; };
export type HAEntityOption = {
  entity_id: string;
  name: string | null;
  platform: string | null;
  device_name: string | null;
  manufacturer: string | null;
  model: string | null;
};
export type HAEntityPreview = {
  entity_id: string;
  state: string | null;
  friendly_name: string | null;
  unit: string | null;
  device_class: string | null;
};
export type NotificationGroupEngineeringRow = {
  group_id: number;
  name: string;
  title: string;
  target_entity_ids: string[];
  notify_delay_s: number;
  enabled: boolean;
  row_order: number;
};
export type AlarmEngineeringRow = {
  alarm_id: number;
  entity_id: string;
  kind: "ANALOG" | "DIGITAL" | "DEVICE";
  condition: string;
  hihi: number | null;
  hi: number | null;
  lo: number | null;
  lolo: number | null;
  alarm_value: string | null;
  priority: string;
  category: string;
  hysteresis: number;
  debounce_on_s: number;
  debounce_off_s: number;
  on_delay_s: number;
  off_delay_s: number;
  stale_after_s: number | null;
  message: string;
  notification_group_id: number | null;
  enabled: boolean;
  row_order: number;
};
export type ValidationIssue = { severity: "ERROR" | "WARNING"; code: string; message: string; object_type: string; object_id: string; field: string | null; };
export type RevisionDiff = { added: string[]; removed: string[]; changed: string[]; };
export type RevisionPreview = { revision_id: string; base_revision_id: string | null; tags: RevisionDiff; alarms: RevisionDiff; notification_policies: RevisionDiff; };
export type DraftReview = { ok: boolean; revision_id: string | null; issues: ValidationIssue[]; source_hash: string | null; preview: RevisionPreview | null; };
export type AlarmBrowserSummary = { views: Record<string, number>; priorities: Record<string, number>; categories: Record<string, number>; };
export type NotificationFailure = { outbox_id: number; alarm_id: string; event_type: string; route_key: string; attempts: number; last_error: string | null; created_at: string; };
export type NotificationStatus = { worker_running: boolean; counts: Record<"PENDING" | "PROCESSING" | "SENT" | "FAILED", number>; pending_due: number; recent_failures: NotificationFailure[]; };

function endpoint(path: string): string { const base = document.baseURI.endsWith("/") ? document.baseURI : `${document.baseURI}/`; return new URL(`api/${path.replace(/^\//, "")}`, base).toString(); }
async function request<T>(path: string, init?: RequestInit): Promise<T> { const response = await fetch(endpoint(path), {...init, headers: {"Content-Type": "application/json", ...(init?.headers ?? {})}}); if (!response.ok) throw new Error(`${response.status} ${await response.text()}`); return response.json() as Promise<T>; }

export const api = {
  session: () => request<Session>("session"),
  locale: (locale: "en" | "fi") => request<Session>("session/locale", {method: "PUT", body: JSON.stringify({locale})}),
  translations: (locale: string) => request<TranslationBundle>(`i18n/${locale}`),
  runtime: () => request<RuntimeStatus>("runtime/status"),
  browseAlarms: (view: string, priority?: string, category?: string, search?: string) => {
    const params = new URLSearchParams({view, limit: "2000"});
    if (priority) params.set("priority", priority);
    if (category) params.set("category", category);
    if (search) params.set("search", search);
    return request<AlarmRow[]>(`alarm-browser?${params.toString()}`);
  },
  alarmSummary: () => request<AlarmBrowserSummary>("alarm-browser/summary"),
  history: () => request<AlarmEvent[]>("alarms/history?limit=300"),
  ack: (alarmId: string) => request(`alarms/${encodeURIComponent(alarmId)}/ack`, {method: "POST"}),
  ackAll: () => request<{count: number}>("alarms/ack-all", {method: "POST"}),
  reset: (alarmId: string) => request(`alarms/${encodeURIComponent(alarmId)}/reset`, {method: "POST"}),
  shelve: (alarmId: string, durationS: number, reason: string) => request(`alarms/${encodeURIComponent(alarmId)}/shelve`, {method: "POST", body: JSON.stringify({duration_s: durationS, reason: reason || null})}),
  unshelve: (alarmId: string) => request(`alarms/${encodeURIComponent(alarmId)}/unshelve`, {method: "POST"}),
  suppress: (alarmId: string, reason: string) => request(`alarms/${encodeURIComponent(alarmId)}/suppress`, {method: "POST", body: JSON.stringify({reason: reason || null})}),
  unsuppress: (alarmId: string, reason: string) => request(`alarms/${encodeURIComponent(alarmId)}/unsuppress`, {method: "POST", body: JSON.stringify({reason: reason || null})}),
  outOfService: (alarmId: string, reason: string) => request(`alarms/${encodeURIComponent(alarmId)}/out-of-service`, {method: "POST", body: JSON.stringify({reason: reason || null})}),
  inService: (alarmId: string, reason: string) => request(`alarms/${encodeURIComponent(alarmId)}/in-service`, {method: "POST", body: JSON.stringify({reason: reason || null})}),
  drafts: () => request<EngineeringDraft[]>("engineering/drafts"),
  createDraft: (name: string, cloneActive = true) => request<EngineeringDraft>("engineering/drafts", {method: "POST", body: JSON.stringify({name, clone_active: cloneActive})}),
  engineeringEntities: () => request<HAEntityOption[]>("engineering/entities"),
  engineeringEntityValues: (entityIds: string[]) => request<Record<string, HAEntityPreview>>("engineering/entity-values", {method: "POST", body: JSON.stringify({entity_ids: entityIds})}),
  alarmTable: (draftId: string) => request<AlarmEngineeringRow[]>(`engineering/drafts/${encodeURIComponent(draftId)}/alarm-table`),
  notificationGroups: (draftId: string) => request<NotificationGroupEngineeringRow[]>(`engineering/drafts/${encodeURIComponent(draftId)}/notification-groups`),
  nextAlarmId: (draftId: string) => request<{alarm_id: number}>(`engineering/drafts/${encodeURIComponent(draftId)}/next-alarm-id`),
  nextNotificationGroupId: (draftId: string) => request<{group_id: number}>(`engineering/drafts/${encodeURIComponent(draftId)}/next-notification-group-id`),
  saveAlarmTable: (draftId: string, expectedUpdatedAt: string, rows: AlarmEngineeringRow[], notificationGroups: NotificationGroupEngineeringRow[]) => request<{saved: number; saved_notification_groups: number; updated_at: string}>(`engineering/drafts/${encodeURIComponent(draftId)}/alarm-table`, {method: "PUT", body: JSON.stringify({expected_updated_at: expectedUpdatedAt, rows, notification_groups: notificationGroups})}),
  reviewDraft: (draftId: string) => request<DraftReview>(`engineering/drafts/${encodeURIComponent(draftId)}/review`, {method: "POST"}),
  activateRevision: (revisionId: string) => request(`engineering/revisions/${encodeURIComponent(revisionId)}/activate`, {method: "POST"}),
  users: () => request<AppUser[]>("admin/users"),
  setUserRole: (userId: string, role: UserRole) => request<AppUser>(`admin/users/${encodeURIComponent(userId)}/role`, {method: "PUT", body: JSON.stringify({role})}),
  notificationStatus: () => request<NotificationStatus>("admin/notifications/status"),
  retryFailedNotifications: (outboxIds?: number[]) => request<{retried: number; outbox_ids: number[]}>("admin/notifications/retry-failed", {method: "POST", body: JSON.stringify({outbox_ids: outboxIds ?? null})}),
};