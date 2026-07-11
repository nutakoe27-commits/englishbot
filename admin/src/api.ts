// ─── API client ──────────────────────────────────────────────────────────────
// Базовый URL берём из VITE_API_BASE (задаётся через docker-compose build arg
// в продакшене) или идём в тот же хост по /api.
const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

const TOKEN_KEY = "englishbot_admin_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { token?: string | null }
): Promise<T> {
  const token = init?.token ?? getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (token) headers["X-Admin-Token"] = token;

  const url = `${API_BASE}${path}`;
  const res = await fetch(url, { ...init, headers });
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const detail =
      (data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : null) ||
      (typeof data === "string" ? data : null) ||
      `HTTP ${res.status}`;
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

// ─── Types (зеркало backend/app/admin.py) ────────────────────────────────────
export interface ModeStat {
  sessions: number;
  minutes: number;
}

export interface ActiveAvg {
  active_users: number;
  avg_minutes_total: number;
  by_mode_minutes: Record<string, number>;
}

export interface Metrics {
  total_users: number;
  active_subscriptions: number;
  blocked_users: number;
  dau: number;
  wau: number;
  mau: number;
  minutes_today: number;
  total_revenue_rub: number;
  new_users_today: number;
  bot_activated_total?: number;
  bot_activated_today?: number;
  modes_today?: Record<string, ModeStat>;
  listening_top_categories?: { category: string; count: number }[];
  active_avg?: ActiveAvg;
  active_avg_30d?: ActiveAvg;
}

export interface OnlineSession {
  user_id: number;
  tg_id: number | null;
  username: string | null;
  first_name: string | null;
  mode: string;
  level: string | null;
  role: string | null;
  duration_sec: number;
}

export interface OnlineResponse {
  count: number;
  by_mode: Record<string, number>;
  sessions: OnlineSession[];
}

export interface UserBrief {
  id: number;
  tg_id: number | null;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  email?: string | null;
  auth_providers?: string[];
  is_blocked: boolean;
  has_subscription: boolean;
  subscription_until: string | null;
  created_at: string;
}

export interface UserDetail extends UserBrief {
  language_code: string | null;
  reminder_enabled: boolean;
  reminder_hour_msk: number;
  used_seconds_today: number;
  free_seconds_per_day: number;
  bonus_seconds_today: number;
  used_seconds_total: number;
  streak_current: number;
  streak_best: number;
  last_practice_date: string | null;
  minutes_by_mode: Record<string, number>;
  words_count: number;
  achievements_earned: number;
  achievements_total: number;
  grammar_topics_done?: number;
  grammar_topics_total?: number;
}

export interface MaintenanceSettings {
  enabled: boolean;
  message: string;
}

export interface BulkExtendResponse {
  ok: boolean;
  affected: number;
}

export interface BroadcastJobStatus {
  job_id: string;
  text_preview: string;
  total: number;
  sent: number;
  failed: number;
  blocked: number;
  is_running: boolean;
  cancelled: boolean;
  started_at: number | null;
  finished_at: number | null;
  error: string | null;
}

export interface BroadcastStatusResponse {
  ok: boolean;
  job: BroadcastJobStatus | null;
}

// ─── Endpoints ───────────────────────────────────────────────────────────────
export const api = {
  me: (token?: string) => request<{ ok: boolean }>("/api/admin/me", { token }),
  metrics: () => request<Metrics>("/api/admin/metrics"),
  users: (q: string, limit = 50, offset = 0) =>
    request<UserBrief[]>(
      `/api/admin/users?q=${encodeURIComponent(q)}&limit=${limit}&offset=${offset}`,
    ),
  user: (id: number) => request<UserDetail>(`/api/admin/users/${id}`),
  grant: (
    id: number,
    body: { days: number; plan?: string; notes?: string; amount_rub?: number }
  ) =>
    request<UserDetail>(`/api/admin/users/${id}/grant-subscription`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteUser: (id: number) =>
    request<{ ok: boolean }>(`/api/admin/users/${id}`, {
      method: "DELETE",
    }),
  block: (id: number, blocked: boolean) =>
    request<UserDetail>(`/api/admin/users/${id}/block`, {
      method: "POST",
      body: JSON.stringify({ blocked }),
    }),
  reminder: (
    id: number,
    body: { enabled?: boolean; hour_msk?: number }
  ) =>
    request<UserDetail>(`/api/admin/users/${id}/reminder`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  maintenance: () =>
    request<MaintenanceSettings>("/api/admin/settings/maintenance"),
  setMaintenance: (body: MaintenanceSettings) =>
    request<MaintenanceSettings>("/api/admin/settings/maintenance", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  online: () => request<OnlineResponse>("/api/admin/online"),

  // ─── Массовые операции ───────────────────────────────────────
  extendAllSubscriptions: (days: number, notes?: string) =>
    request<BulkExtendResponse>("/api/admin/subscription/extend-all", {
      method: "POST",
      body: JSON.stringify({ days, notes: notes ?? null }),
    }),
  sendUserMessage: (userId: number, text: string) =>
    request<{ ok: boolean; delivered: boolean }>(
      `/api/admin/users/${userId}/message`,
      { method: "POST", body: JSON.stringify({ text }) }
    ),
  startBroadcast: (text: string) =>
    request<{ ok: boolean; job_id: string }>("/api/admin/broadcast", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  broadcastStatus: () =>
    request<BroadcastStatusResponse>("/api/admin/broadcast/status"),
  cancelBroadcast: () =>
    request<{ ok: boolean; cancelled: boolean }>(
      "/api/admin/broadcast/cancel",
      { method: "POST" }
    ),

  // ─── Admin v2: charts / retention / sessions ───────────
  chartSeries: (
    metric: "dau" | "new-users" | "revenue",
    days = 30,
  ) =>
    request<{ series: ChartPoint[] }>(
      `/api/admin/charts/${metric}?days=${days}`,
    ),
  retention: (days = 30) =>
    request<{ cohorts: RetentionCohort[] }>(
      `/api/admin/retention?days=${days}`,
    ),
  userSessions: (userId: number, limit = 30) =>
    request<{ sessions: UserSession[] }>(
      `/api/admin/users/${userId}/sessions?limit=${limit}`,
    ),

  // ─── Payments admin tab ───────────────────────────────
  listPayments: (params: {
    limit?: number;
    offset?: number;
    status?: string;
    plan?: string;
  } = {}) => {
    const q = new URLSearchParams();
    if (params.limit != null) q.set("limit", String(params.limit));
    if (params.offset != null) q.set("offset", String(params.offset));
    if (params.status) q.set("status", params.status);
    if (params.plan) q.set("plan", params.plan);
    const qs = q.toString();
    return request<PaymentsListResponse>(
      `/api/admin/payments${qs ? `?${qs}` : ""}`,
    );
  },
  paymentsMonthChart: () =>
    request<PaymentsMonthChart>(`/api/admin/payments/month-chart`),

  // ─── Промокоды ────────────────────────────────────────
  listPromos: () => request<{ items: PromoItem[] }>(`/api/admin/promo`),
  createPromo: (code: string, discount_percent: number) =>
    request<PromoItem>(`/api/admin/promo`, {
      method: "POST",
      body: JSON.stringify({ code, discount_percent }),
    }),
  togglePromo: (code: string, active: boolean) =>
    request<{ ok: boolean }>(`/api/admin/promo/${encodeURIComponent(code)}/toggle`, {
      method: "POST",
      body: JSON.stringify({ active }),
    }),
  promoActivations: (code: string) =>
    request<{ items: PromoActivation[]; total: number }>(
      `/api/admin/promo/${encodeURIComponent(code)}/activations`,
    ),

  // ─── B2B: школы ───────────────────────────────────────
  listOrgs: () => request<{ items: OrgItem[] }>(`/api/admin/orgs`),
  createOrg: (body: {
    name: string; seats_total: number;
    valid_until?: string; contact_email?: string;
  }) =>
    request<OrgItem>(`/api/admin/orgs`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateOrg: (id: number, body: {
    name?: string; seats_total?: number;
    valid_until?: string; active?: boolean;
  }) =>
    request<{ ok: boolean }>(`/api/admin/orgs/${id}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  orgMembers: (id: number) =>
    request<{ items: OrgMemberItem[]; total: number }>(
      `/api/admin/orgs/${id}/members`,
    ),
  orgMemberActive: (id: number, user_id: number, active: boolean) =>
    request<{ ok: boolean }>(`/api/admin/orgs/${id}/member-active`, {
      method: "POST",
      body: JSON.stringify({ user_id, active }),
    }),
  orgMemberRole: (id: number, user_id: number, role: string) =>
    request<{ ok: boolean }>(`/api/admin/orgs/${id}/member-role`, {
      method: "POST",
      body: JSON.stringify({ user_id, role }),
    }),
};

export interface OrgItem {
  id: number;
  name: string;
  invite_code: string;
  invite_link: string;
  invite_link_web: string;
  seats_total: number;
  seats_used: number;
  valid_until: string | null;
  active: boolean;
  contact_email: string | null;
  created_at: string | null;
}
export interface OrgMemberItem {
  user_id: number;
  tg_id: number | null;
  first_name: string | null;
  username: string | null;
  role: string;
  active: boolean;
  joined_at: string | null;
}

export interface PromoItem {
  code: string;
  discount_percent: number;
  active: boolean;
  used_count: number;
  created_at: string | null;
}
export interface PromoActivation {
  user_id: number;
  tg_id: number | null;
  username: string | null;
  discount_percent: number;
  created_at: string | null;
}

export interface PaymentItem {
  id: number;
  user_id: number;
  tg_id: number | null;
  username: string | null;
  amount_rub: number;
  plan: string;
  status: string;
  days_granted: number;
  granted_by_tg_id: number | null;
  notes: string | null;
  created_at: string;
}
export interface PaymentsListResponse {
  items: PaymentItem[];
  total: number;
  limit: number;
  offset: number;
}
export interface PaymentsMonthChartPoint {
  date: string;
  day: number;
  value: number;
}
export interface PaymentsMonthChart {
  month: string;
  days_in_month: number;
  today_day: number;
  total_rub: number;
  series: PaymentsMonthChartPoint[];
}
