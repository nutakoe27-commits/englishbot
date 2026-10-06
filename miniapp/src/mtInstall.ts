/**
 * mtInstall.ts — связка установки Android-приложения с аккаунтом (MyTracker).
 *
 * Нативная обёртка (TWA) при запуске добавляет к стартовому адресу
 * ?mt_iid=<instanceId> из SDK MyTracker. Здесь мы его забираем из адреса,
 * храним в localStorage и после входа один раз отдаём бэкенду. Бэкенд по
 * нему шлёт регистрацию и оплату в MyTracker — так они привязываются к
 * рекламе, с которой пришла установка. Подробно: docs/mytracker.md.
 */

const KEY = "et_mt_iid";
const SENT_KEY = "et_mt_iid_sent";
const IID_RE = /^[A-Za-z0-9\-_.]{8,64}$/;

/** Забрать mt_iid из адреса (и убрать его оттуда, чтобы не светился в
 *  ссылках и не попадал в аналитику как часть URL). Вызывается на старте. */
export function captureInstallId(): void {
  try {
    const url = new URL(window.location.href);
    const iid = (url.searchParams.get("mt_iid") || "").trim();
    if (!iid) return;
    if (IID_RE.test(iid)) localStorage.setItem(KEY, iid);
    url.searchParams.delete("mt_iid");
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  } catch { /* приватный режим или старый браузер — не критично */ }
}

/** Отдать установку бэкенду для вошедшего пользователя. Повторно для той же
 *  пары «аккаунт + установка» не шлёт. Ошибки молча игнорируются. */
export async function linkInstall(userId: number | null | undefined, apiBase: string): Promise<void> {
  if (!userId) return;
  let iid = "";
  try { iid = localStorage.getItem(KEY) || ""; } catch { return; }
  if (!iid) return;
  const mark = `${userId}:${iid}`;
  try { if (localStorage.getItem(SENT_KEY) === mark) return; } catch { return; }
  try {
    const res = await fetch(`${apiBase}/api/analytics/mt-install`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instance_id: iid }),
    });
    if (res.ok) localStorage.setItem(SENT_KEY, mark);
  } catch { /* сеть — попробуем при следующем входе */ }
}
