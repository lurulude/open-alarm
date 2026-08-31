import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type AlarmEngineeringRow,
  type DraftReview,
  type EngineeringDraft,
  type HAEntityOption,
  type HAEntityPreview,
  type NotificationGroupEngineeringRow,
  type Session,
} from "./api";

type T = (key: string) => string;
const ENTITY_RESULT_LIMIT = 20;

function emptyRow(alarmId: number, rowOrder: number): AlarmEngineeringRow {
  return {
    alarm_id: alarmId,
    entity_id: "",
    kind: "ANALOG",
    condition: "HIGH",
    hihi: null,
    hi: null,
    lo: null,
    lolo: null,
    alarm_value: null,
    priority: "P2",
    category: "PROCESS",
    hysteresis: 0,
    debounce_on_s: 0,
    debounce_off_s: 0,
    on_delay_s: 0,
    off_delay_s: 0,
    stale_after_s: null,
    message: "",
    notification_group_id: null,
    enabled: true,
    row_order: rowOrder,
  };
}

function emptyGroup(groupId: number, rowOrder: number, groupLabel: string): NotificationGroupEngineeringRow {
  return {
    group_id: groupId,
    name: `${groupLabel} ${groupId}`,
    title: "Open Alarm",
    target_entity_ids: [],
    notify_delay_s: 0,
    enabled: true,
    row_order: rowOrder,
  };
}

function conditions(kind: AlarmEngineeringRow["kind"]): string[] {
  if (kind === "DIGITAL") return ["EQUALS", "NOT_EQUALS"];
  if (kind === "DEVICE") return ["UNAVAILABLE", "UNKNOWN", "MISSING", "STALE", "BAD_QUALITY"];
  return [];
}

function numberOrNull(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function numberOrZero(value: string): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
}

function entitySearchText(entity: HAEntityOption): string {
  return [entity.entity_id, entity.name, entity.device_name, entity.manufacturer, entity.model, entity.platform]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase();
}

function entityMatches(entityOptions: HAEntityOption[], query: string): HAEntityOption[] {
  const needle = query.trim().toLocaleLowerCase();
  const matches = needle
    ? entityOptions.filter((entity) => entitySearchText(entity).includes(needle))
    : entityOptions;
  return matches.slice(0, ENTITY_RESULT_LIMIT);
}

function previewValue(preview: HAEntityPreview | undefined): string | null {
  if (!preview?.state) return null;
  return preview.unit ? `${preview.state} ${preview.unit}` : preview.state;
}

function deviceInfo(entity: HAEntityOption): string | null {
  const details = [entity.device_name, entity.manufacturer, entity.model].filter((value): value is string => Boolean(value));
  return details.length > 0 ? details.join(" · ") : null;
}

function technicalInfo(entity: HAEntityOption, preview: HAEntityPreview | undefined): string | null {
  const details = [entity.platform, preview?.device_class].filter((value): value is string => Boolean(value));
  return details.length > 0 ? details.join(" · ") : null;
}

export function EngineeringWorkspace({session, t}: {session: Session; t: T}) {
  const [draft, setDraft] = useState<EngineeringDraft | null>(null);
  const [rows, setRows] = useState<AlarmEngineeringRow[]>([]);
  const [groups, setGroups] = useState<NotificationGroupEngineeringRow[]>([]);
  const [entityOptions, setEntityOptions] = useState<HAEntityOption[]>([]);
  const [entityPreviews, setEntityPreviews] = useState<Record<string, HAEntityPreview>>({});
  const [activeEntityRow, setActiveEntityRow] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);
  const [review, setReview] = useState<DraftReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const notifyEntities = useMemo(
    () => entityOptions.filter((entity) => entity.entity_id.startsWith("notify.")),
    [entityOptions],
  );

  const load = useCallback(async () => {
    const drafts = await api.drafts();
    let working = drafts[0] ?? null;
    if (!working) working = await api.createDraft("Working configuration", true);
    const [nextRows, nextGroups] = await Promise.all([
      api.alarmTable(working.draft_id),
      api.notificationGroups(working.draft_id),
    ]);
    setDraft(working);
    setRows(nextRows);
    setGroups(nextGroups);
    setDirty(false);
    setReview(null);
  }, []);

  useEffect(() => {
    void load().catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [load]);

  useEffect(() => {
    void api.engineeringEntities().then(setEntityOptions).catch(() => setEntityOptions([]));
  }, []);

  useEffect(() => {
    if (activeEntityRow === null) return;
    const row = rows[activeEntityRow];
    if (!row) return;
    const matches = entityMatches(entityOptions, row.entity_id);
    if (matches.length === 0) return;
    const entityIds = matches.map((entity) => entity.entity_id);
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void api.engineeringEntityValues(entityIds)
        .then((previews) => {
          if (!cancelled) setEntityPreviews((current) => ({...current, ...previews}));
        })
        .catch(() => undefined);
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [activeEntityRow, entityOptions, rows]);

  function invalidateReview() {
    setReview(null);
    setMessage(null);
  }

  function markDirty() {
    setDirty(true);
    invalidateReview();
  }

  function updateRow(index: number, patch: Partial<AlarmEngineeringRow>) {
    setRows((current) => current.map((row, rowIndex) => rowIndex === index ? {...row, ...patch} : row));
    markDirty();
  }

  function updateGroup(index: number, patch: Partial<NotificationGroupEngineeringRow>) {
    setGroups((current) => current.map((group, groupIndex) => groupIndex === index ? {...group, ...patch} : group));
    markDirty();
  }

  function setKind(index: number, kind: AlarmEngineeringRow["kind"]) {
    updateRow(index, {
      kind,
      condition: kind === "DIGITAL" ? "EQUALS" : kind === "DEVICE" ? "UNAVAILABLE" : "HIGH",
      hihi: null,
      hi: null,
      lo: null,
      lolo: null,
      alarm_value: kind === "DIGITAL" ? "on" : null,
      hysteresis: 0,
    });
  }

  async function addRow() {
    if (!draft) return;
    setBusy(true);
    try {
      const serverNext = (await api.nextAlarmId(draft.draft_id)).alarm_id;
      const localNext = rows.reduce((maximum, row) => Math.max(maximum, row.alarm_id), 0) + 1;
      setRows((current) => [...current, emptyRow(Math.max(serverNext, localNext), current.length)]);
      markDirty();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function addGroup() {
    if (!draft) return;
    setBusy(true);
    try {
      const serverNext = (await api.nextNotificationGroupId(draft.draft_id)).group_id;
      const localNext = groups.reduce((maximum, group) => Math.max(maximum, group.group_id), 0) + 1;
      const nextId = Math.max(serverNext, localNext);
      setGroups((current) => [...current, emptyGroup(nextId, current.length, t("engineering.default_group"))]);
      markDirty();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function deleteRow(index: number) {
    setRows((current) => current.filter((_, rowIndex) => rowIndex !== index));
    markDirty();
  }

  function deleteGroup(index: number) {
    const removed = groups[index];
    if (!removed) return;
    setGroups((current) => current.filter((_, groupIndex) => groupIndex !== index));
    setRows((current) => current.map((row) => row.notification_group_id === removed.group_id ? {...row, notification_group_id: null} : row));
    markDirty();
  }

  function toggleGroupTarget(index: number, entityId: string, checked: boolean) {
    const group = groups[index];
    if (!group) return;
    const targets = checked
      ? Array.from(new Set([...group.target_entity_ids, entityId])).sort()
      : group.target_entity_ids.filter((target) => target !== entityId);
    updateGroup(index, {target_entity_ids: targets});
  }

  async function save() {
    if (!draft) return;
    const normalizedRows = rows.map((row, index) => ({...row, row_order: index}));
    const normalizedGroups = groups.map((group, index) => ({...group, row_order: index, name: group.name.trim(), title: group.title.trim()}));

    const ids = normalizedRows.map((row) => row.alarm_id);
    const duplicate = ids.find((alarmId, index) => ids.indexOf(alarmId) !== index);
    if (duplicate !== undefined) {
      setError(`${t("engineering.error.duplicate_alarm_id")}: ${duplicate}`);
      return;
    }
    const missingEntity = normalizedRows.findIndex((row) => !row.entity_id.trim());
    if (missingEntity >= 0) {
      setError(`${t("engineering.error.entity_required_row")} ${missingEntity + 1}`);
      return;
    }
    for (let index = 0; index < normalizedRows.length; index += 1) {
      const row = normalizedRows[index];
      if (row.kind === "ANALOG") {
        const limits = [row.hihi, row.hi, row.lo, row.lolo].filter((value): value is number => value !== null);
        if (limits.length === 0) {
          setError(`${t("engineering.error.analog_limit_required_row")} ${index + 1}`);
          return;
        }
        if (limits.some((value, limitIndex) => limitIndex > 0 && limits[limitIndex - 1] <= value)) {
          setError(`${t("engineering.error.analog_order_row")} ${index + 1}: HiHi > Hi > Lo > LoLo`);
          return;
        }
      }
      if (row.kind === "DIGITAL" && !row.alarm_value?.trim()) {
        setError(`${t("engineering.error.digital_value_required_row")} ${index + 1}`);
        return;
      }
    }
    for (let index = 0; index < normalizedGroups.length; index += 1) {
      const group = normalizedGroups[index];
      if (!group.name) {
        setError(`${t("engineering.error.group_name_required")} ${index + 1}`);
        return;
      }
      if (!group.title) {
        setError(`${t("engineering.error.group_title_required")}: ${group.name}`);
        return;
      }
      if (group.target_entity_ids.length === 0) {
        setError(`${t("engineering.error.group_target_required")}: ${group.name}`);
        return;
      }
    }

    setBusy(true);
    try {
      const result = await api.saveAlarmTable(
        draft.draft_id,
        draft.updated_at,
        normalizedRows,
        normalizedGroups,
      );
      setRows(normalizedRows);
      setGroups(normalizedGroups);
      setDraft({...draft, updated_at: result.updated_at});
      setDirty(false);
      setReview(null);
      setMessage(`${t("engineering.message.saved_all")} (${result.saved})`);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function discard() {
    if (!draft) return;
    setBusy(true);
    try {
      const [nextRows, nextGroups] = await Promise.all([
        api.alarmTable(draft.draft_id),
        api.notificationGroups(draft.draft_id),
      ]);
      setRows(nextRows);
      setGroups(nextGroups);
      setDirty(false);
      setReview(null);
      setMessage(t("engineering.message.discarded"));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function reviewChanges() {
    if (!draft || dirty) return;
    setBusy(true);
    try {
      const result = await api.reviewDraft(draft.draft_id);
      setReview(result);
      setMessage(result.revision_id ? t("engineering.message.review_ready") : null);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function activate() {
    if (session.role !== "ADMIN" || !review?.revision_id) return;
    setBusy(true);
    try {
      await api.activateRevision(review.revision_id);
      setMessage(`${t("engineering.message.activated")}: ${review.revision_id}`);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return <section className="engineering-main simple-engineering">
    <div className="engineering-toolbar">
      <div>
        <strong>{t("nav.engineering")}</strong>
        <span className="toolbar-sub">{rows.length} {t("common.rows")} · {groups.length} {t("engineering.notification_groups_count")}{dirty ? ` · ${t("engineering.unsaved_changes")}` : ""}</span>
      </div>
      <div className="toolbar-actions">
        <button disabled={busy || !draft} onClick={() => void addRow()}>{t("engineering.add_row")}</button>
        <button disabled={busy || !dirty} onClick={() => void save()}>{t("engineering.save_all")}</button>
        <button disabled={busy || !dirty} onClick={() => void discard()}>{t("engineering.discard_changes")}</button>
        <button disabled={busy || dirty || !draft} onClick={() => void reviewChanges()}>{t("engineering.review")}</button>
      </div>
    </div>

    {error && <div className="error-banner engineering-banner">{error}</div>}
    {message && <div className="success-banner engineering-banner">{message}</div>}

    <section className="notification-group-manager">
      <div className="section-title">
        <span>{t("engineering.notification_groups")}</span>
        <button disabled={busy || !draft} onClick={() => void addGroup()}>{t("engineering.add_notification_group")}</button>
      </div>
      <p className="toolbar-sub">{t("engineering.notification_groups_help")}</p>
      <div className="engineering-grid-wrap">
        <table className="engineering-grid notification-group-grid">
          <thead><tr><th>ID</th><th>{t("engineering.group_name")}</th><th>{t("engineering.group_title")}</th><th>{t("engineering.group_targets")}</th><th>{t("engineering.notify_delay")}</th><th>{t("common.enabled")}</th><th/></tr></thead>
          <tbody>{groups.map((group, index) => <tr key={group.group_id}>
            <td><input className="mono short-input" value={group.group_id} readOnly/></td>
            <td><input className="medium-input" value={group.name} onChange={(event) => updateGroup(index, {name: event.target.value})}/></td>
            <td><input className="medium-input" value={group.title} onChange={(event) => updateGroup(index, {title: event.target.value})}/></td>
            <td>
              <div className="notification-target-list">
                {notifyEntities.length === 0 && <span className="empty-compact">{t("engineering.no_notify_entities")}</span>}
                {notifyEntities.map((entity) => <label key={entity.entity_id} className="notification-target-option">
                  <input
                    type="checkbox"
                    checked={group.target_entity_ids.includes(entity.entity_id)}
                    onChange={(event) => toggleGroupTarget(index, entity.entity_id, event.target.checked)}
                  />
                  <span className="mono">{entity.entity_id}</span>
                  {entity.name && <span>{entity.name}</span>}
                </label>)}
              </div>
            </td>
            <td><input type="number" min="0" value={group.notify_delay_s} onChange={(event) => updateGroup(index, {notify_delay_s: numberOrZero(event.target.value)})}/></td>
            <td><input type="checkbox" checked={group.enabled} onChange={(event) => updateGroup(index, {enabled: event.target.checked})}/></td>
            <td className="row-actions"><button disabled={busy} onClick={() => deleteGroup(index)}>×</button></td>
          </tr>)}</tbody>
        </table>
        {groups.length === 0 && <div className="empty-state">{t("engineering.no_notification_groups")}</div>}
      </div>
    </section>

    <div className="engineering-grid-wrap">
      <table className="engineering-grid alarm-engineering-grid">
        <thead><tr>
          <th>{t("engineering.alarm_id")}</th><th>{t("engineering.entity")}</th><th>{t("engineering.type")}</th><th>HiHi</th><th>Hi</th><th>Lo</th><th>LoLo</th><th>{t("engineering.condition")}</th><th>{t("engineering.value")}</th><th>{t("engineering.hysteresis")}</th><th>{t("engineering.on_delay")}</th><th>{t("engineering.off_delay")}</th><th>{t("engineering.priority")}</th><th>{t("engineering.message")}</th><th>{t("engineering.notification_group")}</th><th>{t("common.enabled")}</th><th/>
        </tr></thead>
        <tbody>{rows.map((row, index) => {
          const matches = activeEntityRow === index ? entityMatches(entityOptions, row.entity_id) : [];
          const selectedPreview = entityPreviews[row.entity_id];
          return <tr key={row.alarm_id}>
            <td><input className="mono short-input" value={row.alarm_id} readOnly title={t("engineering.assigned_automatically")}/></td>
            <td className="entity-picker-cell">
              <div className="entity-picker">
                <div className="entity-picker-input-row">
                  <input className="mono entity-input" autoComplete="off" value={row.entity_id} placeholder={t("engineering.entity_search_placeholder")} onFocus={() => setActiveEntityRow(index)} onBlur={() => window.setTimeout(() => setActiveEntityRow((current) => current === index ? null : current), 120)} onChange={(event) => updateRow(index, {entity_id: event.target.value})}/>
                  {previewValue(selectedPreview) && <span className="entity-current-value">{previewValue(selectedPreview)}</span>}
                </div>
                {activeEntityRow === index && <div className="entity-picker-menu">
                  {matches.map((entity) => {
                    const preview = entityPreviews[entity.entity_id];
                    const name = preview?.friendly_name || entity.name;
                    const device = deviceInfo(entity);
                    const technical = technicalInfo(entity, preview);
                    return <button type="button" className="entity-picker-option" key={entity.entity_id} onMouseDown={(event) => { event.preventDefault(); updateRow(index, {entity_id: entity.entity_id}); setActiveEntityRow(null); }}>
                      <span className="entity-picker-option-head"><span className="mono entity-picker-id">{entity.entity_id}</span><span className="mono entity-picker-live">{previewValue(preview) ?? "—"}</span></span>
                      {name && <span className="entity-picker-name">{name}</span>}
                      {device && <span className="entity-picker-device">{device}</span>}
                      {technical && <span className="entity-picker-tech">{technical}</span>}
                    </button>;
                  })}
                  {matches.length === 0 && <div className="entity-picker-empty">{t("engineering.no_registry_match")}</div>}
                </div>}
              </div>
            </td>
            <td><select value={row.kind} onChange={(event) => setKind(index, event.target.value as AlarmEngineeringRow["kind"])}><option value="ANALOG">{t("engineering.kind.ANALOG")}</option><option value="DIGITAL">{t("engineering.kind.DIGITAL")}</option><option value="DEVICE">{t("engineering.kind.DEVICE")}</option></select></td>
            <td><input type="number" disabled={row.kind !== "ANALOG"} value={row.hihi ?? ""} onChange={(event) => updateRow(index, {hihi: numberOrNull(event.target.value)})}/></td>
            <td><input type="number" disabled={row.kind !== "ANALOG"} value={row.hi ?? ""} onChange={(event) => updateRow(index, {hi: numberOrNull(event.target.value)})}/></td>
            <td><input type="number" disabled={row.kind !== "ANALOG"} value={row.lo ?? ""} onChange={(event) => updateRow(index, {lo: numberOrNull(event.target.value)})}/></td>
            <td><input type="number" disabled={row.kind !== "ANALOG"} value={row.lolo ?? ""} onChange={(event) => updateRow(index, {lolo: numberOrNull(event.target.value)})}/></td>
            <td>{row.kind === "ANALOG" ? <span>—</span> : <select value={row.condition} onChange={(event) => updateRow(index, {condition: event.target.value})}>{conditions(row.kind).map((condition) => <option key={condition} value={condition}>{t(`engineering.condition.${condition}`)}</option>)}</select>}</td>
            <td>{row.kind === "DIGITAL" ? <input className="medium-input" value={row.alarm_value ?? ""} onChange={(event) => updateRow(index, {alarm_value: event.target.value})}/> : <span>—</span>}</td>
            <td><input type="number" min="0" disabled={row.kind !== "ANALOG"} value={row.hysteresis} onChange={(event) => updateRow(index, {hysteresis: numberOrZero(event.target.value)})}/></td>
            <td><input type="number" min="0" value={row.on_delay_s} onChange={(event) => updateRow(index, {on_delay_s: numberOrZero(event.target.value)})}/></td>
            <td><input type="number" min="0" value={row.off_delay_s} onChange={(event) => updateRow(index, {off_delay_s: numberOrZero(event.target.value)})}/></td>
            <td><input className="short-input" value={row.priority} onChange={(event) => updateRow(index, {priority: event.target.value})}/></td>
            <td><input className="message-input" value={row.message} onChange={(event) => updateRow(index, {message: event.target.value})}/></td>
            <td><select value={row.notification_group_id ?? ""} onChange={(event) => updateRow(index, {notification_group_id: event.target.value ? Number(event.target.value) : null})}><option value="">—</option>{groups.filter((group) => group.enabled).map((group) => <option key={group.group_id} value={group.group_id}>{group.name}</option>)}</select></td>
            <td><input type="checkbox" checked={row.enabled} onChange={(event) => updateRow(index, {enabled: event.target.checked})}/></td>
            <td className="row-actions"><button disabled={busy} onClick={() => deleteRow(index)}>×</button></td>
          </tr>;
        })}</tbody>
      </table>
      {rows.length === 0 && <div className="empty-state">{t("engineering.no_alarm_rows")}</div>}
    </div>

    {review && <div className="engineering-bottom simple-review">
      <section className="validation-panel">
        <div className="section-title">{t("engineering.validation")} <span className={review.ok ? "validation-ok" : "validation-fail"}>{review.ok ? t("engineering.validation_ok") : t("engineering.validation_failed")}</span></div>
        {review.issues.length === 0 ? <div className="empty-compact">{t("engineering.no_issues")}</div> : <table className="compact-grid"><thead><tr><th>{t("engineering.severity")}</th><th>{t("engineering.object")}</th><th>{t("engineering.message")}</th></tr></thead><tbody>{review.issues.map((issue, index) => <tr key={`${issue.code}-${issue.object_id}-${index}`}><td>{issue.severity}</td><td className="mono">{issue.object_id}</td><td>{issue.message}</td></tr>)}</tbody></table>}
      </section>
      <section className="preview-panel">
        <div className="section-title">{t("engineering.preview")}</div>
        {!review.preview ? <div className="empty-compact">{t("engineering.no_candidate_revision")}</div> : <>
          <div className="preview-head"><span className="mono">{review.preview.revision_id}</span></div>
          <div className="diff-block"><strong>{t("engineering.alarms")}</strong><span>+ {review.preview.alarms.added.length}</span><span>~ {review.preview.alarms.changed.length}</span><span>- {review.preview.alarms.removed.length}</span></div>
          <div className="diff-block"><strong>{t("engineering.notification_groups")}</strong><span>+ {review.preview.notification_policies.added.length}</span><span>~ {review.preview.notification_policies.changed.length}</span><span>- {review.preview.notification_policies.removed.length}</span></div>
          {session.role === "ADMIN" && <button className="activate-button" disabled={busy} onClick={() => void activate()}>{t("engineering.activate")}</button>}
        </>}
      </section>
    </div>}
  </section>;
}
