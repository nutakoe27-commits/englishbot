/**
 * ThemeToggle — кнопка-иконка для смены темы. Циклит light → dark → system.
 * Использует .et-icon-btn из DS, иконки SVG inline (без зависимостей).
 */

import { useEffect, useState } from "react";
import { getStoredTheme, setStoredTheme, type Theme } from "./theme";

interface Props {
  className?: string;
}

const NEXT: Record<Theme, Theme> = {
  light: "dark",
  dark: "system",
  system: "light",
};

const LABEL: Record<Theme, string> = {
  light: "Светлая тема",
  dark: "Тёмная тема",
  system: "Системная тема",
};

export function ThemeToggle({ className = "" }: Props) {
  const [theme, setTheme] = useState<Theme>(() => getStoredTheme());

  useEffect(() => { setStoredTheme(theme); }, [theme]);

  const toggle = () => setTheme((t) => NEXT[t]);

  return (
    <button
      type="button"
      className={`et-icon-btn et-icon-btn--ghost ${className}`}
      onClick={toggle}
      aria-label={LABEL[theme]}
      title={LABEL[theme]}
    >
      {theme === "light" && <SunIcon />}
      {theme === "dark" && <MoonIcon />}
      {theme === "system" && <SystemIcon />}
    </button>
  );
}

function SunIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

function SystemIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="2" y="4" width="20" height="14" rx="2" />
      <path d="M8 21h8M12 18v3" />
    </svg>
  );
}
