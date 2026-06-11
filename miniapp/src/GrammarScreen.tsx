// GrammarScreen.tsx — режим «Грамматика».
//
// Фазы:
//   config   — выбор weak_points / topic + level + category
//   loading  — спиннер пока LLM генерит 10 заданий
//   exercise — цикл из EXERCISES_PER_SESSION заданий с мгновенным feedback
//   summary  — % правильных, разбивка по категориям
//   error    — что-то пошло не так, кнопка повтора

import { useEffect, useRef, useState } from "react";
import WebApp from "@twa-dev/sdk";
import "./App.css";
import { GrammarExercise, type Exercise } from "./GrammarExercise";
import { ProgressScreen } from "./ProgressScreen";
import { WordsScreen } from "./WordsScreen";
import {
  CATEGORY_OPTIONS,
  loadGrammarSettings,
  saveGrammarSettings,
  type GrammarMode,
  type GrammarSettings,
  type MistakeCategory,
} from "./grammarSettings";
import { LEVEL_OPTIONS } from "./tutorSettings";

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  "https://api-english.krichigindocs.ru";

const HEARTBEAT_MS = 20_000;

const CATEGORY_LABELS_RU: Record<string, string> = {
  article: "Артикли",
  tense: "Времена",
  preposition: "Предлоги",
  word_choice: "Выбор слов",
  phrasal: "Фразовые глаголы",
  other: "Другое",
};

interface Props {
  onExit: () => void;
}

type Phase = "config" | "loading" | "exercise" | "summary" | "error";

interface AnswerLog {
  exercise_id: string;
  category: string;
  user_answer: string;
  is_correct: boolean;
}

export function GrammarScreen({ onExit }: Props) {
  const [settings, setSettings] = useState<GrammarSettings>(() => loadGrammarSettings());
  const [phase, setPhase] = useState<Phase>("config");
  const [exercises, setExercises] = useState<Exercise[]>([]);
  const [answers, setAnswers] = useState<AnswerLog[]>([]);
  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const [sessionId, setSessionId] = useState<string>("");
  const sessionStartRef = useRef<number>(0);
  const [error, setError] = useState<string>("");
  const [userName, setUserName] = useState<string>("there");
  const [progressOpen, setProgressOpen] = useState<boolean>(false);
  const [wordsOpen, setWordsOpen] = useState<boolean>(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    try { WebApp.ready(); } catch { /* ignore */ }
    try { WebApp.expand(); } catch { /* ignore */ }
    // Без этого свайп вниз при скролле конфига/заданий сворачивает Mini App.
    try { WebApp.disableVerticalSwipes?.(); } catch { /* ignore */ }
    const user = WebApp.initDataUnsafe?.user;
    if (user?.first_name) setUserName(user.first_name);
  }, []);

  useEffect(() => {
    saveGrammarSettings(settings);
  }, [settings]);

  // ── Heartbeat пока на экране упражнений ──────────────────────────────
  useEffect(() => {
    if (phase !== "exercise" || !sessionId) return;
    const tick = () => {
      void fetch(`${API_BASE}/api/grammar/heartbeat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          init_data: WebApp.initData || "",
          session_id: sessionId,
        }),
      }).catch(() => {
        /* heartbeat best-effort — не падаем при сетевой ошибке */
      });
    };
    const id = window.setInterval(tick, HEARTBEAT_MS);
    return () => window.clearInterval(id);
  }, [phase, sessionId]);

  const startGeneration = async (mode: GrammarMode) => {
    setError("");
    setExercises([]);
    setAnswers([]);
    setCurrentIndex(0);
    setSessionId("");
    setPhase("loading");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch(`${API_BASE}/api/grammar/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          init_data: WebApp.initData || "",
          mode,
          level: settings.level,
          category: mode === "topic" ? settings.category : undefined,
        }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 200)}` : ""}`);
      }
      const data: { session_id: string; exercises: Exercise[] } = await res.json();
      if (!Array.isArray(data.exercises) || data.exercises.length === 0) {
        throw new Error("Сервер не вернул упражнений");
      }
      setSessionId(data.session_id);
      setExercises(data.exercises);
      sessionStartRef.current = Date.now();
      setPhase("exercise");
    } catch (e: unknown) {
      if (controller.signal.aborted) {
        setPhase("config");
        return;
      }
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  };

  const cancelGeneration = () => abortRef.current?.abort();

  const handleAnswer = (userAnswer: string, isCorrect: boolean) => {
    const ex = exercises[currentIndex];
    if (!ex) return;
    setAnswers((prev) => [
      ...prev,
      {
        exercise_id: ex.id,
        category: ex.category,
        user_answer: userAnswer,
        is_correct: isCorrect,
      },
    ]);
  };

  const handleNext = () => {
    if (currentIndex + 1 < exercises.length) {
      setCurrentIndex(currentIndex + 1);
    } else {
      void finishSession();
    }
  };

  const finishSession = async () => {
    const durationSec = Math.max(
      0,
      Math.round((Date.now() - sessionStartRef.current) / 1000),
    );
    setPhase("summary");
    // POST /finish best-effort: если не дойдёт — фронт UI остаётся
    try {
      await fetch(`${API_BASE}/api/grammar/finish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          init_data: WebApp.initData || "",
          session_id: sessionId,
          results: answers.map((a) => ({
            exercise_id: a.exercise_id,
            user_answer: a.user_answer,
            is_correct: a.is_correct,
            category: a.category,
          })),
          duration_sec: durationSec,
        }),
      });
    } catch {
      /* лог не критичен — fail тихо */
    }
  };

  // ── Сводка ───────────────────────────────────────────────────────────
  const total = answers.length;
  const correct = answers.filter((a) => a.is_correct).length;
  const percent = total > 0 ? Math.round((correct / total) * 100) : 0;
  const byCategory: Record<string, { correct: number; total: number }> = {};
  for (const a of answers) {
    if (!byCategory[a.category]) byCategory[a.category] = { correct: 0, total: 0 };
    byCategory[a.category].total += 1;
    if (a.is_correct) byCategory[a.category].correct += 1;
  }

  const restart = () => {
    setExercises([]);
    setAnswers([]);
    setCurrentIndex(0);
    setSessionId("");
    setPhase("config");
  };

  return (
    <div className="tutor-shell grm-screen">
      <div className="bg-orb bg-orb--one" aria-hidden />
      <div className="bg-orb bg-orb--two" aria-hidden />

      <header className="tutor-header">
        <div className="tutor-brand">
          <button
            type="button"
            className="icon-button tutor-back"
            onClick={onExit}
            aria-label="Назад к выбору режима"
            title="Назад"
          >
            <span style={{ fontSize: 18, lineHeight: 1 }} aria-hidden>←</span>
          </button>
          <span className="tutor-brand__dot" aria-hidden />
          <span className="tutor-brand__name">Грамматика</span>
        </div>
        <div className="tutor-header__right">
          <p className="tutor-hello">Hi, {userName}</p>
          <button
            type="button"
            className="icon-button"
            onClick={() => setProgressOpen(true)}
            aria-label="Мой прогресс"
            title="Мой прогресс"
          >
            <span style={{ fontSize: 18, lineHeight: 1 }} aria-hidden>📊</span>
          </button>
          <button
            type="button"
            className="icon-button"
            onClick={() => setWordsOpen(true)}
            aria-label="Мои слова"
            title="Мои слова"
          >
            <span style={{ fontSize: 18, lineHeight: 1 }} aria-hidden>📖</span>
          </button>
        </div>
      </header>

      <main className="lst-main">
        {phase === "config" && (
          <>
            <section className="lst-section">
              <h3 className="lst-section__title">Уровень</h3>
              <div className="lst-chips">
                {LEVEL_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    className="lst-chip lst-chip--level"
                    data-active={settings.level === opt.value ? "true" : "false"}
                    onClick={() => setSettings({ ...settings, level: opt.value })}
                  >
                    <span className="lst-chip__main">{opt.label}</span>
                    <span className="lst-chip__sub">{opt.hint}</span>
                  </button>
                ))}
              </div>
            </section>

            <section className="lst-section">
              <h3 className="lst-section__title">Тема (для «Новая тема»)</h3>
              <div className="lst-categories">
                {CATEGORY_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    className="lst-category"
                    data-active={settings.category === opt.value ? "true" : "false"}
                    onClick={() =>
                      setSettings({ ...settings, category: opt.value as MistakeCategory })
                    }
                  >
                    <span className="lst-category__emoji" aria-hidden>{opt.emoji}</span>
                    <span className="lst-category__label">{opt.label}</span>
                  </button>
                ))}
              </div>
            </section>

            <div className="grm-mode-row">
              <button
                type="button"
                className="grm-mode-card grm-mode-card--weak"
                onClick={() => startGeneration("weak_points")}
              >
                <span className="grm-mode-card__emoji" aria-hidden>🎯</span>
                <span className="grm-mode-card__title">Разобрать мои ошибки</span>
                <span className="grm-mode-card__hint">
                  AI возьмёт твои реальные ошибки из разговоров и сделает упражнения
                  точно по слабым местам.
                </span>
              </button>

              <button
                type="button"
                className="grm-mode-card grm-mode-card--topic"
                onClick={() => startGeneration("topic")}
              >
                <span className="grm-mode-card__emoji" aria-hidden>📚</span>
                <span className="grm-mode-card__title">Новая тема</span>
                <span className="grm-mode-card__hint">
                  10 упражнений по выбранной категории — для отработки знаний с нуля.
                </span>
              </button>
            </div>
          </>
        )}

        {phase === "loading" && (
          <div className="lst-loading">
            <div className="lst-spinner" aria-hidden />
            <p className="lst-loading__title">Готовлю задания…</p>
            <p className="lst-loading__hint">
              Это займёт ~10 секунд.
            </p>
            <button type="button" className="lst-secondary-btn" onClick={cancelGeneration}>
              Отмена
            </button>
          </div>
        )}

        {phase === "exercise" && exercises[currentIndex] && (
          <GrammarExercise
            key={exercises[currentIndex].id + "@" + currentIndex}
            exercise={exercises[currentIndex]}
            index={currentIndex}
            total={exercises.length}
            onAnswer={handleAnswer}
            onNext={handleNext}
            isLast={currentIndex + 1 === exercises.length}
          />
        )}

        {phase === "summary" && (
          <div className="grm-summary">
            <div className="grm-summary__score">
              <div className="grm-summary__pct">{percent}%</div>
              <div className="grm-summary__count">
                {correct} из {total} правильно
              </div>
            </div>

            <div className="grm-summary__cats">
              {Object.entries(byCategory).map(([cat, st]) => {
                const pct = st.total ? Math.round((st.correct / st.total) * 100) : 0;
                const tone =
                  pct >= 80 ? "good" : pct >= 50 ? "mid" : "bad";
                return (
                  <div key={cat} className="grm-cat-row" data-tone={tone}>
                    <span className="grm-cat-row__label">
                      {CATEGORY_LABELS_RU[cat] ?? cat}
                    </span>
                    <span className="grm-cat-row__value">
                      {st.correct}/{st.total} · {pct}%
                    </span>
                  </div>
                );
              })}
            </div>

            <div className="grm-summary__actions">
              <button type="button" className="lst-secondary-btn" onClick={onExit}>
                В меню
              </button>
              <button type="button" className="grm-primary-btn" onClick={restart}>
                Ещё раунд
              </button>
            </div>
          </div>
        )}

        {phase === "error" && (
          <div className="lst-error">
            <p className="lst-error__title">Не получилось сгенерировать упражнения</p>
            <p className="lst-error__hint">{error || "Попробуй ещё раз через минуту."}</p>
            <div className="lst-error__actions">
              <button type="button" className="lst-secondary-btn" onClick={restart}>
                Назад
              </button>
              <button
                type="button"
                className="grm-primary-btn"
                onClick={() => startGeneration(settings.defaultMode)}
              >
                Повторить
              </button>
            </div>
          </div>
        )}
      </main>

      {wordsOpen && (
        <WordsScreen apiBase={API_BASE} onClose={() => setWordsOpen(false)} />
      )}
      {progressOpen && (
        <ProgressScreen
          apiBase={API_BASE}
          initData={WebApp.initData || ""}
          onClose={() => setProgressOpen(false)}
        />
      )}
    </div>
  );
}
