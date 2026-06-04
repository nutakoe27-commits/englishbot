// ListeningSettingsPanel.tsx — секция config-фазы ListeningScreen.
// Чистый controlled-компонент: значение приходит через value, изменения уходят через onChange.

import {
  CATEGORY_OPTIONS,
  DURATION_PRESETS,
  SPEED_OPTIONS,
  type ListeningSettings,
} from "./listeningSettings";
import { LEVEL_OPTIONS } from "./tutorSettings";

interface Props {
  value: ListeningSettings;
  onChange: (next: ListeningSettings) => void;
}

export function ListeningSettingsPanel({ value, onChange }: Props) {
  return (
    <div className="lst-config">
      {/* Длительность */}
      <section className="lst-section">
        <h3 className="lst-section__title">Длительность</h3>
        <div className="lst-chips">
          {DURATION_PRESETS.map((n) => (
            <button
              key={n}
              type="button"
              className="lst-chip"
              data-active={value.durationMin === n ? "true" : "false"}
              onClick={() =>
                onChange({ ...value, durationMode: "preset", durationMin: n })
              }
            >
              {n} мин
            </button>
          ))}
        </div>
      </section>

      {/* Уровень */}
      <section className="lst-section">
        <h3 className="lst-section__title">Уровень</h3>
        <div className="lst-chips">
          {LEVEL_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              className="lst-chip lst-chip--level"
              data-active={value.level === opt.value ? "true" : "false"}
              onClick={() => onChange({ ...value, level: opt.value })}
              title={opt.hint}
            >
              <span className="lst-chip__main">{opt.label}</span>
              <span className="lst-chip__sub">{opt.hint}</span>
            </button>
          ))}
        </div>
      </section>

      {/* Категория */}
      <section className="lst-section">
        <h3 className="lst-section__title">Тема</h3>
        <div className="lst-categories">
          {CATEGORY_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              className="lst-category"
              data-active={value.category === opt.value ? "true" : "false"}
              onClick={() => onChange({ ...value, category: opt.value })}
            >
              <span className="lst-category__emoji" aria-hidden>
                {opt.emoji}
              </span>
              <span className="lst-category__label">{opt.label}</span>
            </button>
          ))}
        </div>
      </section>

      {/* Тумблер «учитывать мои слова» */}
      <section className="lst-section">
        <label className="lst-toggle">
          <span className="lst-toggle__main">
            <span className="lst-toggle__title">Учитывать мои слова</span>
            <span className="lst-toggle__hint">
              LLM вплетёт топ-слова из словаря в подкаст.
            </span>
          </span>
          <input
            type="checkbox"
            checked={value.useVocab}
            onChange={(e) => onChange({ ...value, useVocab: e.target.checked })}
          />
        </label>
      </section>

      {/* Скорость речи */}
      <section className="lst-section">
        <h3 className="lst-section__title">Скорость речи</h3>
        <div className="lst-chips">
          {SPEED_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              className="lst-chip"
              data-active={value.speed === opt.value ? "true" : "false"}
              onClick={() => onChange({ ...value, speed: opt.value })}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
