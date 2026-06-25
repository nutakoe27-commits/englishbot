/**
 * theme.ts — управление светлой/тёмной темой (DS поддерживает обе через
 * data-theme на <html>).
 *
 * Источник истины — атрибут <html data-theme="light"|"dark">. Если атрибута
 * нет — действует :root из DS (он light).
 *
 * Логика выбора:
 *   1. localStorage("et_theme") — явный выбор юзера.
 *   2. prefers-color-scheme: dark — системное предпочтение.
 *   3. По умолчанию — dark (исторически приложение было тёмным).
 */

export type Theme = "light" | "dark" | "system";
const STORAGE_KEY = "et_theme";

function systemDark(): boolean {
  if (typeof window === "undefined") return true;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? true;
}

function resolved(theme: Theme): "light" | "dark" {
  return theme === "system" ? (systemDark() ? "dark" : "light") : theme;
}

export function getStoredTheme(): Theme {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch { /* ignore */ }
  return "system";
}

export function setStoredTheme(theme: Theme): void {
  try { localStorage.setItem(STORAGE_KEY, theme); } catch { /* ignore */ }
  applyTheme(theme);
}

export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute("data-theme", resolved(theme));
}

/** Инициализация при загрузке: ставим тему из localStorage / системы / dark. */
export function initTheme(): void {
  applyTheme(getStoredTheme());
  // Реагируем на смену системной темы (только когда выбран mode='system').
  try {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener?.("change", () => {
      if (getStoredTheme() === "system") applyTheme("system");
    });
  } catch { /* ignore */ }
}
