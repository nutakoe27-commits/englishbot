/**
 * mtInstall.ts — связка установки Android-приложения с аккаунтом (MyTracker).
 *
 * Нативная обёртка (TWA) при запуске добавляет к стартовому адресу
 * ?mt_iid=<instanceId> из SDK MyTracker. Здесь мы его забираем из адреса,
 * храним в localStorage и после входа отдаём бэкенду. Бэкенд по
 * нему шлёт регистрацию, начало занятия и оплату в MyTracker — так они привязываются к
 * рекламе, с которой пришла установка. Подробно: docs/mytracker.md.
 */

import { isAppMode } from "./appMode";

const KEY = "et_mt_iid";
const IID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const linking = new Map<string, Promise<void>>();

/** Native install identity must not be attached to ordinary browser visits. */
export function getInstallId(): string {
  if (!isAppMode()) return "";
  try {
    const iid = localStorage.getItem(KEY) || "";
    return IID_RE.test(iid) ? iid : "";
  } catch { return ""; }
}

/** Забрать mt_iid из адреса (и убрать его оттуда, чтобы не светился в
 *  ссылках и не попадал в аналитику как часть URL). Вызывается на старте. */
export function captureInstallId(): void {
  try {
    const url = new URL(window.location.href);
    const iid = (url.searchParams.get("mt_iid") || "").trim();
    if (!iid) return;
    if (IID_RE.test(iid)) {
      try { localStorage.setItem(KEY, iid); } catch { /* still scrub the URL */ }
    }
    url.searchParams.delete("mt_iid");
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  } catch { /* приватный режим или старый браузер — не критично */ }
}

/** Refresh last_seen_at on each login; deduplication belongs to the server.
 * A permanent client marker previously hid links even after failed S2S sends. */
export async function linkInstall(userId: number | null | undefined, apiBase: string): Promise<void> {
  if (!userId) return;
  const iid = getInstallId();
  if (!iid) return;
  const mark = `${userId}:${iid}`;
  const existing = linking.get(mark);
  if (existing) return existing;
  const request = (async () => {
    try {
      await fetch(`${apiBase}/api/analytics/mt-install`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instance_id: iid }),
      });
    } catch { /* retry on the next account refresh */ }
    finally { linking.delete(mark); }
  })();
  linking.set(mark, request);
  return request;
}
