/**
 * push.ts — подписка браузера на уведомления (Web Push).
 *
 * Зачем: канал до человека, не зависящий от Telegram. Работает и в обычном
 * мобильном Chrome, и внутри установленного из магазина приложения — код
 * один и тот же.
 *
 * Где не работает, и это нормально:
 *
 * • iOS присылает push только приложению, добавленному на домашний экран,
 *   и только с 16.4+. В обычной вкладке Safari PushManager отсутствует;
 * • на Android без сервисов Google уведомления не доставляются;
 * • внутри Telegram Mini App подписываться бессмысленно — там свои
 *   уведомления, а вебвью живёт ровно пока открыт.
 *
 * Разрешение спрашиваем НЕ на старте. Браузеры считают запрос без повода
 * тёмным паттерном, а человек, которому нечего напоминать, жмёт «нет», и
 * второй попытки уже не будет: отказ запоминается навсегда. Поэтому сперва
 * мягкий вопрос в интерфейсе (после первого занятия или переключателем в
 * Аккаунте) и только по согласию — системный диалог.
 */

import { API_BASE } from "./auth";
import { readyServiceWorker } from "./pwa";

/** Ключ, которым помечаем, что мягкий вопрос уже задавали. */
export const PUSH_ASKED_KEY = "et_push_asked";

export type PushState = "unsupported" | "default" | "granted" | "denied";

/** initData Telegram, если мы внутри Mini App. */
function inTelegram(): boolean {
  try {
    const tg = (window as unknown as {
      Telegram?: { WebApp?: { initData?: string } };
    }).Telegram;
    return !!tg?.WebApp?.initData;
  } catch {
    return false;
  }
}

export function pushSupported(): boolean {
  if (typeof window === "undefined") return false;
  if (inTelegram()) return false;
  return (
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export function pushState(): PushState {
  if (!pushSupported()) return "unsupported";
  const p = Notification.permission;
  if (p === "granted") return "granted";
  if (p === "denied") return "denied";
  return "default";
}

/**
 * Есть ли активная подписка.
 *
 * Разрешение браузера и подписка — РАЗНЫЕ вещи, и путать их нельзя.
 * `unsubscribe()` снимает подписку, но выданное разрешение остаётся
 * выданным навсегда. Если строить состояние переключателя по
 * `Notification.permission`, то после отключения он продолжит показывать
 * «включено», а повторное нажатие будет снова пытаться отписаться — и
 * человек застрянет без возможности включить обратно. Ровно это и
 * случилось, отсюда отдельная проверка.
 *
 * Таймаут нужен, потому что `navigator.serviceWorker.ready` не резолвится
 * никогда, если worker не зарегистрирован: без него экран Аккаунта завис
 * бы на «Секунду…».
 */
export async function pushSubscribed(): Promise<boolean> {
  if (!pushSupported() || Notification.permission !== "granted") return false;
  try {
    const reg = await Promise.race([
      readyServiceWorker(),
      new Promise<null>((r) => setTimeout(() => r(null), 3000)),
    ]);
    if (!reg) return false;
    return !!(await reg.pushManager.getSubscription());
  } catch {
    return false;
  }
}

/** Спрашивали ли уже мягким вопросом. */
export function pushAsked(): boolean {
  try { return localStorage.getItem(PUSH_ASKED_KEY) === "1"; } catch { return false; }
}

export function markPushAsked(): void {
  try { localStorage.setItem(PUSH_ASKED_KEY, "1"); } catch { /* private mode */ }
}

/** VAPID-ключ приходит в base64url, а PushManager хочет байты.
 *  Возвращаем именно ArrayBuffer: типы Uint8Array в разных lib-наборах
 *  расходятся с BufferSource, и подписка перестаёт собираться. */
function urlBase64ToBuffer(base64: string): ArrayBuffer {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const normalized = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(normalized);
  const buf = new ArrayBuffer(raw.length);
  const view = new Uint8Array(buf);
  for (let i = 0; i < raw.length; i++) view[i] = raw.charCodeAt(i);
  return buf;
}

async function fetchKey(): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/api/push/key`);
    if (!res.ok) return null;
    const data = (await res.json()) as { enabled: boolean; public_key: string };
    return data.enabled && data.public_key ? data.public_key : null;
  } catch {
    return null;
  }
}

function initData(): string {
  try {
    const tg = (window as unknown as {
      Telegram?: { WebApp?: { initData?: string } };
    }).Telegram;
    return tg?.WebApp?.initData || "";
  } catch {
    return "";
  }
}

async function sendSubscription(sub: PushSubscription): Promise<boolean> {
  const json = sub.toJSON() as {
    endpoint?: string;
    keys?: { p256dh?: string; auth?: string };
  };
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) return false;
  try {
    const res = await fetch(`${API_BASE}/api/push/subscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        endpoint: json.endpoint,
        keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
        init_data: initData(),
        source: window.matchMedia?.("(display-mode: standalone)")?.matches ? "app" : "web",
      }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Запросить разрешение и подписаться. Возвращает итоговое состояние.
 * Зовётся ТОЛЬКО из явного действия человека — иначе браузер покажет
 * диалог «из ниоткуда» и, скорее всего, получит отказ навсегда.
 */
export async function enablePush(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  markPushAsked();
  let permission = Notification.permission;
  if (permission === "default") {
    try {
      permission = await Notification.requestPermission();
    } catch {
      return "denied";
    }
  }
  if (permission !== "granted") return permission === "denied" ? "denied" : "default";

  const key = await fetchKey();
  if (!key) return "granted";      // разрешение есть, но ключи не настроены
  const reg = await readyServiceWorker();
  if (!reg) return "granted";
  try {
    const existing = await reg.pushManager.getSubscription();
    const sub = existing || await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToBuffer(key),
    });
    await sendSubscription(sub);
  } catch {
    /* браузер отказал в подписке — разрешение всё равно есть */
  }
  return "granted";
}

/** Отключить уведомления: снять подписку и удалить её на сервере. */
export async function disablePush(): Promise<void> {
  if (!pushSupported()) return;
  const reg = await readyServiceWorker();
  if (!reg) return;
  try {
    const sub = await reg.pushManager.getSubscription();
    if (!sub) return;
    const endpoint = sub.endpoint;
    await sub.unsubscribe().catch(() => undefined);
    await fetch(`${API_BASE}/api/push/unsubscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint }),
    }).catch(() => undefined);
  } catch {
    /* нечего снимать */
  }
}

/**
 * Тихая синхронизация на старте: если подписка уже есть, отправить её
 * ещё раз. Нужно ровно для одного — привязать к аккаунту того, кто
 * подписался анонимно (например, на лендинге теста) и вошёл позже.
 * Разрешения не спрашивает и диалогов не показывает.
 */
export async function syncPush(): Promise<void> {
  if (!pushSupported() || Notification.permission !== "granted") return;
  const reg = await readyServiceWorker();
  if (!reg) return;
  try {
    const sub = await reg.pushManager.getSubscription();
    if (sub) await sendSubscription(sub);
  } catch {
    /* не критично */
  }
}
