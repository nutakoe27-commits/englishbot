import { useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  clearToken,
  getToken,
  setToken,
  type Metrics,
  type PaymentRecord,
  type UserBrief,
  type UserDetail,
} from "./api";
import {
  S,
  colors,
  fmtDate,
  fmtRub,
  fmtSeconds,
  userFullName,
} from "./ui";

// ─── Простейший hash-router: #/dashboard, #/users, #/user/123, #/settings ────
function useRoute(): [string, (r: string) => void] {
  const [route, setRoute] = useState<string>(
    () => window.location.hash.replace(/^#/, "") || "/dashboard"
  );
  useEffect(() => {
    const onHash = () =>
      setRoute(window.location.hash.replace(/^#/, "") || "/dashboard");
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const navigate = (to: string) => {
    window.location.hash = to;
  };
  return [route, navigate];
}

// ─── Root ────────────────────────────────────────────────────────────────────
export default function App() {
  const [authed, setAuthed] = useState<boolean>(() => !!getToken());
  const [checking, setChecking] = useState<boolean>(!!getToken());

  // При старте, если в localStorage есть токен — проверяем, что он живой.
  useEffect(() => {
    if (!getToken()) {
      setChecking(false);
      return;
    }
    (async () => {
      try {
        await api.me();
        setAuthed(true);
      } catch {
        clearToken();
        setAuthed(false);
      } finally {
        setChecking(false);
      }
    })();
  }, []);

  if (checking) {
    return (
      <div style={S.page}>
        <div style={{ ...S.container, paddingTop: 80, textAlign: "center" }}>
          <div style={S.muted}>Проверяем доступ…</div>
        </div>
      </div>
    );
  }

  if (!authed) {
    return <Login onAuthed={() => setAuthed(true)} />;
  }

  return <Shell onLogout={() => setAuthed(false)} />;
}

// ─── Login ───────────────────────────────────────────────────────────────────
function Login({ onAuthed }: { onAuthed: () => void }) {
  const [token, setT] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await api.me(token.trim());
      setToken(token.trim());
      onAuthed();
    } catch (e) {
      if (e instanceof ApiError) {
        setErr(
          e.status === 401
            ? "Неверный токен"
            : e.status === 503
            ? "ADMIN_TOKEN не задан на сервере (см. .env)"
            : e.message
        );
      } else {
        setErr("Не удалось связаться с сервером");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        ...S.page,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <form
        onSubmit={submit}
        style={{
          ...S.card,
          width: 360,
          margin: 0,
        }}
      >
        <h1 style={{ ...S.h2, marginBottom: 4 }}>🛠 Admin Panel</h1>
        <p style={{ ...S.muted, marginBottom: 20 }}>AI English Tutor</p>
        <label style={S.label}>ADMIN_TOKEN</label>
        <input
          style={S.input}
          type="password"
          value={token}
          onChange={(e) => setT(e.target.value)}
          placeholder="Токен из .env ADMIN_TOKEN"
          autoFocus
        />
        {err && <div style={{ ...S.error, marginTop: 12 }}>{err}</div>}
        <button
          type="submit"
          disabled={busy || !token.trim()}
          style={{
            ...S.btn,
            width: "100%",
            marginTop: 16,
            opacity: busy || !token.trim() ? 0.6 : 1,
          }}
        >
          {busy ? "Проверяем…" : "Войти"}
        </button>
      </form>
    </div>
  );
}

// ─── Shell (header + routes) ─────────────────────────────────────────────────
function Shell({ onLogout }: { onLogout: () => void }) {
  const [route, navigate] = useRoute();

  const logout = () => {
    clearToken();
    onLogout();
    navigate("/dashboard");
  };

  let view: JSX.Element;
  if (route.startsWith("/user/")) {
    const idStr = route.slice("/user/".length);
    const id = parseInt(idStr, 10);
    view = isNaN(id) ? (
      <UsersList />
    ) : (
      <UserPage id={id} onBack={() => navigate("/users")} />
    );
  } else if (route === "/users") {
    view = <UsersList />;
  } else if (route === "/settings") {
    view = <SettingsPage />;
  } else {
    view = <Dashboard />;
  }

  const navBtn = (path: string, label: string) => {
    const active =
      path === route || (path === "/users" && route.startsWith("/user/"));
    return (
      <button
        style={{
          ...S.navLink,
          ...(active ? S.navLinkActive : {}),
        }}
        onClick={() => navigate(path)}
      >
        {label}
      </button>
    );
  };

  return (
    <div style={S.page}>
      <header style={S.header}>
        <h1 style={S.headerTitle}>🛠 Admin · English Tutor</h1>
        <nav style={S.nav}>
          {navBtn("/dashboard", "Метрики")}
          {navBtn("/users", "Пользователи")}
          {navBtn("/settings", "Настройки")}
        </nav>
        <button style={S.btnSecondary} onClick={logout}>
          Выйти
        </button>
      </header>
      <div style={S.container}>{view}</div>
    </div>
  );
}

// ─── Dashboard ───────────────────────────────────────────────────────────────
function Dashboard() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [payments, setPayments] = useState<PaymentRecord[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    setErr(null);
    try {
      const [m, p] = await Promise.all([
        api.metrics(),
        api.recentPayments().catch(() => [] as PaymentRecord[]),
      ]);
      setMetrics(m);
      setPayments(p);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    load();
  }, []);

  if (err) return <div style={S.error}>{err}</div>;
  if (!metrics) return <div style={S.muted}>Загружаем метрики…</div>;

  const items: { label: string; value: string }[] = [
    { label: "Всего юзеров", value: metrics.total_users.toString() },
    { label: "Активных подписок", value: metrics.active_subscriptions.toString() },
    { label: "Заблокировано", value: metrics.blocked_users.toString() },
    { label: "DAU (сегодня)", value: metrics.dau.toString() },
    { label: "WAU (7 дн.)", value: metrics.wau.toString() },
    { label: "MAU (30 дн.)", value: metrics.mau.toString() },
    { label: "Минут сегодня", value: metrics.minutes_today.toString() },
    { label: "Выручка всего", value: fmtRub(metrics.total_revenue_rub) },
  ];

  return (
    <div>
      <h2 style={S.h2}>Метрики</h2>
      <div style={S.metricsGrid}>
        {items.map((it) => (
          <div key={it.label} style={S.metricCard}>
            <p style={S.metricValue}>{it.value}</p>
            <p style={S.metricLabel}>{it.label}</p>
          </div>
        ))}
      </div>

      <div style={{ ...S.card, marginTop: 20 }}>
        <h3 style={S.h3}>Последние платежи</h3>
        {!payments || payments.length === 0 ? (
          <div style={S.muted}>Пока ничего.</div>
        ) : (
          <table style={S.table}>
            <thead>
              <tr>
                <th style={S.th}>Когда</th>
                <th style={S.th}>TG ID</th>
                <th style={S.th}>Тариф</th>
                <th style={S.th}>Сумма</th>
                <th style={S.th}>Статус</th>
                <th style={S.th}>Заметки</th>
              </tr>
            </thead>
            <tbody>
              {payments.map((p) => (
                <tr key={p.id}>
                  <td style={S.td}>{fmtDate(p.created_at)}</td>
                  <td style={S.td}>{p.tg_id}</td>
                  <td style={S.td}>{p.plan}</td>
                  <td style={S.td}>{fmtRub(p.amount_rub)}</td>
                  <td style={S.td}>{p.status}</td>
                  <td style={S.td}>{p.notes ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ─── Users list ──────────────────────────────────────────────────────────────
function UsersList() {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<UserBrief[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [_, navigate] = useRoute();

  const load = async (query: string) => {
    setLoading(true);
    setErr(null);
    try {
      const list = await api.users(query);
      setItems(list);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load("");
  }, []);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    load(q);
  };

  return (
    <div>
      <h2 style={S.h2}>Пользователи</h2>
      <form onSubmit={submit} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <input
          style={{ ...S.input, flex: 1 }}
          placeholder="Поиск: имя, @username или tg_id"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button type="submit" style={S.btn} disabled={loading}>
          {loading ? "…" : "Найти"}
        </button>
      </form>

      {err && <div style={S.error}>{err}</div>}
      {items && (
        <div style={S.card}>
          {items.length === 0 ? (
            <div style={S.muted}>Никого не нашли.</div>
          ) : (
            <table style={S.table}>
              <thead>
                <tr>
                  <th style={S.th}>Имя</th>
                  <th style={S.th}>@username</th>
                  <th style={S.th}>TG ID</th>
                  <th style={S.th}>Подписка</th>
                  <th style={S.th}>Блок</th>
                  <th style={S.th}>Создан</th>
                </tr>
              </thead>
              <tbody>
                {items.map((u) => (
                  <tr
                    key={u.id}
                    style={S.rowClickable}
                    onClick={() => navigate(`/user/${u.id}`)}
                  >
                    <td style={S.td}>{userFullName(u)}</td>
                    <td style={S.td}>{u.username ? `@${u.username}` : "—"}</td>
                    <td style={S.td}>{u.tg_id}</td>
                    <td style={S.td}>
                      {u.has_subscription ? (
                        <span
                          style={{
                            ...S.badge,
                            backgroundColor: colors.successBg,
                            color: colors.success,
                          }}
                        >
                          до {fmtDate(u.subscription_until)}
                        </span>
                      ) : (
                        <span style={{ ...S.badge, color: colors.textMuted }}>
                          нет
                        </span>
                      )}
                    </td>
                    <td style={S.td}>
                      {u.is_blocked ? (
                        <span
                          style={{
                            ...S.badge,
                            backgroundColor: colors.dangerBg,
                            color: colors.danger,
                          }}
                        >
                          заблокирован
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td style={S.td}>{fmtDate(u.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

// ─── User detail ─────────────────────────────────────────────────────────────
function UserPage({ id, onBack }: { id: number; onBack: () => void }) {
  const [u, setU] = useState<UserDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    setErr(null);
    try {
      setU(await api.user(id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  useEffect(() => {
    load();
  }, [id]);

  if (err) return <div style={S.error}>{err}</div>;
  if (!u) return <div style={S.muted}>Загружаем…</div>;

  const withToast = async (fn: () => Promise<UserDetail>, success: string) => {
    setErr(null);
    setMsg(null);
    try {
      setU(await fn());
      setMsg(success);
      setTimeout(() => setMsg(null), 2500);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div>
      <button style={{ ...S.btnSecondary, marginBottom: 12 }} onClick={onBack}>
        ← К списку
      </button>
      <h2 style={S.h2}>{userFullName(u)}</h2>
      <div style={S.muted}>
        tg_id {u.tg_id}
        {u.username ? ` · @${u.username}` : ""} · язык {u.language_code || "—"}{" "}
        · создан {fmtDate(u.created_at)}
      </div>

      {msg && <div style={{ ...S.success, marginTop: 12 }}>{msg}</div>}
      {err && <div style={{ ...S.error, marginTop: 12 }}>{err}</div>}

      {/* ── Статус ──────────────────────── */}
      <div style={{ ...S.card, marginTop: 16 }}>
        <h3 style={S.h3}>Статус</h3>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <StatusPill
            label="Подписка"
            value={
              u.has_subscription
                ? `до ${fmtDate(u.subscription_until)}`
                : "нет"
            }
            tone={u.has_subscription ? "success" : "muted"}
          />
          <StatusPill
            label="Блокировка"
            value={u.is_blocked ? "заблокирован" : "активен"}
            tone={u.is_blocked ? "danger" : "success"}
          />
          <StatusPill
            label="Сегодня"
            value={`${fmtSeconds(u.used_seconds_today)} / ${fmtSeconds(
              u.free_seconds_per_day
            )}`}
            tone="muted"
          />
          <StatusPill
            label="Напоминание"
            value={
              u.reminder_enabled ? `в ${u.reminder_hour_msk.toString().padStart(2, "0")}:00 МСК` : "выключено"
            }
            tone={u.reminder_enabled ? "success" : "muted"}
          />
        </div>
      </div>

      {/* ── Подписка ────────────────────── */}
      <GrantCard userId={u.id} onDone={(nu) => setU(nu)} />

      {/* ── Блокировка ──────────────────── */}
      <div style={S.card}>
        <h3 style={S.h3}>Блокировка</h3>
        <p style={S.muted}>
          Заблокированный пользователь не сможет вести сессии и использовать бота.
        </p>
        {u.is_blocked ? (
          <button
            style={S.btn}
            onClick={() =>
              withToast(() => api.block(u.id, false), "Разблокирован")
            }
          >
            Разблокировать
          </button>
        ) : (
          <button
            style={S.btnDanger}
            onClick={() =>
              withToast(() => api.block(u.id, true), "Заблокирован")
            }
          >
            Заблокировать
          </button>
        )}
      </div>

      {/* ── Напоминание ─────────────────── */}
      <ReminderCard user={u} onDone={(nu) => setU(nu)} />
    </div>
  );
}

function StatusPill({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "success" | "danger" | "muted" | "warning";
}) {
  const palette = {
    success: { bg: colors.successBg, fg: colors.success },
    danger: { bg: colors.dangerBg, fg: colors.danger },
    warning: { bg: colors.warningBg, fg: colors.warning },
    muted: { bg: "#f3f4f6", fg: colors.textMuted },
  }[tone];
  return (
    <div>
      <div style={{ ...S.muted, fontSize: 12, marginBottom: 2 }}>{label}</div>
      <span
        style={{
          ...S.badge,
          backgroundColor: palette.bg,
          color: palette.fg,
          fontSize: 13,
          padding: "4px 10px",
        }}
      >
        {value}
      </span>
    </div>
  );
}

function GrantCard({
  userId,
  onDone,
}: {
  userId: number;
  onDone: (u: UserDetail) => void;
}) {
  const [days, setDays] = useState<number>(30);
  const [plan, setPlan] = useState<string>("admin_grant");
  const [amount, setAmount] = useState<number>(0);
  const [notes, setNotes] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (days < 1) return;
    setBusy(true);
    setErr(null);
    setOk(null);
    try {
      const u = await api.grant(userId, {
        days,
        plan,
        amount_rub: amount,
        notes: notes.trim() || undefined,
      });
      onDone(u);
      setOk(`+${days} дней подписки выдано`);
      setNotes("");
      setTimeout(() => setOk(null), 2500);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const quick = [7, 30, 90, 365];

  return (
    <form onSubmit={submit} style={S.card}>
      <h3 style={S.h3}>Продлить / подарить подписку</h3>
      {ok && <div style={S.success}>{ok}</div>}
      {err && <div style={S.error}>{err}</div>}

      <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
        {quick.map((d) => (
          <button
            type="button"
            key={d}
            onClick={() => setDays(d)}
            style={{
              ...S.btnSecondary,
              ...(days === d
                ? {
                    borderColor: colors.primary,
                    color: colors.primary,
                    fontWeight: 600,
                  }
                : {}),
            }}
          >
            +{d} дн.
          </button>
        ))}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr 1fr",
          gap: 12,
          marginBottom: 12,
        }}
      >
        <div>
          <label style={S.label}>Дней</label>
          <input
            style={S.input}
            type="number"
            min={1}
            max={3650}
            value={days}
            onChange={(e) => setDays(parseInt(e.target.value || "0", 10))}
          />
        </div>
        <div>
          <label style={S.label}>Тип</label>
          <select
            style={S.input}
            value={plan}
            onChange={(e) => setPlan(e.target.value)}
          >
            <option value="admin_grant">admin_grant (обычное продление)</option>
            <option value="gift">gift (подарок)</option>
            <option value="manual_pay">manual_pay (ручная оплата)</option>
          </select>
        </div>
        <div>
          <label style={S.label}>Сумма ₽ (если была оплата)</label>
          <input
            style={S.input}
            type="number"
            min={0}
            step={0.01}
            value={amount}
            onChange={(e) => setAmount(parseFloat(e.target.value || "0"))}
          />
        </div>
      </div>
      <div style={{ marginBottom: 12 }}>
        <label style={S.label}>Заметка (опционально)</label>
        <input
          style={S.input}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Причина, номер тикета и т.п."
        />
      </div>
      <button type="submit" style={S.btn} disabled={busy || days < 1}>
        {busy ? "…" : "Выдать"}
      </button>
    </form>
  );
}

function ReminderCard({
  user,
  onDone,
}: {
  user: UserDetail;
  onDone: (u: UserDetail) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const update = async (body: { enabled?: boolean; hour_msk?: number }) => {
    setBusy(true);
    setErr(null);
    try {
      onDone(await api.reminder(user.id, body));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const hours = useMemo(() => [8, 10, 12, 15, 18, 19, 20, 21], []);

  return (
    <div style={S.card}>
      <h3 style={S.h3}>Напоминание</h3>
      {err && <div style={S.error}>{err}</div>}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
        <label style={{ fontSize: 14 }}>
          <input
            type="checkbox"
            checked={user.reminder_enabled}
            disabled={busy}
            onChange={(e) => update({ enabled: e.target.checked })}
          />{" "}
          включено
        </label>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {hours.map((h) => (
          <button
            key={h}
            type="button"
            disabled={busy}
            onClick={() => update({ enabled: true, hour_msk: h })}
            style={{
              ...S.btnSecondary,
              ...(user.reminder_enabled && user.reminder_hour_msk === h
                ? {
                    borderColor: colors.primary,
                    color: colors.primary,
                    fontWeight: 600,
                  }
                : {}),
            }}
          >
            {h.toString().padStart(2, "0")}:00
          </button>
        ))}
      </div>
    </div>
  );
}

// ─── Settings ────────────────────────────────────────────────────────────────
function SettingsPage() {
  const [enabled, setEnabled] = useState(false);
  const [message, setMessage] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const s = await api.maintenance();
        setEnabled(s.enabled);
        setMessage(s.message || "");
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setLoaded(true);
      }
    })();
  }, []);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setOk(null);
    try {
      const s = await api.setMaintenance({ enabled, message });
      setEnabled(s.enabled);
      setMessage(s.message);
      setOk("Сохранено");
      setTimeout(() => setOk(null), 2500);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!loaded) return <div style={S.muted}>Загружаем…</div>;

  return (
    <div>
      <h2 style={S.h2}>Настройки</h2>
      <form onSubmit={save} style={S.card}>
        <h3 style={S.h3}>Режим технических работ</h3>
        <p style={S.muted}>
          Когда включён — боту и mini app показывается сообщение ниже, новые
          WS-сессии не стартуют.
        </p>
        {ok && <div style={{ ...S.success, marginTop: 8 }}>{ok}</div>}
        {err && <div style={{ ...S.error, marginTop: 8 }}>{err}</div>}

        <label
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            fontSize: 14,
            margin: "12px 0",
          }}
        >
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Включить режим тех.работ
        </label>

        <label style={S.label}>Сообщение пользователям</label>
        <textarea
          style={{ ...S.input, minHeight: 90, resize: "vertical" }}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Бот на техработах, вернёмся через 30 минут."
        />

        <div style={{ marginTop: 12 }}>
          <button type="submit" style={S.btn} disabled={busy}>
            {busy ? "Сохраняем…" : "Сохранить"}
          </button>
        </div>
      </form>
    </div>
  );
}
