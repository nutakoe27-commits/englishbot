// ModeSelector.tsx — стартовый экран mini-app: выбор режима тренировки.
// Speaking — существующий голосовой тьютор; Listening — генерация подкаста.

import WebApp from "@twa-dev/sdk";
import { useEffect, useState } from "react";

export type Mode = "speaking" | "listening";

interface Props {
  onPick: (mode: Mode) => void;
}

export function ModeSelector({ onPick }: Props) {
  const [userName, setUserName] = useState<string>("there");

  useEffect(() => {
    try { WebApp.ready(); } catch { /* старые клиенты */ }
    try { WebApp.expand(); } catch { /* старые клиенты */ }
    const user = WebApp.initDataUnsafe?.user;
    if (user?.first_name) setUserName(user.first_name);
  }, []);

  return (
    <div className="mode-selector">
      <div className="bg-orb bg-orb--one" aria-hidden />
      <div className="bg-orb bg-orb--two" aria-hidden />

      <header className="mode-selector__header">
        <div className="tutor-brand">
          <span className="tutor-brand__dot" aria-hidden />
          <span className="tutor-brand__name">English Tutor</span>
        </div>
        <p className="tutor-hello">Hi, {userName}</p>
      </header>

      <main className="mode-selector__main">
        <h1 className="mode-selector__title">Что тренируем сегодня?</h1>
        <p className="mode-selector__subtitle">
          Выбери режим — слова и прогресс общие.
        </p>

        <div className="mode-cards">
          <button
            type="button"
            className="mode-card"
            onClick={() => onPick("speaking")}
          >
            <span className="mode-card__emoji" aria-hidden>🎙️</span>
            <span className="mode-card__title">Разговор</span>
            <span className="mode-card__hint">
              Push-to-talk диалог с AI-тьютором.
            </span>
          </button>

          <button
            type="button"
            className="mode-card"
            onClick={() => onPick("listening")}
          >
            <span className="mode-card__emoji" aria-hidden>🎧</span>
            <span className="mode-card__title">Слушание</span>
            <span className="mode-card__hint">
              Сгенерированный подкаст на твою тему и слова.
            </span>
          </button>
        </div>
      </main>
    </div>
  );
}
