/**
 * ProgressScreen.tsx — экран «Мой прогресс» (retention v1).
 *
 * Показывает streak, общую статистику, бары практики за 30 дней
 * и сетку медалей (earned / locked с прогрессом).
 *
 * REST:
 *   GET /api/me/progress?init_data=…       → {streak, total_*, daily_usage}
 *   GET /api/me/achievements?init_data=…   → {achievements: [...]}
 */

import { useEffect, useState } from "react";

interface Props {
  apiBase: string;
  initData: string;
  onClose: () => void;
}

interface DailyPoint {
  date: string;
  minutes: number;
}

interface Progress {
  streak: { current: number; best: number; last_practice_date: string | null };
  total_minutes: number;
  total_sessions: number;
  total_words: number;
  daily_usage: DailyPoint[];
  speaking_minutes?: number;
  listening_minutes?: number;
}

interface Achievement {
  key: string;
  title_ru: string;
  description_ru: string;
  icon: string;
  metric: string;
  target: number;
  current_value: number;
  earned: boolean;
}

export function ProgressScreen({ apiBase, initData, onClose }: Props) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [achievements, setAchievements] = useState<Achievement[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const enc = encodeURIComponent;
    Promise.all([
      fetch(`${apiBase}/api/me/progress?init_data=${enc(initData)}`).then((r) =>
        r.ok ? r.json() : Promise.reject(new Error(`progress ${r.status}`)),
      ),
      fetch(`${apiBase}/api/me/achievements?init_data=${enc(initData)}`).then(
        (r) => (r.ok ? r.json() : Promise.reject(new Error(`ach ${r.status}`))),
      ),
    ])
      .then(([p, a]) => {
        if (cancelled) return;
        setProgress(p);
        setAchievements(a.achievements ?? []);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [apiBase, initData]);

  // Высоту бара считаем относительно максимума за период; если макс 0 —
  // 0 во всех барах, рисуем плоскую серую полоску.
  const maxMinutes = progress
    ? Math.max(1, ...progress.daily_usage.map((d) => d.minutes))
    : 1;

  return (
    <div className="progress-modal" onClick={onClose}>
      <div className="progress-modal__sheet" onClick={(e) => e.stopPropagation()}>
        <div className="progress-modal__header">
          <h2 className="progress-modal__title">Мой прогресс</h2>
          <button
            type="button"
            className="progress-modal__close"
            onClick={onClose}
            aria-label="Закрыть"
          >
            ✕
          </button>
        </div>

        {error && (
          <div className="progress-modal__error">Не удалось загрузить: {error}</div>
        )}

        {!error && progress === null && (
          <div className="progress-modal__loading">Загружаем…</div>
        )}

        {progress && (
          <>
            <div className="progress-streak">
              <div className="progress-streak__icon">🔥</div>
              <div>
                <div className="progress-streak__value">
                  {progress.streak.current}
                  <span className="progress-streak__unit"> дн.</span>
                </div>
                <div className="progress-streak__sub">
                  Рекорд: {progress.streak.best}
                </div>
              </div>
            </div>

            <div className="progress-metrics">
              <Metric label="Минут практики" value={progress.total_minutes} />
              <Metric label="Сессий" value={progress.total_sessions} />
              <Metric label="Слов" value={progress.total_words} />
            </div>

            {(progress.speaking_minutes !== undefined ||
              progress.listening_minutes !== undefined) && (
              <div className="progress-breakdown">
                <div className="progress-breakdown__row">
                  <span className="progress-breakdown__emoji" aria-hidden>🎙️</span>
                  <span className="progress-breakdown__label">Разговор</span>
                  <span className="progress-breakdown__value">
                    {progress.speaking_minutes ?? 0} мин
                  </span>
                </div>
                <div className="progress-breakdown__row">
                  <span className="progress-breakdown__emoji" aria-hidden>🎧</span>
                  <span className="progress-breakdown__label">Слушание</span>
                  <span className="progress-breakdown__value">
                    {progress.listening_minutes ?? 0} мин
                  </span>
                </div>
              </div>
            )}

            <div className="progress-card">
              <h3 className="progress-card__title">Последние 30 дней</h3>
              <div className="daily-bars">
                {progress.daily_usage.map((d) => {
                  const heightPct = Math.round((d.minutes / maxMinutes) * 100);
                  return (
                    <div
                      key={d.date}
                      className="daily-bars__bar"
                      title={`${d.date}: ${d.minutes} мин`}
                    >
                      <div
                        className="daily-bars__fill"
                        style={{ height: `${Math.max(2, heightPct)}%` }}
                        data-empty={d.minutes === 0 ? "1" : "0"}
                      />
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="progress-card">
              <h3 className="progress-card__title">Медали</h3>
              {achievements === null ? (
                <div className="progress-modal__loading">Загружаем…</div>
              ) : (
                <div className="achievements-grid">
                  {achievements.map((a) => (
                    <AchievementCard key={a.key} a={a} />
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="progress-metric">
      <div className="progress-metric__value">{value}</div>
      <div className="progress-metric__label">{label}</div>
    </div>
  );
}

function AchievementCard({ a }: { a: Achievement }) {
  const progressPct =
    a.target > 0 ? Math.min(100, Math.round((a.current_value / a.target) * 100)) : 0;
  return (
    <div className={`achievement ${a.earned ? "achievement--earned" : "achievement--locked"}`}>
      <div className="achievement__icon" aria-hidden>
        {a.icon}
      </div>
      <div className="achievement__body">
        <div className="achievement__title">{a.title_ru}</div>
        <div className="achievement__desc">{a.description_ru}</div>
        {!a.earned && (
          <>
            <div
              className="achievement__bar"
              role="progressbar"
              aria-valuenow={progressPct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="achievement__bar-fill"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <div className="achievement__progress-text">
              {a.current_value} / {a.target}
            </div>
          </>
        )}
        {a.earned && <div className="achievement__earned-badge">Получено ✓</div>}
      </div>
    </div>
  );
}
