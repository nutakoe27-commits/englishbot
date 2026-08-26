/**
 * sw.js — service worker сайта English Tutor.
 *
 * Делает три вещи, и все три нужны:
 *
 * 1. Даёт странице право называться устанавливаемой. Chrome требует
 *    зарегистрированный worker с обработчиком fetch, который умеет ответить
 *    без сети, — иначе не предложит «Установить приложение» и не даст
 *    собрать TWA для магазина.
 * 2. Показывает офлайн-заглушку вместо ошибки браузера.
 * 3. Принимает web push. Это и есть главная причина всей затеи: канал до
 *    человека, не зависящий от Telegram.
 *
 * Чего worker НЕ делает — намеренно:
 *
 * • не кеширует index.html. У него стоит no-store и в nginx, и в <meta>:
 *   иначе после выката человек грузит старую страницу со ссылкой на
 *   несуществующий /assets/index-OLDHASH.js и видит «фичи не работают».
 *   Этот сценарий у нас уже был, повторять его через кеш worker'а нельзя.
 * • не трогает запросы к API. Они уходят на другой origin, а всё
 *   кросс-доменное здесь пропускается без обработки.
 */

// Версию поднимать при изменении набора предкешируемых файлов — старый кеш
// целиком удаляется в activate.
const CACHE = "et-v1";
const OFFLINE_URL = "/offline.html";
const PRECACHE = [OFFLINE_URL, "/icon-192.png", "/favicon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(PRECACHE))
      // Отсутствие одного файла не должно оставлять сайт без worker'а.
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  let url;
  try {
    url = new URL(req.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;   // API и CDN — мимо

  // Переходы по страницам: сначала сеть, при обрыве — офлайн-заглушка.
  // Сам ответ не кешируем (см. про index.html выше).
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() =>
        caches.match(OFFLINE_URL).then((r) => r || new Response(
          "Нет соединения", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } },
        )),
      ),
    );
    return;
  }

  // Собранная статика: имена содержат хеш содержимого, поэтому её можно
  // отдавать из кеша не спрашивая сеть — при выкате имена меняются.
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => undefined);
        }
        return res;
      })),
    );
  }
});

// ─── Web push ──────────────────────────────────────────────────────────────
// Полезная нагрузка — JSON от backend: {title, body, url, tag}. Если пришло
// что-то другое (или пустой push от браузера при проверке подписки), всё
// равно показываем осмысленное уведомление: молча игнорировать push нельзя,
// браузеры за это отзывают разрешение.

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    try { data = { body: event.data.text() }; } catch { data = {}; }
  }
  const title = data.title || "English Tutor";
  const options = {
    body: data.body || "Пора позаниматься английским.",
    icon: data.icon || "/icon-192.png",
    badge: "/icon-192.png",
    lang: "ru",
    // tag схлопывает однотипные уведомления: три напоминания подряд не
    // должны превращаться в три строки в шторке.
    tag: data.tag || "et-generic",
    renotify: !!data.tag,
    requireInteraction: false,
    data: { url: data.url || "/" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      // Уже открытая вкладка приложения — переиспользуем её, а не плодим новые.
      for (const client of list) {
        if (client.url.includes(self.location.origin) && "focus" in client) {
          client.navigate(target).catch(() => undefined);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    }),
  );
});
