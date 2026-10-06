/**
 * metrika.ts — аналитика: Yandex.Metrika (counter 109924904) + VK MyTracker.
 *
 * Метрика инициализируется в index.html (тег <script>). MyTracker — здесь,
 * в initMyTracker(): счётчик включается только если на этапе сборки задан
 * VITE_MYTRACKER_ID, иначе все вызовы no-op. Так стенд без ID не шлёт
 * мусор в боевой проект.
 *
 * ymHit / ymReachGoal шлют событие в ОБА счётчика: набор целей в MyTracker
 * один в один повторяет Метрику, без правок в местах вызова. Если скрипт
 * счётчика не загрузился (adblock, сеть) — вызовы молча no-op'нут.
 */

import { isAppMode } from "./appMode";

const COUNTER_ID = 109924904;

/** ID счётчика MyTracker (web). Пусто — MyTracker выключен. */
const MT_ID = String(import.meta.env.VITE_MYTRACKER_ID || "").trim();

type YmFn = (counterId: number, action: string, ...args: unknown[]) => void;
type TmrQueue = { push: (ev: Record<string, unknown>) => void };

declare global {
  interface Window {
    _tmr?: TmrQueue | Record<string, unknown>[];
  }
}

function ym(): YmFn | null {
  // @ts-expect-error — ym инжектится глобально из index.html
  const fn = typeof window !== "undefined" ? window.ym : undefined;
  return typeof fn === "function" ? fn : null;
}

function tmr(): TmrQueue | null {
  if (!MT_ID || typeof window === "undefined") return null;
  const q = window._tmr;
  return q && typeof (q as TmrQueue).push === "function" ? (q as TmrQueue) : null;
}

/** Откуда открыто приложение — отдельным параметром в каждом событии
 *  MyTracker, чтобы делить Telegram Mini App, установленное приложение
 *  (PWA/TWA из RuStore) и обычный браузер. */
function platform(): string {
  try {
    // @ts-expect-error — Telegram SDK кладёт объект в window
    if (window.Telegram?.WebApp?.initData) return "telegram";
    return isAppMode() ? "app" : "web";
  } catch {
    return "web";
  }
}

/** Подключить счётчик MyTracker. Вызывается один раз при старте (main.tsx).
 *  Первый pageView уходит отсюда, дальше — виртуальные хиты через ymHit. */
export function initMyTracker(): void {
  if (!MT_ID || typeof document === "undefined") return;
  const w = window as Window & { _tmr?: Record<string, unknown>[] };
  const q = (w._tmr = (w._tmr as Record<string, unknown>[]) || []);
  q.push({ id: MT_ID, type: "pageView", start: Date.now() });
  if (document.getElementById("tmr-code")) return;
  const s = document.createElement("script");
  s.type = "text/javascript";
  s.async = true;
  s.id = "tmr-code";
  s.src = "https://top-fwz1.mail.ru/js/code.js";
  const first = document.getElementsByTagName("script")[0];
  if (first?.parentNode) first.parentNode.insertBefore(s, first);
  else document.head.appendChild(s);
}

/** Привязать события к аккаунту (customUserId в MyTracker): один человек
 *  с телефона и ноутбука считается одним пользователем. Повторный вызов с
 *  тем же id ничего не делает. */
let _mtUser = "";
export function mtSetUser(userId: number | string | null | undefined): void {
  const q = tmr();
  const id = userId === null || userId === undefined ? "" : String(userId);
  if (!q || !id || id === _mtUser) return;
  _mtUser = id;
  q.push({ type: "setUserID", userid: id });
}

/** Виртуальный pageview — для SPA-навигации (открытие модалок и экранов). */
export function ymHit(url: string, title?: string): void {
  const fn = ym();
  if (fn) {
    fn(COUNTER_ID, "hit", url, {
      title: title || (typeof document !== "undefined" ? document.title : ""),
      referer: typeof document !== "undefined" ? document.referrer : "",
    });
  }
  const q = tmr();
  if (q) q.push({ id: MT_ID, type: "pageView", url, start: Date.now() });
}

/** Достижение цели. Параметры опциональны: в Метрике попадут в отчёт
 *  «Параметры визитов», в MyTracker — в параметры события. Для оплаты
 *  (amount_rub) сумма уходит ещё и как value — это выручка в MyTracker. */
export function ymReachGoal(goalName: string, params?: Record<string, unknown>): void {
  const fn = ym();
  if (fn) {
    if (params) fn(COUNTER_ID, "reachGoal", goalName, params);
    else fn(COUNTER_ID, "reachGoal", goalName);
  }
  const q = tmr();
  if (q) {
    const ev: Record<string, unknown> = {
      id: MT_ID,
      type: "reachGoal",
      goal: goalName,
      params: { platform: platform(), ...(params || {}) },
    };
    const amount = Number(params?.amount_rub);
    if (goalName === "subscription_paid" && Number.isFinite(amount) && amount > 0) {
      ev.value = amount;
    }
    q.push(ev);
  }
}
