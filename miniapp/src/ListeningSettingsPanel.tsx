// ListeningSettingsPanel.tsx — секция config-фазы ListeningScreen.
// Чистый controlled-компонент: значение приходит через value, изменения уходят через onChange.

import {
  CATEGORY_OPTIONS,
  DURATION_PRESETS,
  MAX_CUSTOM_DURATION,
  SPEED_OPTIONS,
  type ListeningSettings,
} from "./listeningSettings";
import { LEVEL_OPTIONS } from "./tutorSettings";

interface Props {
  value: ListeningSettings;
  onChange: (next: ListeningSettings) => void;
}

export function ListeningSettingsPanel({ value, onChange }: Props) {
  const isCustomMode = value.durationMode === "custom";

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
              data-active={!isCustomMode && value.durationMin === n ? "true" : "false"}
              onClick={() =>
                onChange({ ...value, durationMode: "preset", durationMin: n })
              }
            >
              {n} мин
            </button>
          ))}
          <button
            type="button"
            className="lst-chip"
            data-active={isCustomMode ? "true" : "false"}
            onClick={() =>
              onChange({
                ...value,
                durationMode: "custom",
                // если переключаемся ИЗ preset В custom — оставляем текущее
                // значение durationMin, пользователь сам введёт другое.
              })
            }
          >
            Custom
          </button>
        </div>
        {isCustomMode && (
          <div className="lst-custom-input">
            <input
              type="number"
              inputMode="numeric"
              pattern="[0-9]*"
              min={1}
              max={MAX_CUSTOM_DURATION}
              step={1}
              value={value.durationMin}
              onChange={(e) => {
                const raw = e.target.value;
                if (raw === "") {
                  // временно сохраняем 1, чтобы поле можно было редактировать
                  onChange({ ...value, durationMode: "custom", durationMin: 1 });
                  return;
                }
                const parsed = parseInt(raw, 10);
                if (Number.isFinite(parsed)) {
                  const clamped = Math.min(MAX_CUSTOM_DURATION, Math.max(1, parsed));
                  onChange({ ...value, durationMode: "custom", durationMin: clamped });
                }
              }}
              onBlur={(e) => {
                const parsed = parseInt(e.target.value || "1", 10);
                const clamped = Math.min(
                  MAX_CUSTOM_DURATION,
                  Math.max(1, Number.isFinite(parsed) ? parsed : 1),
                );
                if (clamped !== value.durationMin) {
                  onChange({ ...value, durationMode: "custom", durationMin: clamped });
                }
              }}
              aria-label="Длительность в минутах"
            />
            <span className="lst-custom-input__suffix">мин</span>
            <span className="lst-custom-input__hint">от 1 до {MAX_CUSTOM_DURATION}</span>
          </div>
        )}
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
