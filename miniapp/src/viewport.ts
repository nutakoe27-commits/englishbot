/**
 * viewport.ts — реальная высота видимой области в Telegram Mini App.
 *
 * Зачем. Все экраны сидели на `height: 100dvh`, но в вебвью Telegram
 * `100dvh` НЕ равен видимой области: клиент рисует поверх свою шапку, а на
 * Android вебвью вдобавок сообщает высоту вместе со свёрнутой частью
 * шторки. Экран получался выше, чем показывают, и это давало сразу два
 * симптома:
 *
 *   • нижняя часть контента уходила под обрез и была недостижима;
 *   • внутренний скролл-контейнер (`.mode-selector__main` и его аналоги)
 *     не переполнялся, поэтому палец в середине экрана ничего не прокручивал,
 *     а у краёв жест проваливался мимо контейнера и тащил шторку Telegram —
 *     со стороны это выглядело как «по краям прокручивается, в середине нет».
 *
 * Решение: публикуем настоящую высоту в CSS-переменную `--app-vh`, а в
 * стилях пишем `var(--app-vh, 100dvh)`. Вне Telegram (веб, локальная
 * разработка) переменная не задаётся и работает прежний `100dvh`.
 *
 * viewportStableHeight — высота БЕЗ учёта временных состояний вроде
 * открытой клавиатуры; именно она нужна для каркаса страницы.
 */

type TgWebApp = {
  viewportHeight?: number;
  viewportStableHeight?: number;
  isExpanded?: boolean;
  expand?: () => void;
  onEvent?: (e: string, cb: () => void) => void;
};

function tg(): TgWebApp | null {
  try {
    return (window as unknown as { Telegram?: { WebApp?: TgWebApp } })
      .Telegram?.WebApp ?? null;
  } catch {
    return null;
  }
}

function apply(): void {
  const w = tg();
  const h = w?.viewportStableHeight || w?.viewportHeight;
  const root = document.documentElement;
  if (typeof h === "number" && h > 200) {
    root.style.setProperty("--app-vh", `${Math.round(h)}px`);
  } else {
    // Не в Telegram или высота ещё не пришла — отдаём управление 100dvh.
    root.style.removeProperty("--app-vh");
  }
}

/** Вызывать один раз на старте, после WebApp.ready()/expand(). */
export function initViewport(): void {
  apply();
  const w = tg();
  // Telegram шлёт viewportChanged при разворачивании, повороте, клавиатуре.
  try {
    w?.onEvent?.("viewportChanged", apply);
  } catch { /* старый клиент — обойдёмся начальным значением */ }
  // Поворот экрана и ресайз в вебе: onEvent тут не приходит.
  window.addEventListener("resize", apply);
  window.addEventListener("orientationchange", () => {
    // после поворота Telegram отдаётновую высоту не сразу
    window.setTimeout(apply, 120);
    window.setTimeout(apply, 400);
  });
}
