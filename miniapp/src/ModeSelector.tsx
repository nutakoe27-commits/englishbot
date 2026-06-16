// ModeSelector.tsx — стартовый экран mini-app: выбор режима тренировки.
// Speaking — существующий голосовой тьютор; Listening — генерация подкаста.

import WebApp from "@twa-dev/sdk";
import { useEffect, useState } from "react";
import { ProgressScreen } from "./ProgressScreen";
import { AccountSheet } from "./AccountSheet";

export type Mode = "speaking" | "listening" | "grammar" | "srs";

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  "https://api-english.krichigindocs.ru";

interface Props {
  onPick: (mode: Mode) => void;
  onLoggedOut?: () => void;
}

interface MeStats {
  streak: { current: number; best: number };
  total_minutes: number;
  total_words: number;
  achievements_earned: number;
  achievements_total: number;
}

export function ModeSelector({ onPick, onLoggedOut }: Props) {
  const [userName, setUserName] = useState<string>("there");
  const [stats, setStats] = useState<MeStats | null>(null);
  const [progressOpen, setProgressOpen] = useState<boolean>(false);
  const [accountOpen, setAccountOpen] = useState<boolean>(false);

  useEffect(() => {
    try { WebApp.ready(); } catch { /* старые клиенты */ }
    try { WebApp.expand(); } catch { /* старые клиенты */ }
    // Без этого свайп вниз при скролле карточек сворачивает Mini App.
    try { WebApp.disableVerticalSwipes?.(); } catch { /* старые клиенты */ }
    const user = WebApp.initDataUnsafe?.user;
    if (user?.first_name) setUserName(user.first_name);

    // Статистика на главном экране — чтобы прогресс был виден сразу.
    // На вебе JWT подставляется автоматически (installFetchAuth), в Telegram
    // дополнительно передаём init_data как fallback. Тихо игнорируем ошибки.
    const initData = WebApp.initData || "";
    const url = initData
      ? `${API_BASE}/api/me/progress?init_data=${encodeURIComponent(initData)}`
      : `${API_BASE}/api/me/progress`;
    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setStats(d as MeStats); })
      .catch(() => { /* нет статистики — не критично */ });
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
        <div className="mode-selector__header-right">
          <p className="tutor-hello">Hi, {userName}</p>
          <button
            type="button"
            className="icon-button"
            onClick={() => setAccountOpen(true)}
            aria-label="Аккаунт и настройки входа"
            title="Аккаунт"
          >
            <span style={{ fontSize: 18, lineHeight: 1 }} aria-hidden>👤</span>
          </button>
        </div>
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
            <span className="mode-card__text">
              <span className="mode-card__title">Разговор</span>
              <span className="mode-card__hint">
                Push-to-talk диалог с AI-тьютором.
              </span>
            </span>
          </button>

          <button
            type="button"
            className="mode-card"
            onClick={() => onPick("listening")}
          >
            <span className="mode-card__emoji" aria-hidden>🎧</span>
            <span className="mode-card__text">
              <span className="mode-card__title">Слушание</span>
              <span className="mode-card__hint">
                Подкаст на твою тему и слова.
              </span>
            </span>
          </button>

          <button
            type="button"
            className="mode-card"
            onClick={() => onPick("grammar")}
          >
            <span className="mode-card__emoji" aria-hidden>📝</span>
            <span className="mode-card__text">
              <span className="mode-card__title">Грамматика</span>
              <span className="mode-card__hint">
                Уроки A1–C1 и разбор твоих ошибок.
              </span>
            </span>
          </button>

          <button
            type="button"
            className="mode-card"
            onClick={() => onPick("srs")}
          >
            <span className="mode-card__emoji" aria-hidden>📚</span>
            <span className="mode-card__text">
              <span className="mode-card__title">Слова</span>
              <span className="mode-card__hint">
                Карточки на повтор — интервальное запоминание.
              </span>
            </span>
          </button>
        </div>

        {stats && (
          <button
            type="button"
            className="ms-stats"
            aria-label="Моя статистика — открыть полный прогресс"
            onClick={() => setProgressOpen(true)}
          >
            <div className="ms-stat">
              <span className="ms-stat__value">🔥 {stats.streak.current}</span>
              <span className="ms-stat__label">дней подряд</span>
            </div>
            <div className="ms-stat">
              <span className="ms-stat__value">⏱ {stats.total_minutes}</span>
              <span className="ms-stat__label">минут практики</span>
            </div>
            <div className="ms-stat">
              <span className="ms-stat__value">📚 {stats.total_words}</span>
              <span className="ms-stat__label">слов в словаре</span>
            </div>
            <div className="ms-stat">
              <span className="ms-stat__value">
                🏅 {stats.achievements_earned}/{stats.achievements_total}
              </span>
              <span className="ms-stat__label">медалей</span>
            </div>
          </button>
        )}
      </main>

      {progressOpen && (
        <ProgressScreen
          apiBase={API_BASE}
          initData={WebApp.initData || ""}
          onClose={() => setProgressOpen(false)}
        />
      )}

      {accountOpen && (
        <AccountSheet
          onClose={() => setAccountOpen(false)}
          onLoggedOut={() => {
            setAccountOpen(false);
            onLoggedOut?.();
          }}
        />
      )}
    </div>
  );
}
