/**
 * pwa.ts — регистрация service worker'а.
 *
 * Worker нужен для трёх вещей сразу: установки приложения на домашний экран
 * (и сборки TWA для магазина), офлайн-заглушки и приёма web push. Логика
 * лежит в public/sw.js — он не проходит через сборку намеренно: файл должен
 * быть доступен по фиксированному пути /sw.js, иначе область действия
 * worker'а не покроет весь сайт.
 *
 * Регистрируем только в браузере и только на https (или localhost) — в
 * остальных случаях API просто отсутствует. Ошибка регистрации ничего не
 * ломает: сайт продолжает работать как обычная страница.
 */

let registration: ServiceWorkerRegistration | null = null;

/** Готовая регистрация — нужна для подписки на push. */
export function getRegistration(): ServiceWorkerRegistration | null {
  return registration;
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) return null;
  try {
    registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    // Проверяем обновление сразу: браузер сам сходит за новым sw.js не раньше
    // чем через сутки, а нам нужно, чтобы правки доезжали с выкатом.
    void registration.update().catch(() => undefined);
    return registration;
  } catch {
    return null;
  }
}

/** Дождаться активного worker'а. Подписка на push без него невозможна. */
export async function readyServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) return null;
  try {
    const reg = await navigator.serviceWorker.ready;
    registration = reg;
    return reg;
  } catch {
    return null;
  }
}
