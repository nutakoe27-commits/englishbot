// SettingsSheet.tsx — выдвижной bottom sheet с настройками тьютора.
// Открывается по тапу на кнопку-шестерёнку в шапке.

import { useEffect, useState } from "react";
import {
  LENGTH_OPTIONS,
  LEVEL_OPTIONS,
  MODE_OPTIONS,
  ROLE_PRESETS,
  type TutorSettings,
} from "./tutorSettings";
import { ModalScreen } from "./ModalScreen";
import { IconButton } from "./ds-react/IconButton";
import { SerifH } from "./ds-react/typography";

interface Props {
  initial: TutorSettings;
  onCancel: () => void;
  onSave: (next: TutorSettings) => void;
}

export function SettingsSheet({ initial, onCancel, onSave }: Props) {
  const [draft, setDraft] = useState<TutorSettings>(initial);

  // ESC закрывает
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const unchanged =
    draft.level === initial.level &&
    draft.role === initial.role &&
    draft.roleCustom.trim() === initial.roleCustom.trim() &&
    draft.length === initial.length &&
    draft.corrections === initial.corrections &&
    draft.mode === initial.mode;

  const canSave =
    draft.role !== "custom" || draft.roleCustom.trim().length > 0;

  return (
    <ModalScreen>
      <div className="settings-popover" role="dialog" aria-modal="true" aria-label="Tutor settings">
        <header className="settings-popover__header">
          <SerifH as="h2" size={24}>Settings</SerifH>
          <IconButton icon="x" size="md" label="Close" onClick={onCancel} />
        </header>

        <div className="settings-popover__content">
          {/* 1. Уровень */}
          <section className="sheet-group">
            <h3 className="sheet-group__title">Your English level</h3>
            <div className="segmented">
              {LEVEL_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  className={`segmented__item ${
                    draft.level === opt.value ? "is-active" : ""
                  }`}
                  onClick={() => setDraft((d) => ({ ...d, level: opt.value }))}
                >
                  <span className="segmented__label">{opt.label}</span>
                  <span className="segmented__hint">{opt.hint}</span>
                </button>
              ))}
            </div>
          </section>

          {/* 2. Роль собеседника */}
          <section className="sheet-group">
            <h3 className="sheet-group__title">Conversation partner</h3>
            <div className="role-grid">
              {ROLE_PRESETS.map((role) => (
                <button
                  key={role.value}
                  type="button"
                  className={`role-chip ${
                    draft.role === role.value ? "is-active" : ""
                  }`}
                  onClick={() => setDraft((d) => ({ ...d, role: role.value }))}
                >
                  <span className="role-chip__emoji" aria-hidden>
                    {role.emoji}
                  </span>
                  <span className="role-chip__label">{role.label}</span>
                </button>
              ))}
            </div>
            {draft.role === "custom" && (
              <input
                type="text"
                className="sheet-input"
                placeholder="e.g. a pirate captain, a chess coach, a tech support agent…"
                maxLength={200}
                value={draft.roleCustom}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, roleCustom: e.target.value }))
                }
                autoFocus
              />
            )}
          </section>

          {/* 3. Длина ответов */}
          <section className="sheet-group">
            <h3 className="sheet-group__title">Response length</h3>
            <div className="segmented">
              {LENGTH_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  className={`segmented__item ${
                    draft.length === opt.value ? "is-active" : ""
                  }`}
                  onClick={() => setDraft((d) => ({ ...d, length: opt.value }))}
                >
                  <span className="segmented__label">{opt.label}</span>
                  <span className="segmented__hint">{opt.hint}</span>
                </button>
              ))}
            </div>
          </section>

          {/* 4. Исправления */}
          <section className="sheet-group">
            <label className="switch-row">
              <div className="switch-row__text">
                <span className="switch-row__title">Correct my mistakes</span>
                <span className="switch-row__hint">
                  Partner shows the corrected phrase before replying
                </span>
              </div>
              <input
                type="checkbox"
                className="switch-row__input"
                checked={draft.corrections}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, corrections: e.target.checked }))
                }
              />
              <span className="switch" aria-hidden />
            </label>
          </section>

          {/* 5. Режим общения */}
          <section className="sheet-group">
            <h3 className="sheet-group__title">Mode</h3>
            <div className="segmented">
              {MODE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  className={`segmented__item ${
                    draft.mode === opt.value ? "is-active" : ""
                  }`}
                  onClick={() =>
                    setDraft((d) => ({ ...d, mode: opt.value }))
                  }
                >
                  <span className="segmented__label">{opt.label}</span>
                  <span className="segmented__hint">{opt.hint}</span>
                </button>
              ))}
            </div>
            <p className="sheet-note">
              Voice — hold the mic to talk and hear the reply. Chat —
              type and read, no audio at all.
            </p>
          </section>
        </div>

        <footer className="settings-popover__footer">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn--primary"
            disabled={unchanged || !canSave}
            onClick={() => onSave(draft)}
          >
            {unchanged ? "Saved" : "Apply"}
          </button>
        </footer>
      </div>
    </ModalScreen>
  );
}
