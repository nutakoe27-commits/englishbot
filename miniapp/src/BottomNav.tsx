/**
 * BottomNav — 4 главных таба (Home/Stats/Vocab/Profile) в стиле v2 DS:
 * sliding sage pill индикатор, lucide-иконки, frosted background.
 */

import { Icon } from "./ds-react/Icon";
import type { TabKey } from "./tabs";

interface Props {
  active: TabKey;
  onChange: (tab: TabKey) => void;
}

const TABS: { key: TabKey; icon: string; label: string }[] = [
  { key: "home",     icon: "house",                label: "Главная"  },
  { key: "progress", icon: "chart-no-axes-column", label: "Прогресс" },
  { key: "words",    icon: "layers",               label: "Слова"    },
  { key: "profile",  icon: "user-round",           label: "Профиль"  },
];

export function BottomNav({ active, onChange }: Props) {
  const idx = Math.max(0, TABS.findIndex((t) => t.key === active));
  return (
    <nav className="bnav" aria-label="Основная навигация">
      <span
        className="bnav__pill"
        aria-hidden
        style={{
          width: `calc((100% - 16px) / ${TABS.length})`,
          transform: `translateX(${idx * 100}%)`,
        }}
      />
      {TABS.map((t) => (
        <button
          key={t.key}
          type="button"
          className={`bnav__btn ${active === t.key ? "is-active" : ""}`}
          onClick={() => onChange(t.key)}
          aria-current={active === t.key ? "page" : undefined}
        >
          <span className="bnav__icon"><Icon name={t.icon} size={22} /></span>
          <span className="bnav__label">{t.label}</span>
        </button>
      ))}
    </nav>
  );
}
