import { useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  clearToken,
  getToken,
  setToken,
  type BroadcastJobStatus,
  type ChartPoint,
  type Metrics,
  type OnlineResponse,
  type RetentionCohort,
  type UserBrief,
  type UserDetail,
  type UserSession,
} from "./api";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";
import {
  S,
  colors,
  fmtDate,
  fmtRub,
  fmtSeconds,
  userFullName,
} from "./ui";
import { useIsMobile } from "./useIsMobile";

// На узких экранах делаем саму таблицу горизонтально прокручиваемой
// (display:block + overflow-x), чтобы колонки не сжимались до нечитаемости.
// whiteSpace:nowrap не даёт ячейкам переноситься — таблица скроллится целиком.
function tableStyle(isMobile: boolean, extra?: React.CSSProperties): React.CSSProperties {
  const base = { ...S.table, ...(extra ?? {}) };
  if (!isMobile) return base;
  return {
    ...base,
    display: "block",
    overflowX: "auto",
    whiteSpace: "nowrap",
    WebkitOverflowScrolling: "touch",
  };
}

// Сетка метрик: на мобиле уже минимальная ширина колонки → 2 в ряд на телефоне.
function metricsGridStyle(isMobile: boolean): React.CSSProperties {
  return {
    ...S.metricsGrid,
    gridTemplateColumns: isMobile
      ? "repeat(auto-fit, minmax(130px, 1fr))"
      : "repeat(auto-fit, minmax(170px, 1fr))",
  };
}

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
        padding: 16,
      }}
    >
      <form
        onSubmit={submit}
        style={{
          ...S.card,
          width: "100%",
          maxWidth: 360,
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
  const isMobile = useIsMobile();

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
  } else if (route === "/broadcast") {
    view = <BroadcastPage />;
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
          whiteSpace: "nowrap",
          flexShrink: 0,
        }}
        onClick={() => navigate(path)}
      >
        {label}
      </button>
    );
  };

  return (
    <div style={S.page}>
      <header
        style={{
          ...S.header,
          ...(isMobile
            ? { flexDirection: "column", alignItems: "stretch", gap: 10, padding: "12px 14px" }
            : {}),
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
          }}
        >
          <h1 style={{ ...S.headerTitle, ...(isMobile ? { fontSize: 16 } : {}) }}>
            🛠 Admin · English Tutor
          </h1>
          {isMobile && (
            <button style={S.btnSecondary} onClick={logout}>
              Выйти
            </button>
          )}
        </div>
        <nav
          style={{ ...S.nav, ...(isMobile ? { gap: 6, flex: "none" } : {}) }}
          className={isMobile ? "adm-nav-scroll" : undefined}
        >
          {navBtn("/dashboard", "Метрики")}
          {navBtn("/users", "Пользователи")}
          {navBtn("/broadcast", "Массовые")}
          {navBtn("/settings", "Настройки")}
        </nav>
        {!isMobile && (
          <button style={S.btnSecondary} onClick={logout}>
            Выйти
          </button>
        )}
      </header>
      <div
        style={{
          ...S.container,
          ...(isMobile ? { padding: 14 } : {}),
        }}
      >
        {view}
      </div>
    </div>
  );
}

// ─── Режимы: метки и бейджи ──────────────────────────────────────────────────
const MODE_META: Record<string, { label: string; emoji: string }> = {
  voice: { label: "Разговор", emoji: "🎙" },
  chat: { label: "Чат", emoji: "💬" },
  listening: { label: "Слушание", emoji: "🎧" },
  grammar: { label: "Грамматика", emoji: "📝" },
  srs: { label: "Слова", emoji: "📚" },
};

function modeMeta(mode: string): { label: string; emoji: string } {
  return MODE_META[mode] ?? { label: mode, emoji: "•" };
}

// Провайдеры входа → компактные иконки для списка/детали.
const PROVIDER_EMOJI: Record<string, string> = {
  telegram: "✈️",
  native: "✉️",
  vk: "🔵",
};
function authProvidersLabel(providers?: string[]): string {
  if (!providers || providers.length === 0) return "—";
  return providers.map((p) => PROVIDER_EMOJI[p] ?? p).join(" ");
}

function modeBadgeStyle(mode: string): React.CSSProperties {
  if (mode === "voice") return S.modeBadgeVoice;
  if (mode === "listening") return S.modeBadgeListening;
  if (mode === "grammar") return S.modeBadgeGrammar;
  return S.modeBadgeChat;
}

// ─── Онлайн-панель: кто сейчас занимается (автообновление 5с) ─────────────────
function OnlinePanel({ onOpenUser }: { onOpenUser: (id: number) => void }) {
  const isMobile = useIsMobile();
  const [online, setOnline] = useState<OnlineResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await api.online();
        if (!cancelled) {
          setOnline(r);
          setErr(null);
        }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const count = online?.count ?? 0;
  const bm = online?.by_mode ?? {};

  return (
    <div style={{ ...S.card, marginBottom: 20 }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 14,
          flexWrap: "wrap",
        }}
      >
        <h3 style={{ ...S.h3, margin: 0 }}>
          🟢 Сейчас занимается: {count}
        </h3>
        <span style={S.muted}>
          {modeMeta("voice").emoji} {bm.voice ?? 0} ·{" "}
          {modeMeta("chat").emoji} {bm.chat ?? 0} ·{" "}
          {modeMeta("listening").emoji} {bm.listening ?? 0} ·{" "}
          {modeMeta("grammar").emoji} {bm.grammar ?? 0} ·{" "}
          {modeMeta("srs").emoji} {bm.srs ?? 0}
        </span>
      </div>

      {err && <div style={{ ...S.muted, marginTop: 8 }}>Не удалось обновить: {err}</div>}

      {count === 0 ? (
        <div style={{ ...S.muted, marginTop: 12 }}>Сейчас никого нет онлайн.</div>
      ) : (
        <table style={tableStyle(isMobile, { marginTop: 12 })}>
          <thead>
            <tr>
              <th style={S.th}>Юзер</th>
              <th style={S.th}>Режим</th>
              <th style={S.th}>Уровень</th>
              <th style={S.th}>Тема/роль</th>
              <th style={S.th}>В сессии</th>
            </tr>
          </thead>
          <tbody>
            {(online?.sessions ?? []).map((s) => (
              <tr key={s.user_id}>
                <td style={S.td}>
                  <a
                    href={`#/user/${s.user_id}`}
                    onClick={(e) => {
                      e.preventDefault();
                      onOpenUser(s.user_id);
                    }}
                    style={{ color: colors.primary, cursor: "pointer" }}
                  >
                    {s.username
                      ? `@${s.username}`
                      : s.first_name || `#${s.user_id}`}
                  </a>
                </td>
                <td style={S.td}>
                  <span style={modeBadgeStyle(s.mode)}>
                    {modeMeta(s.mode).emoji} {modeMeta(s.mode).label}
                  </span>
                </td>
                <td style={S.td}>{s.level || "—"}</td>
                <td style={S.td}>{s.role || "—"}</td>
                <td style={S.td}>{fmtSeconds(s.duration_sec)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ─── Карточка «Режимы сегодня» + топ listening-категорий ─────────────────────
function ModesTodayCard({ metrics }: { metrics: Metrics }) {
  const isMobile = useIsMobile();
  const modes = metrics.modes_today ?? {};
  const order = ["voice", "chat", "listening", "grammar", "srs"];
  const topCats = metrics.listening_top_categories ?? [];

  return (
    <div style={{ ...S.card, marginTop: 20 }}>
      <h3 style={S.h3}>Режимы сегодня</h3>
      <div style={metricsGridStyle(isMobile)}>
        {order.map((mode) => {
          const stat = modes[mode] ?? { sessions: 0, minutes: 0 };
          const meta = modeMeta(mode);
          return (
            <div key={mode} style={S.metricCard}>
              <p style={S.metricValue}>
                {meta.emoji} {stat.sessions}
              </p>
              <p style={S.metricLabel}>
                {meta.label} · {stat.minutes} мин
              </p>
            </div>
          );
        })}
      </div>

      {topCats.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <p style={{ ...S.metricLabel, marginBottom: 8 }}>
            🎧 Топ тем подкастов (7 дней)
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {topCats.map((c) => (
              <span key={c.category} style={S.modeBadgeListening}>
                {c.category} · {c.count}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Карточка «Средний активный юзер» (заходил более 2 раз) ──────────────────
function ActiveAvgCard({ metrics }: { metrics: Metrics }) {
  const isMobile = useIsMobile();
  // Основной показатель — за 30 дней (живая активность); фолбэк — за всё время.
  const avg30 = metrics.active_avg_30d;
  const avgAll = metrics.active_avg;
  const primary = avg30 && avg30.active_users > 0 ? avg30 : avgAll;
  if (!primary || primary.active_users === 0) return null;
  const isWindow = primary === avg30;

  const order = ["speaking", "listening", "grammar", "srs"];
  const bm = primary.by_mode_minutes ?? {};

  return (
    <div style={{ ...S.card, marginTop: 20 }}>
      <h3 style={S.h3}>Средний активный юзер</h3>
      <p style={{ ...S.metricLabel, marginTop: -6, marginBottom: 14 }}>
        По {primary.active_users} юзерам, заходившим более 2 раз
        {isWindow ? " за 30 дней" : " (за всё время)"} · в среднем{" "}
        <b>{primary.avg_minutes_total} мин</b> на человека
      </p>
      <div style={metricsGridStyle(isMobile)}>
        {order.map((mode) => {
          const meta = modeMeta(mode);
          return (
            <div key={mode} style={S.metricCard}>
              <p style={S.metricValue}>
                {meta.emoji} {bm[mode] ?? 0}
              </p>
              <p style={S.metricLabel}>{meta.label} · мин/чел</p>
            </div>
          );
        })}
      </div>
      {isWindow && avgAll && avgAll.active_users > 0 && (
        <p style={{ ...S.metricLabel, marginTop: 12 }}>
          За всё время: <b>{avgAll.avg_minutes_total} мин</b>/чел ·{" "}
          {avgAll.active_users} активных юзеров
        </p>
      )}
    </div>
  );
}

// ─── Dashboard ───────────────────────────────────────────────────────────────
function Dashboard() {
  const isMobile = useIsMobile();
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [dauSeries, setDauSeries] = useState<ChartPoint[] | null>(null);
  const [revSeries, setRevSeries] = useState<ChartPoint[] | null>(null);
  const [newUsersSeries, setNewUsersSeries] = useState<ChartPoint[] | null>(null);
  const [retention, setRetention] = useState<RetentionCohort[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [_dashRoute, dashNavigate] = useRoute();

  const load = async () => {
    setErr(null);
    try {
      // Графики и retention тянем параллельно с основными метриками — их
      // ошибки не должны убивать основной дашборд (catch → пустые массивы).
      const [m, dau, rev, nu, ret] = await Promise.all([
        api.metrics(),
        api.chartSeries("dau", 30).catch(() => ({ series: [] as ChartPoint[] })),
        api.chartSeries("revenue", 30).catch(() => ({ series: [] as ChartPoint[] })),
        api.chartSeries("new-users", 30).catch(() => ({ series: [] as ChartPoint[] })),
        api.retention(30).catch(() => ({ cohorts: [] as RetentionCohort[] })),
      ]);
      setMetrics(m);
      setDauSeries(dau.series);
      setRevSeries(rev.series);
      setNewUsersSeries(nu.series);
      setRetention(ret.cohorts);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    load();
  }, []);

  if (err) return <div style={S.error}>{err}</div>;
  if (!metrics) return <div style={S.muted}>Загружаем метрики…</div>;

  // Берём всё через num() — если backend вдруг вернёт null/undefined,
  // не падаем, а показываем 0.
  const num = (v: number | null | undefined): string =>
    (v == null ? 0 : v).toString();
  const items: { label: string; value: string }[] = [
    { label: "В Mini App всего", value: num(metrics.total_users) },
    { label: "Активировали бота", value: num(metrics.bot_activated_total) },
    { label: "Новых в Mini App сегодня", value: num(metrics.new_users_today) },
    { label: "Активаций бота сегодня", value: num(metrics.bot_activated_today) },
    { label: "Активных подписок", value: num(metrics.active_subscriptions) },
    { label: "Заблокировано", value: num(metrics.blocked_users) },
    { label: "DAU (сегодня)", value: num(metrics.dau) },
    { label: "WAU (7 дн.)", value: num(metrics.wau) },
    { label: "MAU (30 дн.)", value: num(metrics.mau) },
    { label: "Минут сегодня", value: num(metrics.minutes_today) },
    { label: "Выручка всего", value: fmtRub(metrics.total_revenue_rub ?? 0) },
  ];

  return (
    <div>
      <OnlinePanel onOpenUser={(uid) => dashNavigate(`/user/${uid}`)} />

      <h2 style={S.h2}>Метрики</h2>
      <div style={metricsGridStyle(isMobile)}>
        {items.map((it) => (
          <div key={it.label} style={S.metricCard}>
            <p style={S.metricValue}>{it.value}</p>
            <p style={S.metricLabel}>{it.label}</p>
          </div>
        ))}
      </div>

      <ModesTodayCard metrics={metrics} />
      <ActiveAvgCard metrics={metrics} />

      <ChartBlock
        title="DAU за 30 дней"
        data={dauSeries}
        color={colors.primary}
        kind="line"
      />
      <ChartBlock
        title="Выручка за 30 дней (₽)"
        data={revSeries}
        color={colors.success}
        kind="bar"
        valueFormatter={(v) => `${v.toLocaleString("ru-RU")} ₽`}
      />
      <ChartBlock
        title="Новые юзеры за 30 дней"
        data={newUsersSeries}
        color={colors.warning}
        kind="bar"
      />

      <RetentionTable data={retention} />
    </div>
  );
}

// ─── Чарты и retention для дашборда ──────────────────────────────────────────

function ChartBlock({
  title,
  data,
  color,
  kind,
  valueFormatter,
}: {
  title: string;
  data: ChartPoint[] | null;
  color: string;
  kind: "line" | "bar";
  valueFormatter?: (v: number) => string;
}) {
  if (data === null) {
    return (
      <div style={S.chartCard}>
        <h3 style={S.chartTitle}>{title}</h3>
        <div style={S.muted}>Загрузка…</div>
      </div>
    );
  }
  if (data.length === 0) {
    return (
      <div style={S.chartCard}>
        <h3 style={S.chartTitle}>{title}</h3>
        <div style={S.muted}>Нет данных</div>
      </div>
    );
  }
  // Короткая ось X: ДД.ММ, потому что 30 точек YYYY-MM-DD не влезает.
  const formatted = data.map((p) => ({
    ...p,
    label: p.date.slice(5).replace("-", "."),
  }));
  return (
    <div style={S.chartCard}>
      <h3 style={S.chartTitle}>{title}</h3>
      <div style={{ width: "100%", height: 220 }}>
        <ResponsiveContainer>
          {kind === "line" ? (
            <LineChart data={formatted} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
              <CartesianGrid stroke={colors.border} strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: colors.textMuted }} />
              <YAxis tick={{ fontSize: 11, fill: colors.textMuted }} allowDecimals={false} />
              <Tooltip
                formatter={(v: number) =>
                  valueFormatter ? valueFormatter(v) : String(v)
                }
                labelStyle={{ color: colors.text }}
              />
              <Line
                type="monotone"
                dataKey="value"
                stroke={color}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          ) : (
            <BarChart data={formatted} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
              <CartesianGrid stroke={colors.border} strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: colors.textMuted }} />
              <YAxis tick={{ fontSize: 11, fill: colors.textMuted }} allowDecimals={false} />
              <Tooltip
                formatter={(v: number) =>
                  valueFormatter ? valueFormatter(v) : String(v)
                }
                labelStyle={{ color: colors.text }}
              />
              <Bar dataKey="value" fill={color} isAnimationActive={false} />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function RetentionTable({ data }: { data: RetentionCohort[] | null }) {
  const isMobile = useIsMobile();
  if (data === null) {
    return (
      <div style={S.chartCard}>
        <h3 style={S.chartTitle}>Retention по cohort'ам (D1 / D7 / D30)</h3>
        <div style={S.muted}>Загрузка…</div>
      </div>
    );
  }
  if (data.length === 0) {
    return (
      <div style={S.chartCard}>
        <h3 style={S.chartTitle}>Retention по cohort'ам (D1 / D7 / D30)</h3>
        <div style={S.muted}>Нет данных</div>
      </div>
    );
  }
  const cell = (val: number | null, size: number) => {
    if (val === null) return "—";
    const pct = size > 0 ? Math.round((val / size) * 100) : 0;
    return `${val} (${pct}%)`;
  };
  return (
    <div style={S.chartCard}>
      <h3 style={S.chartTitle}>Retention по cohort'ам (D1 / D7 / D30)</h3>
      <table style={tableStyle(isMobile)}>
        <thead>
          <tr>
            <th style={S.th}>Cohort</th>
            <th style={S.th}>Size</th>
            <th style={S.th}>D1</th>
            <th style={S.th}>D7</th>
            <th style={S.th}>D30</th>
          </tr>
        </thead>
        <tbody>
          {data.map((c) => (
            <tr key={c.cohort_date}>
              <td style={S.td}>{c.cohort_date}</td>
              <td style={S.td}>{c.size}</td>
              <td style={S.td}>{cell(c.d1, c.size)}</td>
              <td style={S.td}>{cell(c.d7, c.size)}</td>
              <td style={S.td}>{cell(c.d30, c.size)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Users list ──────────────────────────────────────────────────────────────
const USERS_PAGE = 50;

function UsersList() {
  const isMobile = useIsMobile();
  const [q, setQ] = useState("");
  // appliedQ — текущий "зафиксированный" запрос, по которому уже идёт
  // пагинация. Меняем только при submit/Enter, чтобы не дёргать запрос
  // на каждый keypress.
  const [appliedQ, setAppliedQ] = useState("");
  const [items, setItems] = useState<UserBrief[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [_, navigate] = useRoute();

  const loadPage = async (query: string, offset: number) => {
    setLoading(true);
    setErr(null);
    try {
      const list = await api.users(query, USERS_PAGE, offset);
      const batch = Array.isArray(list) ? list : [];
      setItems((prev) => {
        const base = offset === 0 || prev === null ? [] : prev;
        return [...base, ...batch];
      });
      setHasMore(batch.length === USERS_PAGE);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPage("", 0);
  }, []);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setAppliedQ(q);
    setItems([]);
    setHasMore(true);
    loadPage(q, 0);
  };

  const loadMore = () => {
    if (loading || !hasMore || items === null) return;
    loadPage(appliedQ, items.length);
  };

  return (
    <div>
      <h2 style={S.h2}>Пользователи</h2>
      <form onSubmit={submit} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <input
          style={{ ...S.input, flex: 1 }}
          placeholder="Поиск: имя, @username, tg_id или email"
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
            <table style={tableStyle(isMobile)}>
              <thead>
                <tr>
                  <th style={S.th}>Имя</th>
                  <th style={S.th}>@username</th>
                  <th style={S.th}>TG ID</th>
                  <th style={S.th}>Вход</th>
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
                    <td style={S.td}>{u.tg_id ?? "—"}</td>
                    <td style={S.td}>{authProvidersLabel(u.auth_providers)}</td>
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
          {items.length > 0 && (
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                marginTop: 12,
                gap: 12,
                alignItems: "center",
              }}
            >
              <span style={S.muted}>Показано: {items.length}</span>
              {hasMore && (
                <button
                  style={S.btnSecondary}
                  onClick={loadMore}
                  disabled={loading}
                >
                  {loading ? "Загружаем…" : "Загрузить ещё"}
                </button>
              )}
            </div>
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
  const [tab, setTab] = useState<"overview" | "sessions">("overview");

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
        tg_id {u.tg_id ?? "—"}
        {u.username ? ` · @${u.username}` : ""} · язык {u.language_code || "—"}{" "}
        · создан {fmtDate(u.created_at)}
      </div>
      <div style={{ ...S.muted, marginTop: 4 }}>
        Вход: {authProvidersLabel(u.auth_providers)}
        {u.auth_providers && u.auth_providers.length
          ? ` (${u.auth_providers.join(", ")})`
          : ""}
        {u.email ? ` · ${u.email}` : ""}
      </div>

      {msg && <div style={{ ...S.success, marginTop: 12 }}>{msg}</div>}
      {err && <div style={{ ...S.error, marginTop: 12 }}>{err}</div>}

      <div style={{ ...S.tabs, marginTop: 16 }}>
        <button
          style={tab === "overview" ? S.tabActive : S.tab}
          onClick={() => setTab("overview")}
        >
          Обзор
        </button>
        <button
          style={tab === "sessions" ? S.tabActive : S.tab}
          onClick={() => setTab("sessions")}
        >
          Сессии
        </button>
      </div>

      {tab === "sessions" && <SessionsTab userId={id} />}
      {tab === "overview" && (
        <>
      {/* ── Статус ──────────────────────── */}
      <div style={{ ...S.card, marginTop: 0 }}>
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
            label="Бонус сегодня"
            value={fmtSeconds(u.bonus_seconds_today ?? 0)}
            tone={(u.bonus_seconds_today ?? 0) > 0 ? "success" : "muted"}
          />
          <StatusPill
            label="Практика всего"
            value={fmtSeconds(u.used_seconds_total ?? 0)}
            tone="muted"
          />
          <StatusPill
            label="Напоминание"
            value={
              u.reminder_enabled
                ? `в ${(u.reminder_hour_msk ?? 19).toString().padStart(2, "0")}:00 МСК`
                : "выключено"
            }
            tone={u.reminder_enabled ? "success" : "muted"}
          />
        </div>
      </div>

      {/* ── Активность (стрик / режимы / словарь / медали) ── */}
      <div style={{ ...S.card, marginTop: 16 }}>
        <h3 style={S.h3}>Активность</h3>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <StatusPill
            label="Стрик"
            value={
              (u.streak_current ?? 0) > 0
                ? `🔥 ${u.streak_current} дн. · рекорд ${u.streak_best ?? 0}`
                : `нет · рекорд ${u.streak_best ?? 0}`
            }
            tone={(u.streak_current ?? 0) > 0 ? "success" : "muted"}
          />
          <StatusPill
            label="Последняя практика"
            value={u.last_practice_date ? fmtDate(u.last_practice_date) : "—"}
            tone="muted"
          />
          <StatusPill
            label="Слов в словаре"
            value={(u.words_count ?? 0).toString()}
            tone={(u.words_count ?? 0) > 0 ? "success" : "muted"}
          />
          <StatusPill
            label="📝 Темы грамматики"
            value={`${u.grammar_topics_done ?? 0} / ${u.grammar_topics_total ?? 0}`}
            tone={(u.grammar_topics_done ?? 0) > 0 ? "success" : "muted"}
          />
          <StatusPill
            label="Медали"
            value={`${u.achievements_earned ?? 0} / ${u.achievements_total ?? 0}`}
            tone={(u.achievements_earned ?? 0) > 0 ? "success" : "muted"}
          />
        </div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 12 }}>
          {["voice", "chat", "listening", "grammar", "srs"].map((mode) => {
            const mins = (u.minutes_by_mode ?? {})[mode] ?? 0;
            const meta = modeMeta(mode);
            return (
              <StatusPill
                key={mode}
                label={`${meta.emoji} ${meta.label}`}
                value={`${mins} мин`}
                tone={mins > 0 ? "success" : "muted"}
              />
            );
          })}
        </div>
      </div>

      <GrantCard userId={u.id} onDone={(nu) => setU(nu)} />

      {/* ── Написать сообщение ───────────── */}
      <SendMessageCard user={u} />

      {/* ── Блокировка ──────────────── */}
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

      {/* ── Удаление аккаунта (необратимо) ─── */}
      <DeleteAccountCard user={u} onDeleted={onBack} />
        </>
      )}
    </div>
  );
}

// ─── Sessions tab (на UserPage) ──────────────────────────────────────────────

function SessionsTab({ userId }: { userId: number }) {
  const isMobile = useIsMobile();
  const [sessions, setSessions] = useState<UserSession[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSessions(null);
    setErr(null);
    api
      .userSessions(userId, 30)
      .then((r) => {
        if (!cancelled) setSessions(r.sessions);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setErr(e instanceof Error ? e.message : String(e));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  if (err) return <div style={S.error}>{err}</div>;
  if (sessions === null) return <div style={S.muted}>Загружаем…</div>;
  if (sessions.length === 0) {
    return (
      <div style={S.card}>
        <div style={S.muted}>Юзер ещё не занимался.</div>
      </div>
    );
  }

  return (
    <div style={S.card}>
      <h3 style={S.h3}>Последние сессии</h3>
      <table style={tableStyle(isMobile)}>
        <thead>
          <tr>
            <th style={S.th}>Дата/время</th>
            <th style={S.th}>Длительность</th>
            <th style={S.th}>Режим</th>
            <th style={S.th}>Уровень</th>
            <th style={S.th}>Роль</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((s) => (
            <tr key={s.id}>
              <td style={S.td}>{fmtDate(s.started_at)}</td>
              <td style={S.td}>
                {fmtSeconds(s.used_seconds)}
                {s.ended_at === null && (
                  <span style={{ ...S.muted, marginLeft: 6 }}>(в процессе)</span>
                )}
              </td>
              <td style={S.td}>
                <span style={modeBadgeStyle(s.mode)}>
                  {modeMeta(s.mode).emoji} {modeMeta(s.mode).label}
                </span>
              </td>
              <td style={S.td}>{s.level || "—"}</td>
              <td style={S.td}>{s.role || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SendMessageCard({ user }: { user: UserDetail }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    if (!user?.id) {
      setErr("Не удалось определить id юзера — обновите страницу.");
      return;
    }
    setBusy(true);
    setErr(null);
    setOk(null);
    try {
      await api.sendUserMessage(user.id, t);
      setOk("Сообщение доставлено");
      setText("");
      setTimeout(() => {
        setOk(null);
        setOpen(false);
      }, 1500);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={S.card}>
      <h3 style={S.h3}>Написать сообщение</h3>
      <p style={S.muted}>
        Бот отправит сообщение напрямую этому пользователю. Поддерживается HTML.
      </p>
      <button
        type="button"
        style={S.btn}
        onClick={() => {
          setOpen(true);
          setErr(null);
          setOk(null);
        }}
        disabled={user.is_blocked}
      >
        ✉ Написать
      </button>
      {user.is_blocked && (
        <div style={{ ...S.muted, marginTop: 8 }}>
          Юзер заблокирован — отправка недоступна.
        </div>
      )}

      {open && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: 16,
          }}
          onClick={() => !busy && setOpen(false)}
        >
          <form
            onSubmit={submit}
            onClick={(e) => e.stopPropagation()}
            style={{
              background: colors.card,
              padding: 20,
              borderRadius: 10,
              maxWidth: 520,
              width: "100%",
              boxShadow: "0 10px 40px rgba(0,0,0,0.25)",
            }}
          >
            <h3 style={S.h3}>
              Сообщение для {userFullName(user)}
            </h3>
            <div style={{ ...S.muted, marginBottom: 10 }}>
              tg_id {user.tg_id}
              {user.username ? ` · @${user.username}` : ""}
            </div>
            {ok && <div style={S.success}>{ok}</div>}
            {err && <div style={S.error}>{err}</div>}
            <textarea
              style={{
                ...S.input,
                minHeight: 140,
                fontFamily: "inherit",
                resize: "vertical",
              }}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Текст сообщения (до 4000 символов, можно HTML-теги)"
              maxLength={4000}
              autoFocus
            />
            <div style={{ ...S.muted, marginTop: 4, fontSize: 12 }}>
              {text.length} / 4000
            </div>
            <div
              style={{
                display: "flex",
                gap: 8,
                marginTop: 12,
                justifyContent: "flex-end",
              }}
            >
              <button
                type="button"
                style={S.btnSecondary}
                onClick={() => setOpen(false)}
                disabled={busy}
              >
                Отмена
              </button>
              <button
                type="submit"
                style={S.btn}
                disabled={busy || !text.trim()}
              >
                {busy ? "Отправка…" : "Отправить"}
              </button>
            </div>
          </form>
        </div>
      )}
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

// ─── DeleteAccountCard — необратимое удаление профиля ───────────────────────
function DeleteAccountCard({
  user,
  onDeleted,
}: {
  user: UserDetail;
  onDeleted: () => void;
}) {
  const expected = String(user.tg_id ?? user.email ?? user.id);
  const [confirm, setConfirm] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const canDelete = confirm && typed.trim() === expected && !busy;

  const doDelete = async () => {
    if (!canDelete) return;
    setBusy(true);
    setErr(null);
    try {
      await api.deleteUser(user.id);
      onDeleted();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ ...S.card, borderColor: colors.danger, marginTop: 16 }}>
      <h3 style={{ ...S.h3, color: colors.danger }}>Удалить аккаунт</h3>
      <p style={{ ...S.muted, marginTop: 0 }}>
        <b>Это безвозвратно.</b> Будут удалены: сам юзер, привязки входа,
        сессии, словарь, платежи, прогресс грамматики, медали — всё связанное.
      </p>
      {err && <div style={S.error}>{err}</div>}

      <label style={{ display: "block", marginTop: 10, fontSize: 14 }}>
        <input
          type="checkbox"
          checked={confirm}
          onChange={(e) => setConfirm(e.target.checked)}
          disabled={busy}
        />{" "}
        Я понимаю, что это необратимо.
      </label>

      <div style={{ marginTop: 8 }}>
        <div style={{ ...S.muted, marginBottom: 4 }}>
          Чтобы подтвердить, введи{" "}
          <code style={{ background: colors.card, padding: "1px 4px" }}>
            {expected}
          </code>{" "}
          (tg_id / email / id):
        </div>
        <input
          type="text"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          disabled={!confirm || busy}
          placeholder={expected}
          style={{
            ...S.input,
            width: "100%",
            maxWidth: 320,
          }}
        />
      </div>

      <button
        type="button"
        onClick={doDelete}
        disabled={!canDelete}
        style={{
          ...S.btnDanger,
          marginTop: 10,
          opacity: canDelete ? 1 : 0.5,
          cursor: canDelete ? "pointer" : "not-allowed",
        }}
      >
        {busy ? "Удаляю…" : "Удалить аккаунт"}
      </button>
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

// ─── Broadcast + Bulk Extend ─────────────────────────────────────────────────
function BroadcastPage() {
  return (
    <div>
      <h2 style={S.h2}>Массовые действия</h2>
      <BulkExtendCard />
      <BroadcastCard />
    </div>
  );
}

function BulkExtendCard() {
  const [days, setDays] = useState<number>(7);
  const [notes, setNotes] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const submit = async () => {
    setBusy(true);
    setOk(null);
    setErr(null);
    try {
      const r = await api.extendAllSubscriptions(days, notes.trim() || undefined);
      setOk(`Подписка продлена для ${r.affected} пользователей на +${days} дн.`);
      setNotes("");
      setConfirmOpen(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const quick = [3, 7, 14, 30];

  return (
    <div style={S.card}>
      <h3 style={S.h3}>Продлить подписку всем активным</h3>
      <p style={S.muted}>
        Подписка будет продлена только тем пользователям, у которых она сейчас
        активна (subscription_until &gt; сейчас). Каждому создаётся запись в
        payments с типом admin_bulk для аудита.
      </p>
      {ok && <div style={{ ...S.success, marginBottom: 10 }}>{ok}</div>}
      {err && <div style={{ ...S.error, marginBottom: 10 }}>{err}</div>}

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
          gridTemplateColumns: "120px 1fr",
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
          <label style={S.label}>Заметка (опционально)</label>
          <input
            style={S.input}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Например: компенсация за сбой 20 апреля"
          />
        </div>
      </div>

      <button
        type="button"
        style={S.btn}
        disabled={busy || days < 1}
        onClick={() => setConfirmOpen(true)}
      >
        Продлить всем активным на +{days} дн.
      </button>

      {confirmOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: 16,
          }}
          onClick={() => !busy && setConfirmOpen(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: colors.card,
              padding: 20,
              borderRadius: 10,
              maxWidth: 440,
              width: "100%",
              boxShadow: "0 10px 40px rgba(0,0,0,0.25)",
            }}
          >
            <h3 style={S.h3}>Подтверждение</h3>
            <p>
              Продлить подписку <b>всем активным подписчикам</b> на{" "}
              <b>+{days} дн.</b>?
            </p>
            <p style={S.muted}>Это действие нельзя откатить одной кнопкой.</p>
            <div
              style={{
                display: "flex",
                gap: 8,
                marginTop: 12,
                justifyContent: "flex-end",
              }}
            >
              <button
                type="button"
                style={S.btnSecondary}
                onClick={() => setConfirmOpen(false)}
                disabled={busy}
              >
                Отмена
              </button>
              <button
                type="button"
                style={S.btn}
                onClick={submit}
                disabled={busy}
              >
                {busy ? "Продлеваем…" : "Да, продлить"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function BroadcastCard() {
  const [text, setText] = useState<string>("");
  const [job, setJob] = useState<BroadcastJobStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const loadStatus = async () => {
    try {
      const r = await api.broadcastStatus();
      setJob(r.job);
    } catch (e) {
      // Тихо — статус может падать
    }
  };

  // При монтировании загрузим статус один раз
  useEffect(() => {
    loadStatus();
  }, []);

  // Поллинг прогресса раз в секунду пока is_running
  useEffect(() => {
    if (!job || !job.is_running) return;
    const t = setInterval(loadStatus, 1000);
    return () => clearInterval(t);
  }, [job?.is_running]);

  const start = async () => {
    if (!text.trim()) return;
    setStarting(true);
    setErr(null);
    setOk(null);
    try {
      await api.startBroadcast(text);
      setOk("Рассылка запущена");
      setConfirmOpen(false);
      setTimeout(loadStatus, 200);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    setErr(null);
    setOk(null);
    try {
      await api.cancelBroadcast();
      setOk("Запрос на отмену отправлен");
      setTimeout(loadStatus, 200);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const running = !!job?.is_running;
  const progressPct =
    job && job.total > 0
      ? Math.min(100, Math.round(((job.sent + job.failed + job.blocked) / job.total) * 100))
      : 0;

  return (
    <div style={S.card}>
      <h3 style={S.h3}>Рассылка всем пользователям</h3>
      <p style={S.muted}>
        Сообщение будет отправлено всем незаблокированным пользователям через
        бота. Ограничение скорости — 25 сообщ/сек (чтобы не попасть в лимит
        Telegram). Заблокировавшие бота помечаются автоматически.
      </p>
      {ok && <div style={{ ...S.success, marginBottom: 10 }}>{ok}</div>}
      {err && <div style={{ ...S.error, marginBottom: 10 }}>{err}</div>}

      <label style={S.label}>Текст сообщения (до 4000 символов, HTML)</label>
      <textarea
        style={{
          ...S.input,
          minHeight: 140,
          fontFamily: "inherit",
          resize: "vertical",
          marginBottom: 6,
        }}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Например: Привет! Сегодня мы запустили новый режим тренировки..."
        maxLength={4000}
        disabled={running}
      />
      <div style={{ ...S.muted, fontSize: 12, marginBottom: 12 }}>
        {text.length} / 4000
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          style={S.btn}
          disabled={running || !text.trim() || starting}
          onClick={() => setConfirmOpen(true)}
        >
          {starting ? "Запускаем…" : "Запустить рассылку"}
        </button>
        {running && (
          <button type="button" style={S.btnDanger} onClick={cancel}>
            Отменить
          </button>
        )}
        <button type="button" style={S.btnSecondary} onClick={loadStatus}>
          Обновить статус
        </button>
      </div>

      {/* ── Прогресс / история ── */}
      {job && (
        <div
          style={{
            marginTop: 16,
            padding: 12,
            border: `1px solid ${colors.border}`,
            borderRadius: 8,
          }}
        >
          <div style={{ fontSize: 14, marginBottom: 8 }}>
            <b>Статус:</b>{" "}
            {job.is_running
              ? "идёт рассылка…"
              : job.cancelled
              ? "отменена"
              : job.error
              ? "завершена с ошибкой"
              : "завершена"}{" "}
            <span style={S.muted}>· job {job.job_id}</span>
          </div>

          <div
            style={{
              width: "100%",
              height: 10,
              background: "#eee",
              borderRadius: 999,
              overflow: "hidden",
              marginBottom: 8,
            }}
          >
            <div
              style={{
                width: `${progressPct}%`,
                height: "100%",
                background: colors.primary,
                transition: "width 0.5s",
              }}
            />
          </div>

          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 14 }}>
            <span>
              Доставлено: <b>{job.sent}</b>
            </span>
            <span>
              Заблокировали бота: <b>{job.blocked}</b>
            </span>
            <span>
              Ошибок: <b>{job.failed}</b>
            </span>
            <span>
              Всего: <b>{job.total}</b>
            </span>
            <span style={S.muted}>{progressPct}%</span>
          </div>

          {job.error && (
            <div style={{ ...S.error, marginTop: 8 }}>Ошибка: {job.error}</div>
          )}
          {job.text_preview && (
            <div style={{ marginTop: 10, fontSize: 13 }}>
              <div style={S.muted}>Превью текста:</div>
              <div
                style={{
                  whiteSpace: "pre-wrap",
                  padding: 8,
                  background: "#f7f7f7",
                  borderRadius: 6,
                  marginTop: 4,
                }}
              >
                {job.text_preview}
              </div>
            </div>
          )}
        </div>
      )}

      {confirmOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: 16,
          }}
          onClick={() => !starting && setConfirmOpen(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: colors.card,
              padding: 20,
              borderRadius: 10,
              maxWidth: 520,
              width: "100%",
              boxShadow: "0 10px 40px rgba(0,0,0,0.25)",
            }}
          >
            <h3 style={S.h3}>Подтвердите рассылку</h3>
            <p>Сообщение уйдёт всем незаблокированным пользователям:</p>
            <div
              style={{
                whiteSpace: "pre-wrap",
                padding: 10,
                background: "#f7f7f7",
                borderRadius: 6,
                fontSize: 13,
                maxHeight: 240,
                overflow: "auto",
                marginBottom: 12,
              }}
            >
              {text}
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button
                type="button"
                style={S.btnSecondary}
                onClick={() => setConfirmOpen(false)}
                disabled={starting}
              >
                Отмена
              </button>
              <button
                type="button"
                style={S.btn}
                onClick={start}
                disabled={starting || !text.trim()}
              >
                {starting ? "Запускаем…" : "Да, отправить всем"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
