/**
 * appMode.ts — приложение это или сайт.
 *
 * Один и тот же код обслуживает две ситуации, и вести себя одинаково в них
 * нельзя. На сайт человек приходит по ссылке и его надо убедить: отсюда
 * лендинг с шапкой, ценами, FAQ и подвалом. Приложение он уже установил —
 * убеждать поздно, ему нужен вход и работа. Показывать в установленном
 * приложении маркетинговую страницу — значит выглядеть витриной, а не
 * продуктом; на этом мы и получили отказ модерации RuStore.
 *
 * Как определяем. `display-mode: standalone` — стандартный признак: его
 * даёт и установленная PWA, и TWA из магазина (там внутри Chrome с тем же
 * режимом отображения). Дополнительно смотрим `start_url`-метку и признак
 * Android-обёртки в user-agent — по отдельности каждый способ где-нибудь
 * даёт осечку, вместе они закрывают все три площадки.
 *
 * Решение фиксируется один раз при загрузке: `display-mode` меняется, если
 * человек развернул окно, и мигающий между режимами интерфейс — худшее из
 * возможного.
 */

const APP_FLAG_KEY = "et_app_mode";

function detect(): boolean {
  if (typeof window === "undefined") return false;
  try {
    // Основной признак: окно без браузерного обрамления.
    if (window.matchMedia?.("(display-mode: standalone)")?.matches) return true;
    if (window.matchMedia?.("(display-mode: fullscreen)")?.matches) return true;
    // iOS до сих пор отвечает только этим.
    if ((navigator as unknown as { standalone?: boolean }).standalone) return true;
    // TWA сообщает о себе заголовком, который Chrome превращает в этот
    // признак: приложение открыто из магазинной обёртки.
    if (document.referrer.startsWith("android-app://")) return true;
    // Метка из манифеста (start_url). Переживает переходы внутри SPA,
    // потому что мы её тут же запоминаем.
    const p = new URLSearchParams(window.location.search);
    if (p.get("source") === "pwa" || p.get("app") === "1") return true;
    // Запомненное с прошлого запуска: TWA открывает start_url только
    // первый раз, дальше человек может уйти вглубь и вернуться.
    return sessionStorage.getItem(APP_FLAG_KEY) === "1";
  } catch {
    return false;
  }
}

const IS_APP = detect();

if (IS_APP) {
  try { sessionStorage.setItem(APP_FLAG_KEY, "1"); } catch { /* приватный режим */ }
}

/** true — мы внутри установленного приложения (PWA или TWA из магазина). */
export function isAppMode(): boolean {
  return IS_APP;
}
