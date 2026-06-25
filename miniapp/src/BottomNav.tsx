/**
 * BottomNav — фиксированный таб-бар внизу экрана для главных секций.
 * 4 таба: Главная / Прогресс / Слова / Профиль.
 *
 * Использует DS-токены (warm-neutrals + coral accent). Стили в Landing.css
 * не лезут — свой префикс .bnav-.
 *
 * Показывается ТОЛЬКО на главных табах. На экранах режимов (Speaking,
 * Listening, Grammar) — скрыт, чтобы не мешать тренировке.
 */

import type { TabKey } from "./tabs";

interface Props {
  active: TabKey;
  onChange: (tab: TabKey) => void;
}

const TABS: { key: TabKey; label: string; icon: JSX.Element }[] = [
  { key: "home",     label: "Главная",  icon: <HomeIcon /> },
  { key: "progress", label: "Прогресс", icon: <ChartIcon /> },
  { key: "words",    label: "Слова",    icon: <BookIcon /> },
  { key: "profile",  label: "Профиль",  icon: <UserIcon /> },
];

export function BottomNav({ active, onChange }: Props) {
  return (
    <nav className="bnav" aria-label="Основная навигация">
      {TABS.map((t) => (
        <button
          key={t.key}
          type="button"
          className={`bnav__btn ${active === t.key ? "is-active" : ""}`}
          onClick={() => onChange(t.key)}
          aria-current={active === t.key ? "page" : undefined}
        >
          <span className="bnav__icon" aria-hidden>{t.icon}</span>
          <span className="bnav__label">{t.label}</span>
        </button>
      ))}
    </nav>
  );
}

/* ── Icons ─────────────────────────────────────────────────────────── */
function HomeIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 10.5L12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z" />
    </svg>
  );
}
function ChartIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 3v18h18" />
      <path d="M7 14l4-4 4 4 5-7" />
    </svg>
  );
}
function BookIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 4h12a4 4 0 0 1 4 4v12H8a4 4 0 0 1-4-4V4z" />
      <path d="M4 16a4 4 0 0 1 4-4h12" />
    </svg>
  );
}
function UserIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21a8 8 0 0 1 16 0" />
    </svg>
  );
}
