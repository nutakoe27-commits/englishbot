/**
 * SessionSummary.tsx — экран «итог урока», показывается сразу после
 * завершения голосовой/текстовой сессии.
 *
 * Дизайн осознанно лёгкий: 3 короткие карточки. Цель — за 5 секунд дать
 * юзеру понять что он сделал и стоит ли возвращаться завтра.
 *
 * Данные тянем с /api/learner/recent-context — за неделю, не только за
 * последнюю сессию (так стабильнее: LLM-recap иногда не даёт результата
 * на коротких диалогах, и экран всё равно покажет что-то полезное).
 */

import { useEffect, useState } from "react";
import WebApp from "@twa-dev/sdk";

interface RecentContext {
  streak: { current: number; best: number; last_practice_date: string | null };
  vocab: { word: string; times_used: number; last_seen_at: string | null }[];
  mistakes: { category: string; bad: string; good: string; occurred_at: string | null }[];
  today_used_seconds: number;
}

interface Props {
  apiBase: string;        // например "https://api-english.krichigindocs.ru"
  sessionSeconds: number; // длина только что закончившейся сессии
  onClose: () => void;
}

const CATEGORY_LABELS: Record<string, string> = {
  article: "артикли (a/an/the)",
  tense: "времена",
  preposition: "предлоги",
  word_choice: "выбор слов",
  phrasal: "фразовые глаголы",
  other: "разное",
};

function pluralizeMinutes(n: number): string {
  const a = Math.abs(n) % 100;
  if (a >= 11 && a <= 14) return "минут";
  const last = a % 10;
  if (last === 1) return "минуту";
  if (last >= 2 && last <= 4) return "минуты";
  return "минут";
}

function pluralizeDays(n: number): string {
  const a = Math.abs(n) % 100;
  if (a >= 11 && a <= 14) return "дней";
  const last = a % 10;
  if (last === 1) return "день";
  if (last >= 2 && last <= 4) return "дня";
  return "дней";
}

export function SessionSummary({ apiBase, sessionSeconds, onClose }: Props) {
  const [data, setData] = useState<RecentContext | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const initData = WebApp.initData || "";
    fetch(
      `${apiBase}/api/learner/recent-context?init_data=${encodeURIComponent(initData)}`,
    )
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: RecentContext) => {
        if (!cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [apiBase]);

  // Группируем ошибки по категории — показываем только топ-категорий с примером.
  const mistakeGroups: { category: string; example?: { bad: string; good: string } }[] = [];
  if (data) {
    const seen = new Set<string>();
    for (const m of data.mistakes) {
      if (seen.has(m.category)) continue;
      seen.add(m.category);
      mistakeGroups.push({
        category: m.category,
        example: { bad: m.bad, good: m.good },
      });
    }
  }

  const sessionMin = Math.max(1, Math.round(sessionSeconds / 60));
  const todayMin =
    data && data.today_used_seconds > 0
      ? Math.max(1, Math.round(data.today_used_seconds / 60))
      : sessionMin;
  const streak = data?.streak.current ?? 0;
  const best = data?.streak.best ?? 0;

  return (
    <div className="summary-overlay">
      <div className="summary-card">
        <h2 className="summary-title">Итог занятия</h2>

        {/* Блок 1: время + стрик */}
        <section className="summary-block">
          <div className="summary-stat">
            <div className="summary-stat__value">
              {sessionMin} {pluralizeMinutes(sessionMin)}
            </div>
            <div className="summary-stat__label">в этой сессии</div>
          </div>
          {streak > 0 && (
            <div className="summary-stat">
              <div className="summary-stat__value">
                🔥 {streak} {pluralizeDays(streak)}
              </div>
              <div className="summary-stat__label">
                {best > streak ? `стрик · рекорд ${best}` : "это твой рекорд!"}
              </div>
            </div>
          )}
          {!streak && (
            <div className="summary-stat__label">
              Сегодня: {todayMin} {pluralizeMinutes(todayMin)} практики
            </div>
          )}
        </section>

        {/* Блок 2: слова за неделю */}
        {data && data.vocab.length > 0 && (
          <section className="summary-block">
            <h3 className="summary-section-title">
              💡 Слова, которые тренировали
            </h3>
            <div className="summary-chips">
              {data.vocab.slice(0, 8).map((v) => (
                <span key={v.word} className="summary-chip">
                  {v.word}
                </span>
              ))}
            </div>
            <div className="summary-hint">
              Попробуй вслух повторить каждое — закрепляется в 3 раза лучше.
            </div>
          </section>
        )}

        {/* Блок 3: на что обратить внимание */}
        {mistakeGroups.length > 0 && (
          <section className="summary-block">
            <h3 className="summary-section-title">📌 На что обратить внимание</h3>
            <ul className="summary-mistakes">
              {mistakeGroups.slice(0, 3).map((g) => (
                <li key={g.category}>
                  <strong>{CATEGORY_LABELS[g.category] || g.category}</strong>
                  {g.example && (
                    <div className="summary-example">
                      <span className="summary-example__bad">«{g.example.bad}»</span>
                      <span className="summary-example__arrow">→</span>
                      <span className="summary-example__good">«{g.example.good}»</span>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* Empty state — пока нечего показать (короткая первая сессия) */}
        {data && data.vocab.length === 0 && mistakeGroups.length === 0 && (
          <p className="summary-empty">
            Поговори ещё пару минут в следующий раз — я начну запоминать слова
            и подмечать ошибки, чтобы вернуться к ним позже.
          </p>
        )}

        {error && (
          <p className="summary-empty">
            Не получилось загрузить детали урока — но сессия зачтена.
          </p>
        )}

        <button className="summary-close" onClick={onClose}>
          Готово
        </button>
      </div>
    </div>
  );
}
