import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminWorkspace } from "./Admin";
import { api, type AlarmBrowserSummary, type AlarmEvent, type AlarmRow, type RuntimeStatus, type Session } from "./api";
import "./controls.css";
import { EngineeringWorkspace } from "./Engineering";

type View = "active" | "unacknowledged" | "shelved" | "inhibited" | "suppressed" | "out_of_service" | "history" | "engineering" | "admin";

const fallback: Record<string, string> = {
  "app.title": "Open Alarm", "nav.active_alarms": "Active alarms", "nav.unacknowledged": "Unacknowledged",
  "nav.shelved": "Shelved", "nav.inhibited": "Inhibited", "nav.suppressed": "Suppressed", "nav.out_of_service": "Out of service",
  "nav.history": "History", "nav.engineering": "Engineering", "nav.admin": "Users",
  "common.all": "All", "common.no_records": "No records", "common.rows": "rows", "common.system": "System",
  "runtime.ha_online": "HA ONLINE", "runtime.ha_offline": "HA OFFLINE", "runtime.no_active_revision": "NO ACTIVE REVISION",
  "runtime.sources": "sources", "runtime.idle": "idle", "runtime.normal": "normal",
  "alarm.browser.search": "Search", "alarm.browser.priority": "Priority", "alarm.browser.category": "Category",
  "alarm.browser.search_placeholder": "Alarm ID / source / message", "alarm.browser.priority_short": "Pri",
  "alarm.browser.state": "State", "alarm.browser.active_since": "Active since", "alarm.browser.alarm": "Alarm",
  "alarm.browser.message": "Message", "alarm.browser.source": "Source", "alarm.browser.value": "Value",
  "alarm.browser.control": "Control", "alarm.browser.ack": "ACK",
  "alarm.condition.HIGH_HIGH": "High-high", "alarm.condition.HIGH": "High", "alarm.condition.LOW": "Low", "alarm.condition.LOW_LOW": "Low-low",
  "alarm.condition.EQUALS": "Equals", "alarm.condition.NOT_EQUALS": "Not equal", "alarm.condition.UNAVAILABLE": "Unavailable",
  "alarm.condition.UNKNOWN": "Unknown", "alarm.condition.MISSING": "Missing", "alarm.condition.STALE": "Stale", "alarm.condition.BAD_QUALITY": "Bad quality",
  "history.time": "Time", "history.event": "Event", "history.user": "User",
  "alarm.action.acknowledge": "Acknowledge", "alarm.action.acknowledge_all": "Acknowledge all",
  "alarm.action.reset": "Reset", "alarm.action.shelve": "Shelve", "alarm.action.unshelve": "Unshelve",
  "alarm.action.suppress": "Suppress", "alarm.action.unsuppress": "Unsuppress",
  "alarm.action.out_of_service": "Take out of service", "alarm.action.in_service": "Return to service",
  "alarm.control.reason": "Reason", "alarm.control.reason_optional": "Reason (optional)", "alarm.control.duration": "Shelf duration", "alarm.control.latched": "Latched",
  "alarm.control.inhibited_by": "Inhibited by", "alarm.control.returned_waiting_reset": "Returned, waiting for reset",
  "engineering.save_all": "Save all", "engineering.review": "Review changes", "engineering.activate": "Activate revision",
  "engineering.kind.DIGITAL": "Digital", "engineering.kind.DEVICE": "Device",
  "role.viewer": "Viewer", "role.operator": "Operator", "role.engineer": "Engineer", "role.admin": "Admin",
};
const alarmViews: View[] = ["active", "unacknowledged", "shelved", "inhibited", "suppressed", "out_of_service"];

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [messages, setMessages] = useState<Record<string, string>>(fallback);
  const [view, setView] = useState<View>("active");
  const [alarms, setAlarms] = useState<AlarmRow[]>([]);
  const [summary, setSummary] = useState<AlarmBrowserSummary | null>(null);
  const [priorityFilter, setPriorityFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [search, setSearch] = useState("");
  const [history, setHistory] = useState<AlarmEvent[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [controlReason, setControlReason] = useState("");
  const [shelfMinutes, setShelfMinutes] = useState(60);
  const [error, setError] = useState<string | null>(null);

  const t = useCallback((key: string) => messages[key] ?? fallback[key] ?? key, [messages]);
  const canOperate = session ? ["OPERATOR", "ENGINEER", "ADMIN"].includes(session.role) : false;
  const canEngineer = session ? ["ENGINEER", "ADMIN"].includes(session.role) : false;
  const canAdmin = session?.role === "ADMIN";
  const alarmView = alarmViews.includes(view);

  const refresh = useCallback(async () => {
    try {
      setRuntime(await api.runtime());
      if (alarmViews.includes(view)) {
        const [nextAlarms, nextSummary] = await Promise.all([
          api.browseAlarms(view, priorityFilter || undefined, categoryFilter || undefined, search || undefined),
          api.alarmSummary(),
        ]);
        setAlarms(nextAlarms);
        setSummary(nextSummary);
      } else if (view === "history") setHistory(await api.history());
      setError(null);
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
  }, [view, priorityFilter, categoryFilter, search]);

  useEffect(() => { void (async () => { try { const nextSession = await api.session(); setSession(nextSession); setMessages((await api.translations(nextSession.locale)).messages); } catch (err) { setError(err instanceof Error ? err.message : String(err)); } })(); }, []);
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 1000); return () => window.clearInterval(timer); }, [refresh]);

  const selectedAlarm = useMemo(() => alarms.find((alarm) => alarm.alarm_id === selected) ?? null, [alarms, selected]);
  async function setLocale(locale: "en" | "fi") { const updated = await api.locale(locale); setSession(updated); setMessages((await api.translations(locale)).messages); }
  async function perform(action: () => Promise<unknown>, clearReason = false) { try { await action(); if (clearReason) setControlReason(""); await refresh(); setError(null); } catch (err) { setError(err instanceof Error ? err.message : String(err)); } }
  async function acknowledgeSelected() { if (selectedAlarm) await perform(() => api.ack(selectedAlarm.alarm_id)); }
  async function acknowledgeAll() { await perform(() => api.ackAll()); }
  function formatValue(value: unknown, unit?: string | null): string {
    if (value === null || value === undefined) return "—";
    const text = typeof value === "object" ? JSON.stringify(value) : String(value);
    return unit ? `${text} ${unit}` : text;
  }
  function formatTime(value: string | null): string { if (!value) return "—"; const locale = session?.locale === "fi" ? "fi-FI" : "en-GB"; return new Intl.DateTimeFormat(locale, {dateStyle: "short", timeStyle: "medium"}).format(new Date(value)); }
  function alarmMessage(alarm: AlarmRow): string {
    if (alarm.message_key) return t(alarm.message_key);
    if (session?.locale === "fi" && alarm.message_fi?.trim()) return alarm.message_fi;
    return alarm.message?.trim() || alarm.source_friendly_name?.trim() || alarm.source_entity_id || alarm.alarm_id;
  }
  function alarmLabel(alarm: AlarmRow): string {
    if (alarm.origin === "SYSTEM") return t("common.system");
    if (alarm.kind === "DIGITAL") return t("engineering.kind.DIGITAL");
    if (alarm.condition) return t(`alarm.condition.${alarm.condition}`);
    if (alarm.kind === "DEVICE") return t("engineering.kind.DEVICE");
    return alarm.alarm_id;
  }
  function historyAlarmLabel(event: AlarmEvent): string {
    if (event.origin === "SYSTEM") return t("common.system");
    if (event.kind === "DIGITAL") return t("engineering.kind.DIGITAL");
    if (event.condition) return t(`alarm.condition.${event.condition}`);
    if (event.kind === "DEVICE") return t("engineering.kind.DEVICE");
    const legacyConditions: Array<[string, string]> = [
      ["_HIHI", "HIGH_HIGH"], ["_LOLO", "LOW_LOW"], ["_HI", "HIGH"], ["_LO", "LOW"],
    ];
    const legacy = legacyConditions.find(([suffix]) => event.alarm_id.endsWith(suffix));
    if (legacy) return t(`alarm.condition.${legacy[1]}`);
    if (event.alarm_id.endsWith("_DIGITAL")) return t("engineering.kind.DIGITAL");
    if (event.alarm_id.endsWith("_DEVICE")) return t("engineering.kind.DEVICE");
    return event.alarm_id;
  }
  function historyMessage(event: AlarmEvent): string {
    if (event.message_key) return t(event.message_key);
    if (session?.locale === "fi" && event.message_fi?.trim()) return event.message_fi;
    return event.message?.trim() || event.source_friendly_name?.trim() || event.source_entity_id || "—";
  }
  function sourceName(alarm: AlarmRow): string {
    if (alarm.origin === "SYSTEM") return t("common.system");
    return alarm.source_friendly_name?.trim() || alarm.source_entity_id || alarm.source_tag_id || "—";
  }
  function controlState(alarm: AlarmRow): string {
    const flags: string[] = [];
    if (alarm.latched) flags.push(alarm.condition_abnormal ? t("alarm.control.latched") : t("alarm.control.returned_waiting_reset"));
    if (alarm.out_of_service) flags.push(t("nav.out_of_service"));
    if (alarm.suppressed) flags.push(t("nav.suppressed"));
    if (alarm.inhibited) flags.push(`${t("alarm.control.inhibited_by")}: ${alarm.inhibited_by.join(", ") || "—"}`);
    if (alarm.shelved_until) flags.push(`${t("nav.shelved")} → ${formatTime(alarm.shelved_until)}`);
    return flags.join(" · ") || "—";
  }
  function viewLabel(key: string, viewName: string): string { const count = summary?.views[viewName]; return count === undefined ? t(key) : `${t(key)} (${count})`; }
  function roleLabel(): string { return session ? t(`role.${session.role.toLowerCase()}`) : "—"; }

  return <div className="app-shell">
    <header className="topbar"><div className="brand"><span className="beacon"/>{t("app.title")}</div><div className="status-strip">{runtime?.configured === true && <span className={`status ${runtime.connected ? "ok" : "fault"}`}>{runtime.connected ? t("runtime.ha_online") : t("runtime.ha_offline")}</span>}<span>{runtime?.active_revision_id ?? t("runtime.no_active_revision")}</span><span>{runtime?.monitored_entities ?? 0} {t("runtime.sources")}</span><span>{session?.display_name ?? session?.user_name ?? "—"} · {roleLabel()}</span><select value={session?.locale ?? "en"} onChange={(e) => void setLocale(e.target.value as "en" | "fi")} aria-label="Language"><option value="en">EN</option><option value="fi">FI</option></select></div></header>
    <nav className="nav-tabs"><button className={view === "active" ? "active" : ""} onClick={() => setView("active")}>{viewLabel("nav.active_alarms", "active")}</button><button className={view === "unacknowledged" ? "active" : ""} onClick={() => setView("unacknowledged")}>{viewLabel("nav.unacknowledged", "unacknowledged")}</button><button className={view === "shelved" ? "active" : ""} onClick={() => setView("shelved")}>{viewLabel("nav.shelved", "shelved")}</button><button className={view === "inhibited" ? "active" : ""} onClick={() => setView("inhibited")}>{viewLabel("nav.inhibited", "inhibited")}</button>{canEngineer && <button className={view === "suppressed" ? "active" : ""} onClick={() => setView("suppressed")}>{viewLabel("nav.suppressed", "suppressed")}</button>}{canEngineer && <button className={view === "out_of_service" ? "active" : ""} onClick={() => setView("out_of_service")}>{viewLabel("nav.out_of_service", "out_of_service")}</button>}<button className={view === "history" ? "active" : ""} onClick={() => setView("history")}>{t("nav.history")}</button>{canEngineer && <button className={view === "engineering" ? "active" : ""} onClick={() => setView("engineering")}>{t("nav.engineering")}</button>}{canAdmin && <button className={view === "admin" ? "active" : ""} onClick={() => setView("admin")}>{t("nav.admin")}</button>}<div className="nav-spacer"/>{(view === "active" || view === "unacknowledged") && canOperate && <button disabled={!selectedAlarm} onClick={() => void acknowledgeSelected()}>{t("alarm.action.acknowledge")}</button>}{(view === "active" || view === "unacknowledged") && canOperate && <button onClick={() => void acknowledgeAll()}>{t("alarm.action.acknowledge_all")}</button>}</nav>
    {runtime?.configured === false && canEngineer && view !== "engineering" && <section className="alarm-control-strip"><strong>{t("nav.engineering")}</strong><span>{t("engineering.save_all")} → {t("engineering.review")} → {t("engineering.activate")}</span><button onClick={() => setView("engineering")}>{t("nav.engineering")}</button></section>}
    {alarmView && <section className="alarm-filter-strip"><label>{t("alarm.browser.search")}<input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("alarm.browser.search_placeholder")}/></label><label>{t("alarm.browser.priority")}<select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)}><option value="">{t("common.all")}</option>{Object.keys(summary?.priorities ?? {}).map((priority) => <option key={priority} value={priority}>{priority} ({summary?.priorities[priority]})</option>)}</select></label><label>{t("alarm.browser.category")}<select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}><option value="">{t("common.all")}</option>{Object.keys(summary?.categories ?? {}).map((category) => <option key={category} value={category}>{category} ({summary?.categories[category]})</option>)}</select></label><span className="filter-result-count">{alarms.length} {t("common.rows")}</span></section>}
    {alarmView && selectedAlarm && canOperate && <section className="alarm-control-strip"><strong title={selectedAlarm.alarm_id}>{alarmLabel(selectedAlarm)}</strong><label>{t("alarm.control.duration")}<select value={shelfMinutes} onChange={(e) => setShelfMinutes(Number(e.target.value))}><option value={15}>15 min</option><option value={60}>1 h</option><option value={240}>4 h</option><option value={480}>8 h</option><option value={1440}>24 h</option></select></label><label className="control-reason">{t("alarm.control.reason_optional")}<input value={controlReason} maxLength={240} onChange={(e) => setControlReason(e.target.value)}/></label>{selectedAlarm.latched && <button disabled={selectedAlarm.condition_abnormal || selectedAlarm.lifecycle !== "ACTIVE_ACK"} onClick={() => void perform(() => api.reset(selectedAlarm.alarm_id))}>{t("alarm.action.reset")}</button>}{selectedAlarm.shelved_until ? <button onClick={() => void perform(() => api.unshelve(selectedAlarm.alarm_id))}>{t("alarm.action.unshelve")}</button> : <button onClick={() => void perform(() => api.shelve(selectedAlarm.alarm_id, shelfMinutes * 60, controlReason), true)}>{t("alarm.action.shelve")}</button>}{canEngineer && (selectedAlarm.suppressed ? <button onClick={() => void perform(() => api.unsuppress(selectedAlarm.alarm_id, controlReason), true)}>{t("alarm.action.unsuppress")}</button> : <button onClick={() => void perform(() => api.suppress(selectedAlarm.alarm_id, controlReason), true)}>{t("alarm.action.suppress")}</button>)}{canEngineer && (selectedAlarm.out_of_service ? <button onClick={() => void perform(() => api.inService(selectedAlarm.alarm_id, controlReason), true)}>{t("alarm.action.in_service")}</button> : <button onClick={() => void perform(() => api.outOfService(selectedAlarm.alarm_id, controlReason), true)}>{t("alarm.action.out_of_service")}</button>)}</section>}
    {error && <div className="error-banner">{error}</div>}
    {view === "engineering" && session && canEngineer ? <EngineeringWorkspace session={session} t={t}/> : view === "admin" && session && canAdmin ? <AdminWorkspace session={session} t={t}/> : <main className="grid-wrap">{view === "history" ? <table className="alarm-grid"><thead><tr><th>{t("history.time")}</th><th>{t("history.event")}</th><th>{t("alarm.browser.alarm")}</th><th>{t("alarm.browser.message")}</th><th>{t("history.user")}</th><th>{t("alarm.browser.value")}</th></tr></thead><tbody>{history.map((event) => <tr key={event.event_id}><td>{formatTime(event.event_at)}</td><td>{t(`event.${event.event_type}`)}</td><td title={event.alarm_id}>{historyAlarmLabel(event)}</td><td>{historyMessage(event)}</td><td title={event.user_id ?? undefined}>{event.user_display_name ?? event.user_id ?? "—"}</td><td className="mono">{formatValue(event.value, event.source_unit)}</td></tr>)}</tbody></table> : <table className="alarm-grid"><thead><tr><th>{t("alarm.browser.priority_short")}</th><th>{t("alarm.browser.state")}</th><th>{t("alarm.browser.active_since")}</th><th>{t("alarm.browser.alarm")}</th><th>{t("alarm.browser.message")}</th><th>{t("alarm.browser.source")}</th><th>{t("alarm.browser.value")}</th><th>{t("alarm.browser.control")}</th><th>{t("alarm.browser.ack")}</th></tr></thead><tbody>{alarms.map((alarm) => <tr key={alarm.alarm_id} className={`${alarm.priority?.toLowerCase() ?? ""} ${alarm.origin === "SYSTEM" ? "system" : ""} ${selected === alarm.alarm_id ? "selected" : ""}`} onClick={() => setSelected(alarm.alarm_id)}><td className="priority">{alarm.priority ?? "—"}</td><td>{t(`alarm.lifecycle.${alarm.lifecycle}`)}</td><td>{formatTime(alarm.active_since)}</td><td title={alarm.alarm_id}>{alarmLabel(alarm)}</td><td>{alarmMessage(alarm)}</td><td title={alarm.source_entity_id ?? undefined}>{sourceName(alarm)}</td><td className="mono">{formatValue(alarm.raw_value, alarm.source_unit)}</td><td>{controlState(alarm)}</td><td>{alarm.ack_user_id ?? "—"}</td></tr>)}</tbody></table>}{(view === "history" ? history.length : alarms.length) === 0 && <div className="empty-state">{t("common.no_records")}</div>}</main>}
    <footer className="footer">Open Alarm · {runtime?.subscription_mode ?? t("runtime.idle")} · {runtime?.reason ?? t("runtime.normal")}</footer>
  </div>;
}
