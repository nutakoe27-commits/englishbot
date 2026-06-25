/**
 * lucide.ts — helper для рендеринга иконок lucide через CDN.
 *
 * lucide подключен как <script src="...unpkg.com/lucide@latest"> в index.html.
 * Иконки используются как <i data-lucide="user-round" /> в JSX, и lucide
 * заменяет их на inline <svg> при вызове window.lucide.createIcons().
 *
 * После каждого React-render надо повторно вызвать createIcons() — иначе
 * новые иконки в DOM останутся пустыми <i> элементами. Hook useLucide()
 * это делает в useEffect.
 */

import { useEffect } from "react";

declare global {
  interface Window {
    lucide?: {
      createIcons: (opts?: { attrs?: Record<string, string | number> }) => void;
    };
  }
}

/** Перерендерить все <i data-lucide> в SVG. Безопасно: no-op если lucide не загружен. */
export function renderIcons(): void {
  try {
    window.lucide?.createIcons({ attrs: { "stroke-width": "1.75" } });
  } catch {
    /* lucide ещё не загружен — попробуем при следующем render */
  }
}

/** Hook: перерендеривает иконки после каждого commit'a компонента.
 *  Передай зависимость, при изменении которой иконки должны обновиться (опц.). */
export function useLucide(dep?: unknown): void {
  useEffect(() => {
    renderIcons();
  }, [dep]);
}
