/**
 * ProgressScreen.tsx — экран «Мой прогресс» (retention v1).
 *
 * Показывает streak, общую статистику, бары практики за 30 дней
 * и сетку медалей (earned / locked с прогрессом).
 *
 * REST:
 *   GET /api/me/progress?init_data=…       → {streak, total_*, daily_usage}
 *   GET /api/me/achievements?init_data=…   → {achievements: [...]}
 *
 * UI v2: warm cream notebook tab — animated flame streak, NoteCard'ы,
 * sage bars + butter-yellow медали.
 */

import { useEffect, useState } from "react";
import { NoteCard } from "./ds-react/NoteCard";
import { SerifH } from "./ds-react/typography";
import { Badge } from "./ds-react/Badge";
import { Icon } from "./ds-react/Icon";
import { IconButton } from "./ds-react/IconButton";
import { useLucide } from "./lucide";

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
  grammar_minutes?: number;
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

  useLucide(`${progress ? "p" : "no"}-${achievements?.length ?? 0}`);

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

  const maxMinutes = progress
    ? Math.max(1, ...progress.daily_usage.map((d) => d.minutes))
    : 1;

  return (
    <div className="prog-v2">
      <header className="prog-v2__top">
        <SerifH as="h1" size={28}>Мой прогресс</SerifH>
        <IconButton icon="x" size="md" label="Закрыть" onClick={onClose} />
      </header>

      {error && (
        <NoteCard padding={16} tone="warn">
          <p style={{ margin: 0, fontSize: 14, color: "var(--text)" }}>
            Не удалось загрузить: {error}
          </p>
        </NoteCard>
      )}

      {!error && progress === null && (
        <p className="prog-v2__loading">Загружаем…</p>
      )}

      {progress && (
        <>
          {/* ── Streak hero ─────────────────────────────────────────── */}
          <NoteCard padding="20px 22px">
            <div className="prog-v2__streak">
              <div className="prog-v2__flame" aria-hidden>
                <Icon name="flame" size={36} />
              </div>
              <div>
                <div className="prog-v2__streak-val">
                  {progress.streak.current}
                  <span className="prog-v2__streak-unit"> дн.</span>
                </div>
                <div className="prog-v2__streak-sub">
                  Рекорд: {progress.streak.best}
                </div>
              </div>
            </div>
          </NoteCard>

          {/* ── 3 big stats ─────────────────────────────────────────── */}
          <div className="prog-v2__row3">
            <NoteCard padding="14px 14px">
              <div className="prog-v2__stat-val">{progress.total_minutes}</div>
              <div className="prog-v2__stat-label">Минут практики</div>
            </NoteCard>
            <NoteCard padding="14px 14px">
              <div className="prog-v2__stat-val">{progress.total_sessions}</div>
              <div className="prog-v2__stat-label">Сессий</div>
            </NoteCard>
            <NoteCard padding="14px 14px">
              <div className="prog-v2__stat-val">{progress.total_words}</div>
              <div className="prog-v2__stat-label">Слов</div>
            </NoteCard>
          </div>

          {/* ── By mode ─────────────────────────────────────────────── */}
          {(progress.speaking_minutes !== undefined ||
            progress.listening_minutes !== undefined ||
            progress.grammar_minutes !== undefined) && (
            <NoteCard padding={0}>
              <ByModeRow icon="mic" label="Разговор" value={`${progress.speaking_minutes ?? 0} мин`} first />
              <ByModeRow icon="headphones" label="Слушание" value={`${progress.listening_minutes ?? 0} мин`} />
              <ByModeRow icon="book-open" label="Грамматика" value={`${progress.grammar_minutes ?? 0} мин`} />
            </NoteCard>
          )}

          {/* ── 30-day bars ─────────────────────────────────────────── */}
          <NoteCard padding="18px 16px">
            <SerifH as="h3" size={20} style={{ marginBottom: 14 }}>Последние 30 дней</SerifH>
            <div className="prog-v2__bars">
              {progress.daily_usage.map((d) => {
                const heightPct = Math.round((d.minutes / maxMinutes) * 100);
                const tall = d.minutes >= maxMinutes * 0.55;
                return (
                  <div
                    key={d.date}
                    className={`prog-v2__bar ${tall ? "is-tall" : ""}`}
                    title={`${d.date}: ${d.minutes} мин`}
                    style={{ height: `${Math.max(6, heightPct)}%` }}
                  />
                );
              })}
            </div>
          </NoteCard>

          {/* ── Medals ──────────────────────────────────────────────── */}
          <div>
            <SerifH as="h3" size={20} style={{ marginBottom: 10 }}>Медали</SerifH>
            {achievements === null ? (
              <p className="prog-v2__loading">Загружаем…</p>
            ) : (
              <div className="prog-v2__medals">
                {achievements.map((a) => (
                  <MedalCard key={a.key} a={a} />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function ByModeRow({ icon, label, value, first }: { icon: string; label: string; value: string; first?: boolean }) {
  return (
    <div className={`prog-v2__bymode ${first ? "" : "has-divider"}`}>
      <Icon name={icon} size={16} />
      <span className="prog-v2__bymode-label">{label}</span>
      <span className="prog-v2__bymode-val">{value}</span>
    </div>
  );
}

function MedalCard({ a }: { a: Achievement }) {
  const progressPct =
    a.target > 0 ? Math.min(100, Math.round((a.current_value / a.target) * 100)) : 0;
  // Подбираем lucide-иконку по metric (мягкая эвристика, fallback на 'award').
  const lucideIcon = _medalIcon(a.metric, a.icon);
  return (
    <NoteCard padding="12px 14px" style={{ opacity: a.earned ? 1 : 0.62 }}>
      <div className="prog-v2__medal">
        <span className={`prog-v2__medal-icon ${a.earned ? "is-on" : ""}`}>
          <Icon name={lucideIcon} size={18} />
        </span>
        <div className="prog-v2__medal-body">
          <div className="prog-v2__medal-name">{a.title_ru}</div>
          <div className="prog-v2__medal-desc">{a.description_ru}</div>
          {!a.earned && (
            <>
              <div className="prog-v2__medal-bar" role="progressbar" aria-valuenow={progressPct} aria-valuemin={0} aria-valuemax={100}>
                <div className="prog-v2__medal-bar-fill" style={{ width: `${progressPct}%` }} />
              </div>
              <div className="prog-v2__medal-pct">{a.current_value} / {a.target}</div>
            </>
          )}
          {a.earned && (
            <div style={{ marginTop: 6 }}>
              <Badge tone="speak" icon="check">Получено</Badge>
            </div>
          )}
        </div>
      </div>
    </NoteCard>
  );
}

function _medalIcon(metric: string, fallbackEmoji: string): string {
  switch (metric) {
    case "speaking_sessions":
    case "speaking_minutes":
      return "mic";
    case "listening_sessions":
    case "listening_minutes":
      return "headphones";
    case "grammar_lessons":
    case "grammar_minutes":
      return "book-open";
    case "srs_reviews":
    case "vocab_words":
      return "layers";
    case "streak_days":
      return "flame";
    case "total_minutes":
      return "clock";
    default:
      // Эмодзи из бэкенда (🔥/🎯/🏅) — если не сопоставили, всё равно
      // рендерим award-иконку, иначе попадает «название» icon-name в lucide.
      return fallbackEmoji && fallbackEmoji.length === 1 ? "award" : "award";
  }
}
