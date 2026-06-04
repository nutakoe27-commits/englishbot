// ListeningSettingsPanel.tsx — секция config-фазы ListeningScreen.
// Чистый controlled-компонент: значение приходит через value, изменения уходят через onChange.

import {
  CATEGORY_OPTIONS,
  DURATION_PRESETS,
  MAX_CUSTOM_DURATION,
  SPEED_OPTIONS,
  type ListeningSettings,
} from "./listeningSettings";

interface Props {
  value: ListeningSettings;
  onChange: (next: ListeningSettings) => void;
}

export function ListeningSettingsPanel({ value, onChange }: Props) {
  const isCustomDuration = !DURATION_PRESETS.includes(value.durationMin);

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
              data-active={value.durationMin === n && !isCustomDuration ? "true" : "false"}
              onClick={() => onChange({ ...value, durationMin: n })}
            >
              {n} мин
            </button>
          ))}
          <button
            type="button"
            className="lst-chip"
            data-active={isCustomDuration ? "true" : "false"}
            onClick={() =>
              onChange({
                ...value,
                durationMin: isCustomDuration ? value.durationMin : 7,
              })
            }
          >
            Custom
          </button>
        </div>
        {isCustomDuration && (
          <div className="lst-slider">
            <input
              type="range"
              min={1}
              max={MAX_CUSTOM_DURATION}
              step={1}
              value={value.durationMin}
              onChange={(e) =>
                onChange({ ...value, durationMin: parseInt(e.target.value, 10) })
              }
            />
            <span className="lst-slider__value">{value.durationMin} мин</span>
          </div>
        )}
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
