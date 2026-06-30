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
  { key: "home",        icon: "house",      label: "Главная"   },
  { key: "leaderboard", icon: "trophy",     label: "Лидерборд" },
  { key: "words",       icon: "layers",     label: "Слова"     },
  { key: "profile",     icon: "user-round", label: "Профиль"   },
];

export function BottomNav({ active, onChange }: Props) {
  const idx = Math.max(0, TABS.findIndex((t) => t.key === active));
  const PILL_INSET = 6;            // визуальный буфер по краям пилюли
  return (
    <nav className="bnav" aria-label="Основная навигация">
      <span
        className="bnav__pill"
        aria-hidden
        style={{
          // Сетка из 4 равных колонок без gap (см. .bnav grid-template).
          // Пилюля = 25% ширины - 2*INSET, позиционируется через left.
          // Это точнее, чем translateX от width, потому что 25% и left:0%
          // считаются от ОДНОГО padding-box → нет рассинхрона на десктопе.
          width: `calc(25% - ${PILL_INSET * 2}px)`,
          left: `calc(${idx * 25}% + ${PILL_INSET}px)`,
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
