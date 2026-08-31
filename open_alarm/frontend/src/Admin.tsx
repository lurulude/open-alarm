import { useCallback, useEffect, useState } from "react";
import { api, type AppUser, type NotificationStatus, type Session } from "./api";

type T = (key: string) => string;
const ROLES: AppUser["role"][] = ["VIEWER", "OPERATOR", "ENGINEER", "ADMIN"];

export function AdminWorkspace({session, t}: {session: Session; t: T}) {
  const [users, setUsers] = useState<AppUser[]>([]);
  const [notifications, setNotifications] = useState<NotificationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busyUser, setBusyUser] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);

  const load = useCallback(async () => {
    try {
      const [nextUsers, nextNotifications] = await Promise.all([api.users(), api.notificationStatus()]);
      setUsers(nextUsers);
      setNotifications(nextNotifications);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function updateRole(userId: string, role: AppUser["role"]) {
    setBusyUser(userId);
    try {
      await api.setUserRole(userId, role);
      await load();
      setMessage(t("admin.message.role_saved"));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyUser(null);
    }
  }

  async function retryFailures(outboxIds?: number[]) {
    setRetrying(true);
    try {
      const result = await api.retryFailedNotifications(outboxIds);
      await load();
      setMessage(`${t("admin.notifications.retried")}: ${result.retried}`);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRetrying(false);
    }
  }

  return (
    <main className="admin-workspace">
      <header className="workspace-header">
        <div>
          <h2>{t("admin.users")}</h2>
          <p>{t("admin.users_help")}</p>
        </div>
        <button onClick={() => void load()}>{t("admin.refresh")}</button>
      </header>
      {error && <div className="error-banner workspace-banner">{error}</div>}
      {message && <div className="success-banner workspace-banner">{message}</div>}

      {notifications && (
        <section className="notification-health">
          <div className="section-title">
            {t("admin.notifications.title")}
            {notifications.counts.FAILED > 0 && (
              <button disabled={retrying} onClick={() => void retryFailures()}>
                {t("admin.notifications.retry_all")}
              </button>
            )}
          </div>
          <div className="status-cards">
            <div><span>{t("admin.notifications.worker")}</span><strong>{notifications.worker_running ? t("common.active") : t("common.disabled")}</strong></div>
            <div><span>{t("admin.notifications.pending")}</span><strong>{notifications.counts.PENDING}</strong></div>
            <div><span>{t("admin.notifications.due")}</span><strong>{notifications.pending_due}</strong></div>
            <div><span>{t("admin.notifications.processing")}</span><strong>{notifications.counts.PROCESSING}</strong></div>
            <div><span>{t("admin.notifications.sent")}</span><strong>{notifications.counts.SENT}</strong></div>
            <div><span>{t("admin.notifications.failed")}</span><strong>{notifications.counts.FAILED}</strong></div>
          </div>
          {notifications.recent_failures.length > 0 && (
            <div className="admin-grid-wrap notification-failures">
              <table className="alarm-grid admin-grid">
                <thead><tr><th>{t("admin.notifications.alarm")}</th><th>{t("admin.notifications.event")}</th><th>{t("admin.notifications.route")}</th><th>{t("admin.notifications.attempts")}</th><th>{t("admin.notifications.error")}</th><th/></tr></thead>
                <tbody>{notifications.recent_failures.map((failure) => (
                  <tr key={failure.outbox_id}>
                    <td className="mono">{failure.alarm_id}</td>
                    <td>{failure.event_type}</td>
                    <td className="mono">{failure.route_key}</td>
                    <td>{failure.attempts}</td>
                    <td>{failure.last_error ?? "—"}</td>
                    <td><button disabled={retrying} onClick={() => void retryFailures([failure.outbox_id])}>{t("admin.notifications.retry")}</button></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <div className="admin-grid-wrap">
        <table className="alarm-grid admin-grid">
          <thead><tr><th>{t("admin.display_name")}</th><th>{t("admin.username")}</th><th>Home Assistant ID</th><th>{t("admin.role")}</th><th>{t("admin.locale")}</th><th>{t("admin.current_user")}</th></tr></thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.user_id}>
                <td>{user.display_name ?? "—"}</td>
                <td>{user.user_name ?? "—"}</td>
                <td className="mono">{user.user_id}</td>
                <td>
                  <select
                    value={user.role}
                    disabled={busyUser === user.user_id}
                    onChange={(event) => void updateRole(user.user_id, event.target.value as AppUser["role"])}
                  >
                    {ROLES.map((role) => <option key={role} value={role}>{t(`role.${role.toLowerCase()}`)}</option>)}
                  </select>
                </td>
                <td>{user.locale.toUpperCase()}</td>
                <td>{user.user_id === session.user_id ? "●" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {users.length === 0 && <div className="empty-state">{t("admin.no_users")}</div>}
      </div>
      <div className="admin-note">{t("admin.last_admin_note")}</div>
    </main>
  );
}
