// GrammarScreen.tsx — режим «Грамматика». Два трека:
//
//   learn — «Учить правила»: каталог тем по уровням (GET /topics) →
//           урок: теория → 8 упражнений → итог с порогом 70% и
//           разблокировкой следующей темы (Duolingo-style).
//   test  — «Проверить себя»: существующий генератор 10 заданий
//           (weak_points по user_mistakes / topic по категории).
//
// Фазы:
//   home     — выбор трека (стартовая)
//   topics   — дерево тем Learn-трека (табы уровней, ✅/🔓/🔒)
//   theory   — карточка правила + примеры, кнопка «К практике»
//   config   — настройки test-трека (уровень + категория + 2 кнопки)
//   loading  — спиннер (генерация LLM)
//   exercise — цикл заданий с мгновенным feedback (общий для треков)
//   summary  — итог (для learn — с passed-бейджем и «Следующая тема»)
//   error    — ошибка + retry

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
import { LEVEL_OPTIONS, type Level } from "./tutorSettings";

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

type Phase =
  | "home"
  | "topics"
  | "theory"
  | "config"
  | "loading"
  | "exercise"
  | "summary"
  | "error";

type Track = "learn" | "test";

interface AnswerLog {
  exercise_id: string;
  category: string;
  user_answer: string;
  is_correct: boolean;
}

interface TopicInfo {
  key: string;
  title_ru: string;
  category: string;
  status: "done" | "available" | "locked";
  best_score: number;
}

interface LessonData {
  topic_key: string;
  title_ru: string;
  theory: string;
}

interface LessonResult {
  passed: boolean;
  score: number;
  best_score: number;
  next_topic_key: string | null;
}

export function GrammarScreen({ onExit }: Props) {
  const [settings, setSettings] = useState<GrammarSettings>(() => loadGrammarSettings());
  const [phase, setPhase] = useState<Phase>("home");
  const [track, setTrack] = useState<Track>("test");
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

  // Learn-трек
  const [topicLevels, setTopicLevels] = useState<Record<string, TopicInfo[]> | null>(null);
  const [topicsLevel, setTopicsLevel] = useState<Level>(settings.level);
  const [topicsError, setTopicsError] = useState<string>("");
  const [lesson, setLesson] = useState<LessonData | null>(null);
  const [lessonResult, setLessonResult] = useState<LessonResult | null>(null);

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

  // ── Learn: список тем ─────────────────────────────────────────────────
  const openTopics = async () => {
    setTrack("learn");
    setPhase("topics");
    setTopicsError("");
    try {
      const res = await fetch(
        `${API_BASE}/api/grammar/topics?init_data=${encodeURIComponent(WebApp.initData || "")}`,
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: { levels: Record<string, TopicInfo[]> } = await res.json();
      setTopicLevels(data.levels || {});
    } catch (e: unknown) {
      setTopicsError(e instanceof Error ? e.message : String(e));
    }
  };

  // ── Learn: открыть урок ───────────────────────────────────────────────
  const openLesson = async (topicKey: string) => {
    setError("");
    setExercises([]);
    setAnswers([]);
    setCurrentIndex(0);
    setSessionId("");
    setLessonResult(null);
    setPhase("loading");
    setTrack("learn");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch(`${API_BASE}/api/grammar/lesson`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          init_data: WebApp.initData || "",
          topic_key: topicKey,
        }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 200)}` : ""}`);
      }
      const data: {
        topic_key: string;
        title_ru: string;
        theory: string;
        exercises: Exercise[];
        session_id: string;
      } = await res.json();
      if (!Array.isArray(data.exercises) || data.exercises.length === 0) {
        throw new Error("Сервер не вернул упражнений");
      }
      setLesson({
        topic_key: data.topic_key,
        title_ru: data.title_ru,
        theory: data.theory,
      });
      setExercises(data.exercises);
      setSessionId(data.session_id);
      setSettings((s) => ({ ...s, lastTopicKey: data.topic_key }));
      setPhase("theory");
    } catch (e: unknown) {
      if (controller.signal.aborted) {
        setPhase("topics");
        return;
      }
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  };

  const startLessonPractice = () => {
    sessionStartRef.current = Date.now();
    setPhase("exercise");
  };

  // ── Test: генерация (существующий путь) ───────────────────────────────
  const startGeneration = async (mode: GrammarMode) => {
    setError("");
    setExercises([]);
    setAnswers([]);
    setCurrentIndex(0);
    setSessionId("");
    setLesson(null);
    setLessonResult(null);
    setPhase("loading");
    setTrack("test");
    setSettings((s) => ({ ...s, defaultMode: mode }));

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
    } else if (track === "learn") {
      void finishLesson();
    } else {
      void finishSession();
    }
  };

  // ── Test: финиш ───────────────────────────────────────────────────────
  const finishSession = async () => {
    const durationSec = Math.max(
      0,
      Math.round((Date.now() - sessionStartRef.current) / 1000),
    );
    setPhase("summary");
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
      /* fail тихо */
    }
  };

  // ── Learn: финиш урока ────────────────────────────────────────────────
  const finishLesson = async () => {
    const durationSec = Math.max(
      0,
      Math.round((Date.now() - sessionStartRef.current) / 1000),
    );
    const correctCount = answers.filter((a) => a.is_correct).length;
    setPhase("summary");
    try {
      const res = await fetch(`${API_BASE}/api/grammar/lesson/finish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          init_data: WebApp.initData || "",
          topic_key: lesson?.topic_key || "",
          session_id: sessionId,
          correct: correctCount,
          total: exercises.length,
          duration_sec: durationSec,
        }),
      });
      if (res.ok) {
        const data: LessonResult = await res.json();
        setLessonResult(data);
      }
    } catch {
      /* fail тихо — summary покажет локальный счёт */
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

  const resetRound = () => {
    setExercises([]);
    setAnswers([]);
    setCurrentIndex(0);
    setSessionId("");
    setLesson(null);
    setLessonResult(null);
  };

  // Назад в шапке — фазозависимый.
  const handleBack = () => {
    if (phase === "home") {
      onExit();
    } else if (phase === "topics" || phase === "config") {
      resetRound();
      setPhase("home");
    } else if (phase === "theory") {
      resetRound();
      void openTopics();
    } else if (phase === "summary" || phase === "error") {
      resetRound();
      if (track === "learn") void openTopics();
      else setPhase("config");
    } else {
      onExit();
    }
  };

  const levelTabs: Level[] = ["A2", "B1", "B2", "C1"];
  const currentTopics = topicLevels?.[topicsLevel] ?? [];

  return (
    <div className="tutor-shell grm-screen">
      <div className="bg-orb bg-orb--one" aria-hidden />
      <div className="bg-orb bg-orb--two" aria-hidden />

      <header className="tutor-header">
        <div className="tutor-brand">
          <button
            type="button"
            className="icon-button tutor-back"
            onClick={handleBack}
            aria-label="Назад"
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
        {/* ── HOME: выбор трека ── */}
        {phase === "home" && (
          <div className="grm-mode-row" style={{ marginTop: 4 }}>
            <button
              type="button"
              className="grm-mode-card grm-mode-card--weak"
              onClick={() => void openTopics()}
            >
              <span className="grm-mode-card__emoji" aria-hidden>📖</span>
              <span className="grm-mode-card__title">Учить правила</span>
              <span className="grm-mode-card__hint">
                Программа тем от A2 до C1: правило с примерами → практика →
                следующая тема открывается после прохождения.
              </span>
            </button>

            <button
              type="button"
              className="grm-mode-card"
              onClick={() => { setTrack("test"); setPhase("config"); }}
            >
              <span className="grm-mode-card__emoji" aria-hidden>🎯</span>
              <span className="grm-mode-card__title">Проверить себя</span>
              <span className="grm-mode-card__hint">
                10 упражнений по твоим реальным ошибкам из разговоров или по
                выбранной теме.
              </span>
            </button>
          </div>
        )}

        {/* ── TOPICS: дерево тем Learn ── */}
        {phase === "topics" && (
          <>
            <section className="lst-section">
              <h3 className="lst-section__title">Уровень</h3>
              <div className="lst-chips">
                {levelTabs.map((lv) => (
                  <button
                    key={lv}
                    type="button"
                    className="lst-chip"
                    data-active={topicsLevel === lv ? "true" : "false"}
                    onClick={() => setTopicsLevel(lv)}
                  >
                    {lv}
                  </button>
                ))}
              </div>
            </section>

            {topicsError && (
              <div className="lst-error" style={{ padding: "16px 8px" }}>
                <p className="lst-error__hint">{topicsError}</p>
                <button type="button" className="lst-secondary-btn" onClick={() => void openTopics()}>
                  Повторить
                </button>
              </div>
            )}

            {!topicsError && topicLevels === null && (
              <div className="lst-loading" style={{ padding: "30px 16px" }}>
                <div className="lst-spinner" aria-hidden />
              </div>
            )}

            {!topicsError && topicLevels !== null && (
              <div className="grm-topics">
                {currentTopics.length === 0 && (
                  <p className="lst-loading__hint">Темы этого уровня скоро появятся.</p>
                )}
                {currentTopics.map((t, i) => (
                  <button
                    key={t.key}
                    type="button"
                    className="grm-topic-row"
                    data-status={t.status}
                    disabled={t.status === "locked"}
                    onClick={() => {
                      if (t.status !== "locked") void openLesson(t.key);
                    }}
                  >
                    <span className="grm-topic-row__icon" aria-hidden>
                      {t.status === "done" ? "✅" : t.status === "available" ? "🔓" : "🔒"}
                    </span>
                    <span className="grm-topic-row__body">
                      <span className="grm-topic-row__title">
                        {i + 1}. {t.title_ru}
                      </span>
                      {t.status === "done" && (
                        <span className="grm-topic-row__score">
                          лучший результат {t.best_score}%
                        </span>
                      )}
                      {t.status === "locked" && (
                        <span className="grm-topic-row__score">
                          пройди предыдущую тему
                        </span>
                      )}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </>
        )}

        {/* ── THEORY: карточка правила ── */}
        {phase === "theory" && lesson && (
          <div className="grm-theory">
            <h2 className="grm-theory__title">{lesson.title_ru}</h2>
            <div className="grm-theory__body">
              {lesson.theory.split("\n").map((line, i) =>
                line.trim() === "" ? (
                  <div key={i} style={{ height: 10 }} />
                ) : (
                  <p key={i} className="grm-theory__para">{line}</p>
                ),
              )}
            </div>
            <button type="button" className="grm-primary-btn" onClick={startLessonPractice}>
              К практике →
            </button>
          </div>
        )}

        {/* ── CONFIG: test-трек (как раньше) ── */}
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

        {/* ── LOADING ── */}
        {phase === "loading" && (
          <div className="lst-loading">
            <div className="lst-spinner" aria-hidden />
            <p className="lst-loading__title">
              {track === "learn" ? "Готовлю урок…" : "Готовлю задания…"}
            </p>
            <p className="lst-loading__hint">Это займёт ~10 секунд.</p>
            <button type="button" className="lst-secondary-btn" onClick={cancelGeneration}>
              Отмена
            </button>
          </div>
        )}

        {/* ── EXERCISE (общий) ── */}
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

        {/* ── SUMMARY ── */}
        {phase === "summary" && (
          <div className="grm-summary">
            {track === "learn" && (
              <div
                className="grm-lesson-badge"
                data-passed={(lessonResult?.passed ?? percent >= 70) ? "true" : "false"}
              >
                {(lessonResult?.passed ?? percent >= 70)
                  ? "🎉 Тема пройдена!"
                  : "Нужно ≥70% — попробуй ещё раз"}
              </div>
            )}

            <div className="grm-summary__score">
              <div className="grm-summary__pct">{percent}%</div>
              <div className="grm-summary__count">
                {correct} из {total} правильно
              </div>
            </div>

            {track === "test" && (
              <div className="grm-summary__cats">
                {Object.entries(byCategory).map(([cat, st]) => {
                  const pct = st.total ? Math.round((st.correct / st.total) * 100) : 0;
                  const tone = pct >= 80 ? "good" : pct >= 50 ? "mid" : "bad";
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
            )}

            <div className="grm-summary__actions">
              {track === "learn" ? (
                <>
                  <button
                    type="button"
                    className="lst-secondary-btn"
                    onClick={() => { resetRound(); void openTopics(); }}
                  >
                    К темам
                  </button>
                  {lessonResult?.passed && lessonResult.next_topic_key ? (
                    <button
                      type="button"
                      className="grm-primary-btn"
                      onClick={() => {
                        const next = lessonResult.next_topic_key!;
                        resetRound();
                        void openLesson(next);
                      }}
                    >
                      Следующая тема →
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="grm-primary-btn"
                      onClick={() => {
                        const cur = lesson?.topic_key;
                        resetRound();
                        if (cur) void openLesson(cur);
                        else void openTopics();
                      }}
                    >
                      Ещё раз
                    </button>
                  )}
                </>
              ) : (
                <>
                  <button type="button" className="lst-secondary-btn" onClick={onExit}>
                    В меню
                  </button>
                  <button
                    type="button"
                    className="grm-primary-btn"
                    onClick={() => { resetRound(); setPhase("config"); }}
                  >
                    Ещё раунд
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {/* ── ERROR ── */}
        {phase === "error" && (
          <div className="lst-error">
            <p className="lst-error__title">Не получилось сгенерировать</p>
            <p className="lst-error__hint">{error || "Попробуй ещё раз через минуту."}</p>
            <div className="lst-error__actions">
              <button
                type="button"
                className="lst-secondary-btn"
                onClick={() => {
                  resetRound();
                  if (track === "learn") void openTopics();
                  else setPhase("config");
                }}
              >
                Назад
              </button>
              <button
                type="button"
                className="grm-primary-btn"
                onClick={() => {
                  if (track === "learn" && settings.lastTopicKey) {
                    void openLesson(settings.lastTopicKey);
                  } else {
                    void startGeneration(settings.defaultMode);
                  }
                }}
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
