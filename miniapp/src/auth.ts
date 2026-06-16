/**
 * auth.ts — клиентская авторизация (PR-B).
 *
 * Модель: после входа (Telegram/Google) бэкенд выдаёт JWT, храним в
 * localStorage. На все запросы к API_BASE автоматически добавляем заголовок
 * Authorization: Bearer <jwt> (через monkeypatch window.fetch — чтобы не
 * править каждый fetch по экранам). Бэкенд принимает ЛИБО JWT, ЛИБО initData,
 * поэтому Mini App продолжает работать даже без токена.
 */

export const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  "https://api-english.krichigindocs.ru";

export const BOT_USERNAME = (
  (import.meta.env.VITE_BOT_USERNAME as string | undefined) || "kmo_ai_english_bot"
)
  .trim()
  .replace(/^@+/, "");

export const GOOGLE_CLIENT_ID =
  (import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined) || "";

const TOKEN_KEY = "englishbot_jwt";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* приватный режим — токен живёт только в памяти текущей сессии fetch */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

/** Token-параметр для WebSocket (заголовки на WS не повесить). */
export function wsTokenParam(): string {
  const t = getToken();
  return t ? `token=${encodeURIComponent(t)}` : "";
}

/** Один раз на старте: внедряем Authorization в fetch к нашему API. */
let _patched = false;
export function installFetchAuth(): void {
  if (_patched) return;
  _patched = true;
  const orig = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    try {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
          ? input.toString()
          : (input as Request).url;
      const token = getToken();
      if (token && url && url.startsWith(API_BASE)) {
        const headers = new Headers(init?.headers || {});
        if (!headers.has("Authorization")) {
          headers.set("Authorization", `Bearer ${token}`);
        }
        init = { ...(init || {}), headers };
      }
    } catch {
      /* если что-то пошло не так — отдаём оригинальный fetch без правок */
    }
    return orig(input, init);
  };
}

interface AuthResult {
  token: string;
  user: unknown;
}

async function _postJson(path: string, body: unknown): Promise<Response> {
  return fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Вход через Telegram Mini App initData. */
export async function loginTelegramInitData(initData: string): Promise<boolean> {
  try {
    const res = await _postJson("/api/auth/telegram", { init_data: initData });
    if (!res.ok) return false;
    const data = (await res.json()) as AuthResult;
    if (data.token) {
      setToken(data.token);
      return true;
    }
  } catch {
    /* network */
  }
  return false;
}

/** Вход через Telegram Login Widget (callback-объект от виджета). */
export async function loginTelegramWidget(widget: Record<string, unknown>): Promise<boolean> {
  const res = await _postJson("/api/auth/telegram", { widget });
  if (!res.ok) return false;
  const data = (await res.json()) as AuthResult;
  if (data.token) {
    setToken(data.token);
    return true;
  }
  return false;
}

/** Вход через Google (ID-token из Google Identity Services). */
export async function loginGoogle(idToken: string): Promise<{ ok: boolean; error?: string }> {
  const res = await _postJson("/api/auth/google", { id_token: idToken });
  if (res.ok) {
    const data = (await res.json()) as AuthResult;
    if (data.token) {
      setToken(data.token);
      return { ok: true };
    }
    return { ok: false, error: "no_token" };
  }
  let error = `HTTP ${res.status}`;
  try {
    const d = await res.json();
    if (d?.detail) error = String(d.detail);
  } catch {
    /* ignore */
  }
  return { ok: false, error };
}

/** Проверить, что сохранённый токен ещё валиден (GET /api/auth/me). */
export async function verifySession(): Promise<boolean> {
  if (!getToken()) return false;
  try {
    const res = await fetch(`${API_BASE}/api/auth/me`);
    if (res.ok) return true;
    if (res.status === 401) clearToken();
    return false;
  } catch {
    // Сеть недоступна — не разлогиниваем, считаем токен валидным оптимистично.
    return true;
  }
}
