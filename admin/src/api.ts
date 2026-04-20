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
export interface Metrics {
  total_users: number;
  active_subscriptions: number;
  blocked_users: number;
  dau: number;
  wau: number;
  mau: number;
  minutes_today: number;
  total_revenue_rub: number;
}

export interface UserBrief {
  id: number;
  tg_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
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
}

export interface MaintenanceSettings {
  enabled: boolean;
  message: string;
}

export interface PaymentRecord {
  id: number;
  user_id: number;
  tg_id: number;
  amount_rub: number;
  plan: string;
  status: string;
  created_at: string;
  notes: string | null;
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
  users: (q: string, limit = 50) =>
    request<UserBrief[]>(
      `/api/admin/users?q=${encodeURIComponent(q)}&limit=${limit}`
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
  recentPayments: () =>
    request<PaymentRecord[]>("/api/admin/payments/recent"),

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
};
