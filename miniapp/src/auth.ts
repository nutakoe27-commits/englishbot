/**
 * auth.ts — клиентская авторизация.
 *
 * Модель: после входа через Telegram (Mini App initData или Login Widget на
 * сайте) бэкенд выдаёт JWT, храним в localStorage. На все запросы к API_BASE
 * автоматически добавляем заголовок Authorization: Bearer <jwt> (через
 * monkeypatch window.fetch). Бэкенд принимает ЛИБО JWT, ЛИБО initData, поэтому
 * Mini App продолжает работать даже без токена.
 *
 * Google/Apple убраны (миграция 0021): иностранный OAuth запрещён в РФ.
 * Нативная регистрация email+password — PR-2 серии 0021.
 */

export const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  "https://api-english.krichigindocs.ru";

export const BOT_USERNAME = (
  (import.meta.env.VITE_BOT_USERNAME as string | undefined) || "kmo_ai_english_bot"
)
  .trim()
  .replace(/^@+/, "");

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

interface NativeResult {
  ok: boolean;
  error?: string;
}

/** Регистрация по email+password. */
export async function registerNative(
  email: string, password: string, firstName?: string,
): Promise<NativeResult> {
  const res = await _postJson("/api/auth/register", {
    email,
    password,
    first_name: firstName || null,
  });
  if (res.ok) {
    const d = (await res.json()) as AuthResult;
    if (d.token) { setToken(d.token); return { ok: true }; }
    return { ok: false, error: "no_token" };
  }
  return { ok: false, error: await _readError(res) };
}

/** Логин по email+password. */
export async function loginNative(
  email: string, password: string,
): Promise<NativeResult> {
  const res = await _postJson("/api/auth/login", { email, password });
  if (res.ok) {
    const d = (await res.json()) as AuthResult;
    if (d.token) { setToken(d.token); return { ok: true }; }
    return { ok: false, error: "no_token" };
  }
  return { ok: false, error: await _readError(res) };
}

/** Запросить отвязку email/пароля — backend пошлёт сообщение в Telegram-бот,
 *  юзер должен подтвердить в чате (PR-6). */
export async function requestUnlinkNative(): Promise<NativeResult> {
  const res = await _postJson("/api/auth/unlink/request", { provider: "native" });
  if (res.ok) return { ok: true };
  return { ok: false, error: await _readError(res) };
}

/** Задать пароль (и опц. email) текущему юзеру (Bearer JWT). */
export async function setPassword(
  password: string, email?: string,
): Promise<NativeResult> {
  const res = await _postJson("/api/auth/set-password", {
    password,
    email: email || null,
  });
  if (res.ok) return { ok: true };
  return { ok: false, error: await _readError(res) };
}

async function _readError(res: Response): Promise<string> {
  let error = `HTTP ${res.status}`;
  try {
    const d = await res.json();
    if (d?.detail) error = String(d.detail);
  } catch {
    /* ignore */
  }
  return error;
}

interface TgStartResponse {
  token: string;
  url: string;
}

/** Старт Telegram deep-link флоу. mode='login' (anon) | 'link' (Bearer). */
interface YandexStartResponse {
  token: string;
  url: string;
}

/** Старт OAuth-флоу через Яндекс ID. mode='login' (anon) | 'link' (Bearer). */
export async function startYandexFlow(mode: "login" | "link"): Promise<YandexStartResponse | null> {
  try {
    const res = await _postJson("/api/auth/yandex/start", { mode });
    if (!res.ok) return null;
    return (await res.json()) as YandexStartResponse;
  } catch {
    return null;
  }
}

export interface YandexCallback {
  jwt?: string;
  mode?: "login" | "link";
  merged?: boolean;
  error?: string;
}

/** Разобрать URL fragment после возврата с Яндекса.
 *  Backend редиректит на <MINIAPP_URL>/#yandex_jwt=…&mode=…[&merged=1] или
 *  #yandex_error=<reason>. После чтения чистит URL.
 */
export function extractYandexCallback(): YandexCallback | null {
  if (typeof window === "undefined") return null;
  const hash = window.location.hash || "";
  if (!hash || (!hash.includes("yandex_jwt=") && !hash.includes("yandex_error="))) return null;
  const params = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
  const out: YandexCallback = {};
  const jwt = params.get("yandex_jwt");
  const err = params.get("yandex_error");
  const mode = params.get("mode");
  if (jwt) {
    out.jwt = jwt;
    setToken(jwt);
  }
  if (err) out.error = err;
  if (mode === "login" || mode === "link") out.mode = mode;
  if (params.get("merged") === "1") out.merged = true;
  try {
    window.history.replaceState(
      null, "", window.location.pathname + window.location.search,
    );
  } catch { /* ignore */ }
  return out;
}


export async function startTelegramFlow(mode: "login" | "link"): Promise<TgStartResponse | null> {
  try {
    const res = await _postJson("/api/auth/telegram/start", { mode });
    if (!res.ok) return null;
    return (await res.json()) as TgStartResponse;
  } catch {
    return null;
  }
}

export interface PollResult {
  status: "pending" | "done" | "cancelled" | "failed" | "expired";
  action?: string;
  token?: string;          // JWT, выдаётся при done для login/link
}

/** Опросить состояние action-токена. JWT (если есть) сохраняем сами. */
export async function pollAuth(token: string): Promise<PollResult> {
  try {
    const res = await fetch(
      `${API_BASE}/api/auth/poll?token=${encodeURIComponent(token)}`,
    );
    if (!res.ok) return { status: "failed" };
    const data = (await res.json()) as PollResult;
    if (data.status === "done" && data.token) {
      setToken(data.token);
    }
    return data;
  } catch {
    return { status: "failed" };
  }
}

/** Вход через Telegram Login Widget (callback-объект от виджета). Оставлен
 *  для back-compat и тестов; основной флоу теперь — startTelegramFlow. */
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

export interface MeIdentity {
  provider: string;
  email: string | null;
}

// ─── Подписка / оплата (PR-8: ЮKassa) ────────────────────────────────
export interface Plan {
  key: "trial7" | "monthly" | "yearly" | "twoyear";
  days: number;
  amount_rub: number;
  title: string;
  /** Бейдж на карточке («Рекомендуем», «Попробовать»). Решает backend. */
  badge?: string | null;
  /** Мелкая строка под ценой — условия тарифа. */
  note?: string | null;
}

export async function listPlans(): Promise<Plan[]> {
  try {
    const res = await fetch(`${API_BASE}/api/payments/plans`);
    if (!res.ok) return [];
    const data = await res.json() as { plans: Plan[] };
    return data.plans || [];
  } catch { return []; }
}

interface CreatePaymentResult {
  ok: boolean;
  confirmation_url?: string;
  payment_id?: number;
  error?: string;          // 'email_required' | 'yookassa_not_configured' | 'promo_invalid' | 'promo_already_used'
}

export async function createPayment(
  plan: Plan["key"], email?: string, promo_code?: string,
): Promise<CreatePaymentResult> {
  try {
    const res = await _postJson("/api/payments/create", { plan, email, promo_code });
    if (res.ok) {
      const data = await res.json() as { confirmation_url: string; payment_id: number };
      return { ok: true, ...data };
    }
    return { ok: false, error: await _readError(res) };
  } catch { return { ok: false, error: "network" }; }
}

// ─── B2B self-service: подключение школы с лендинга ──────────────────

export interface OrgQuote {
  seats: number;
  months: number;
  base_price_per_seat_month: number;
  volume_discount_pct: number;
  period_discount_pct: number;
  total_discount_pct: number;
  base_total_rub: number;
  total_rub: number;
  per_seat_month_rub: number;
  saving_rub: number;
  days: number;
}

export interface OrgPricing {
  base_price_per_seat_month: number;
  min_seats: number;
  max_seats: number;
  allowed_months: number[];
  volume_tiers: { seats: number; pct: number }[];
  period_tiers: { months: number; pct: number }[];
  trial_seats?: number;
  trial_days?: number;
}

/** Параметры тарификации для калькулятора (публичный эндпоинт). */
export async function fetchOrgPricing(): Promise<OrgPricing | null> {
  try {
    const res = await fetch(`${API_BASE}/api/payments/org/pricing`);
    if (!res.ok) return null;
    return await res.json() as OrgPricing;
  } catch { return null; }
}

/** Оформление пакета мест: возвращает ссылку на оплату ЮKassa. */
export async function orgCheckout(body: {
  school_name: string; seats: number; months: number;
  contact_person?: string; email?: string;
  /** new — новая школа, renew — продление, seats — докупка мест. */
  kind?: "new" | "renew" | "seats"; org_id?: number;
}): Promise<{ ok: boolean; confirmation_url?: string; payment_id?: number; error?: string }> {
  try {
    const res = await _postJson("/api/payments/org/checkout", body);
    if (res.ok) {
      const d = await res.json() as { confirmation_url: string; payment_id: number };
      return { ok: true, ...d };
    }
    return { ok: false, error: await _readError(res) };
  } catch { return { ok: false, error: "network" }; }
}

export interface OrgOrderStatus {
  payment_status: string;
  order_status: string;
  school_name: string;
  seats: number;
  months: number;
  amount_rub: number;
  org_id?: number;
  invite_code?: string;
  invite_link?: string;
  invite_link_web?: string;
  valid_until?: string | null;
}

/** Статус заказа школы + ссылки-приглашения после оплаты. */
export async function fetchOrgOrderStatus(paymentId: number): Promise<OrgOrderStatus | null> {
  try {
    const headers: HeadersInit = {};
    const tok = getToken();
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const res = await fetch(
      `${API_BASE}/api/payments/org/order-status?payment_id=${paymentId}`,
      { headers },
    );
    if (!res.ok) return null;
    return await res.json() as OrgOrderStatus;
  } catch { return null; }
}

export interface OrgAddonQuote {
  seats_add: number;
  remaining_days: number;
  seats_after: number;
  volume_discount_pct: number;
  total_rub: number;
  expired?: boolean;
}

/** Расчёт докупки мест до конца оплаченного срока. */
export async function fetchOrgAddonQuote(seats: number): Promise<OrgAddonQuote | null> {
  try {
    const res = await _postJson("/api/payments/org/addon-quote", { seats });
    if (!res.ok) return null;
    return await res.json() as OrgAddonQuote;
  } catch { return null; }
}

/** Бесплатный пробный период школы. */
export async function startOrgTrial(schoolName: string): Promise<
  { ok: true; invite_link: string; invite_link_web: string; teacher_link: string;
    seats: number; days: number; valid_until: string | null }
  | { ok: false; error: string }
> {
  try {
    const res = await _postJson("/api/org/trial", { school_name: schoolName });
    if (res.ok) return { ok: true, ...(await res.json()) };
    return { ok: false, error: await _readError(res) };
  } catch { return { ok: false, error: "network" }; }
}

/** Заявка на счёт и договор для юрлица. */
export async function requestOrgInvoice(body: {
  school_name: string; contact_email: string; seats: number; months: number;
  inn?: string; contact_person?: string; phone?: string; comment?: string;
}): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await _postJson("/api/payments/org/invoice-request", body);
    if (res.ok) return { ok: true };
    return { ok: false, error: await _readError(res) };
  } catch { return { ok: false, error: "network" }; }
}

/** B2B: подключение к школе по инвайт-коду (?school=CODE из deep-link). */
export async function joinOrg(
  inviteCode: string,
): Promise<{ status: string; org_name: string | null } | null> {
  try {
    const res = await _postJson("/api/org/join", { invite_code: inviteCode });
    if (!res.ok) return null;
    return await res.json() as { status: string; org_name: string | null };
  } catch { return null; }
}

export interface PromoCheck {
  valid: boolean;
  discount_percent: number;
  already_used: boolean;
}

export async function checkPromo(code: string): Promise<PromoCheck | null> {
  try {
    const headers: HeadersInit = {};
    const tok = getToken();
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const res = await fetch(
      `${API_BASE}/api/payments/promo/check?code=${encodeURIComponent(code)}`,
      { headers },
    );
    if (!res.ok) return null;
    return await res.json() as PromoCheck;
  } catch { return null; }
}

export interface PaymentStatus {
  payment_id: number;
  status: "pending" | "succeeded" | "canceled" | "refunded";
  plan: string;
  amount_rub: number;
  days_granted: number;
}

export async function fetchPaymentStatus(paymentId: number): Promise<PaymentStatus | null> {
  try {
    const headers: HeadersInit = {};
    const tok = getToken();
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const res = await fetch(`${API_BASE}/api/payments/status?payment_id=${paymentId}`, { headers });
    if (!res.ok) return null;
    return await res.json() as PaymentStatus;
  } catch { return null; }
}


export interface MeInfo {
  id: number;
  tg_id: number | null;
  first_name: string | null;
  username: string | null;
  email: string | null;
  identities: MeIdentity[];
  has_subscription?: boolean;
  subscription_until?: string | null;
  tutorial_done?: boolean;
  /** Первые дни после регистрации — полный доступ (миграция 0033).
   *  has_subscription при этом тоже true. */
  trial_active?: boolean;
  trial_until?: string | null;
  /** Уровень по тесту (миграция 0035). null — тест ещё не проходили. */
  cefr_level?: string | null;
  cefr_tested_at?: string | null;
  // B2B: школа юзера. role teacher/admin → в Профиле кнопка «Кабинет школы».
  org?: { name: string; role: "student" | "teacher" | "admin" } | null;
}

// ─── B2B: кабинет школы (для role teacher/admin) ─────────────────────

export interface OrgStudentRow {
  user_id: number;
  first_name: string | null;
  username: string | null;
  active: boolean;
  joined_at: string | null;
  speaking_min: number;
  listening_min: number;
  grammar_lessons: number;
  /** Суммарная практика за период — главная метрика в списке. */
  practice_min: number;
  points_month: number;
  streak_days: number;
  last_practice_date: string | null;
  /** Дней с последнего занятия; null — не начинал вовсе. */
  days_since_practice: number | null;
  /** Классификация с сервера: ok | attention (7+ дней) | never. */
  status: "ok" | "attention" | "never";
}

export interface OrgSummary {
  students_total: number;
  active_students: number;
  active_students_prev: number;
  practice_min: number;
  practice_min_prev: number;
  need_attention: number;
  never_started: number;
}

export interface OrgPeriod {
  key: string;
  label: string;
  options: { key: string; label: string }[];
}

export interface OrgCabinet {
  org: {
    id: number;
    name: string;
    seats_total: number;
    seats_used: number;
    valid_until: string | null;
    days_left: number;
    is_trial: boolean;
    invite_link: string;
    invite_link_web: string;
    /** Ссылка для преподавателей: даёт кабинет, места не занимает. */
    teacher_link: string;
    teacher_link_web: string;
  };
  period: OrgPeriod;
  summary: OrgSummary;
  students: OrgStudentRow[];
}

export interface OrgStudentDetail {
  student: OrgStudentRow;
  level: { level: number; lifetime_points: number };
  mistakes: { category: string | null; bad: string | null; good: string | null }[];
}

function _authHeaders(): HeadersInit {
  const tok = getToken();
  return tok ? { Authorization: `Bearer ${tok}` } : {};
}

/** Выход из школы по инициативе юзера (место освобождается). */
export async function leaveOrg(): Promise<{ ok: boolean; org_name?: string } | null> {
  try {
    const res = await _postJson("/api/org/leave", {});
    if (!res.ok) return null;
    return await res.json() as { ok: boolean; org_name?: string };
  } catch { return null; }
}

/** Учитель исключает/возвращает ученика своей школы. */
export async function setOrgStudentActive(
  userId: number, active: boolean,
): Promise<boolean> {
  try {
    const res = await _postJson(
      `/api/org/cabinet/student/${userId}/active`, { active },
    );
    return res.ok;
  } catch { return false; }
}

export async function fetchOrgCabinet(period = "7"): Promise<OrgCabinet | null> {
  try {
    const res = await fetch(
      `${API_BASE}/api/org/cabinet?period=${encodeURIComponent(period)}`,
      { headers: _authHeaders() },
    );
    if (!res.ok) return null;
    return (await res.json()) as OrgCabinet;
  } catch { return null; }
}

export async function fetchOrgStudent(userId: number): Promise<OrgStudentDetail | null> {
  try {
    const res = await fetch(
      `${API_BASE}/api/org/cabinet/student/${userId}`,
      { headers: _authHeaders() },
    );
    if (!res.ok) return null;
    return (await res.json()) as OrgStudentDetail;
  } catch { return null; }
}

/** Скачать CSV-отчёт: fetch с Bearer → blob → программный клик по ссылке. */
export async function downloadOrgReport(): Promise<boolean> {
  try {
    const res = await fetch(
      `${API_BASE}/api/org/cabinet/report.csv`,
      { headers: _authHeaders() },
    );
    if (!res.ok) return false;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const cd = res.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename="?([^";]+)"?/);
    a.download = m ? m[1] : "report.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    return true;
  } catch { return false; }
}

/** Пометить онбординг пройденным (или скипнутым). Идемпотентно. */
export async function completeTutorial(): Promise<void> {
  try {
    await _postJson("/api/auth/tutorial/complete", {});
  } catch { /* ignore — не блокирующая операция */ }
}

/** Текущий аккаунт + привязки. null если не авторизован/ошибка. */
export async function fetchMe(): Promise<MeInfo | null> {
  try {
    const res = await fetch(`${API_BASE}/api/auth/me`);
    if (!res.ok) return null;
    return (await res.json()) as MeInfo;
  } catch {
    return null;
  }
}

interface LinkResult {
  ok: boolean;
  merged?: boolean;
  error?: string;
}

/** Привязать Telegram (Login Widget) к текущему аккаунту (для веб-юзеров).
 *  При слиянии бэк может вернуть новый JWT — сохраняем его, чтобы дальнейшие
 *  запросы шли уже от primary-аккаунта. */
export async function linkTelegramWidget(
  widget: Record<string, unknown>,
): Promise<LinkResult> {
  const res = await _postJson("/api/auth/link", { provider: "telegram", widget });
  if (res.ok) {
    try {
      const d = (await res.json()) as {
        merged?: boolean; token?: string | null;
      };
      if (d.token) setToken(d.token);
      return { ok: true, merged: !!d.merged };
    } catch {
      return { ok: true };
    }
  }
  return { ok: false, error: await _readError(res) };
}

/** Выйти из аккаунта (только клиентски — стираем токен). */
export function logout(): void {
  clearToken();
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


// ─── Тест уровня (миграция 0035) ─────────────────────────────────────
// Во время прохождения LLM не участвует: вопросы приходят из
// калиброванного банка, поэтому каждый шаг — обычный быстрый POST.

export interface LevelQuestion {
  kind: "quiz";
  /** grammar — предложение с пропуском, vocab — слово, listening — вопрос по подкасту. */
  subkind: "grammar" | "vocab" | "listening";
  id: string;
  index: number;
  total: number;
  level: string | null;
  skill: string;
  prompt: string;
  choices: string[];
}

/** Экран прослушивания. ready=false — подкаст ещё генерится в фоне,
 *  клиент опрашивает /listen и не листает дальше. */
export interface LevelAudio {
  kind: "listen_audio";
  block: number;
  index: number;
  total: number;
  ready: boolean;
  failed: boolean;
  level: string | null;
  audio_url: string | null;
  seconds: number | null;
}

export type LevelScreen = LevelQuestion | LevelAudio;

export interface LevelResult {
  cefr: string;
  correct_cnt: number;
  total_cnt: number;
  by_level: Record<string, { correct: number; total: number }>;
  by_skill: Record<string, { correct: number; total: number }>;
  weak_skills: string[];
  /** null, если оба подкаста не сгенерировались. */
  listening: { correct: number; total: number } | null;
  /** true — короткий тест с лендинга, пройденный анонимно: сервер отдал
   *  только уровень, разбор откроется после claimLevelTest(). */
  locked?: boolean;
}

export interface LevelStart {
  test_id: string;
  question: LevelScreen;
  previous: {
    cefr: string;
    created_at: string | null;
    correct_cnt: number;
    total_cnt: number;
  } | null;
}

export interface LevelAnswer {
  /** null на экране прослушивания — это не задание. */
  correct: boolean | null;
  correct_answer?: string;
  note?: string;
  done: boolean;
  question?: LevelScreen;
  result?: LevelResult;
}

/** initData Telegram Mini App, если мы внутри Telegram. На вебе — "",
 *  там авторизация идёт по JWT через monkeypatch window.fetch. */
function _initData(): string {
  try {
    const tg = (window as unknown as {
      Telegram?: { WebApp?: { initData?: string } };
    }).Telegram;
    return tg?.WebApp?.initData || "";
  } catch {
    return "";
  }
}

async function _levelPost<T>(path: string, body: Record<string, unknown>): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}/api/level-test/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ..._authHeaders() },
      body: JSON.stringify({ init_data: _initData(), ...body }),
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** mode="short" — короткая версия для лендинга /level: 10 заданий из
 *  банка, без подкастов, можно без авторизации. */
export const startLevelTest = (mode?: "short") =>
  _levelPost<LevelStart>("start", mode ? { mode } : {});

/** Забрать анонимно пройденный тест себе после входа — в ответе полный
 *  результат с разбивкой по навыкам. */
export const claimLevelTest = (test_id: string) =>
  _levelPost<{ result: LevelResult }>("claim", { test_id });

export const answerLevelTest = (test_id: string, question_id: string, answer: string) =>
  _levelPost<LevelAnswer>("answer", { test_id, question_id, answer });

export const fetchLevelReport = (test_id: string) =>
  _levelPost<{ report: string }>("report", { test_id });

/** Готов ли подкаст блока. Опрашивается, пока ready=false. */
export const pollLevelAudio = (test_id: string, block: number) =>
  _levelPost<LevelAudio>("listen", { test_id, block });
