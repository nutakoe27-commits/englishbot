// GrammarScreen.tsx — режим «Грамматика». Два трека:
//
//   learn — «Учить правила»: каталог тем A1–C1 (GET /topics) → урок:
//           рукописная теория → 8 LLM-упражнений (каждому юзеру свежие) →
//           итог с порогом 70% и разблокировкой следующей темы.
//   test  — «Проверить себя»: БЕЗ настроек. Сразу генерим 10 заданий по
//           реальным ошибкам юзера (user_mistakes). Если ошибок нет —
//           заглушка «Сначала поговори с AI Tutor».
//
// Фазы:
//   home        — выбор трека (стартовая)
//   topics      — дерево тем Learn-трека (табы уровней, ✅/🔓/🔒)
//   theory      — карточка правила, кнопка «К практике»
//   no_mistakes — заглушка test-трека: нет накопленных ошибок
//   loading     — спиннер (генерация LLM)
//   exercise    — цикл заданий с мгновенным feedback (общий для треков)
//   summary     — итог (для learn — passed-бейдж и «Следующая тема»)
//   error       — ошибка + retry

import { useEffect, useRef, useState } from "react";
import WebApp from "@twa-dev/sdk";
import "./App.css";
import { GrammarExercise, type Exercise } from "./GrammarExercise";
import { ProgressScreen } from "./ProgressScreen";
import { WordsScreen } from "./WordsScreen";
import { LockScreen } from "./LockScreen";
import { SubscribeScreen } from "./SubscribeScreen";
import {
  loadGrammarSettings,
  saveGrammarSettings,
  type GrammarSettings,
} from "./grammarSettings";
import { loadSettings as loadTutorSettings } from "./tutorSettings";
import { IconButton } from "./ds-react/IconButton";
import { SerifH } from "./ds-react/typography";
import { Icon } from "./ds-react/Icon";
import { useLucide } from "./lucide";

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  "https://api-english.krichigindocs.ru";

const BOT_USERNAME =
  (import.meta.env.VITE_BOT_USERNAME as string | undefined) || "kmo_ai_english_bot";

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
  | "no_mistakes"
  | "loading"
  | "exercise"
  | "summary"
  | "error";

type Track = "learn" | "test";

// Уровни Learn-каталога (включая A1, которого нет в tutorSettings.Level).
type TopicLevel = "A1" | "A2" | "B1" | "B2" | "C1";

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

// Статус фоновой генерации упражнений урока: теория открывается мгновенно,
// упражнения LLM готовит параллельно, пока юзер читает.
type ExercisesState = "idle" | "pending" | "ready" | "error";

// ── Рендер теории: **bold** + автотаблица примеров «EN — RU» ────────────
// Строка считается примером, если EN-часть — латиница с финальной
// пунктуацией (как «I am from Russia. — Я из России.»). Подряд идущие
// примеры группируются в один блок-таблицу.

function renderBold(text: string): React.ReactNode[] {
  return text
    .split(/\*\*(.+?)\*\*/g)
    .map((part, i) => (i % 2 === 1 ? <strong key={i}>{part}</strong> : part));
}

function renderTheoryBlocks(text: string): React.ReactNode[] {
  const lines = text.split("\n");
  const blocks: React.ReactNode[] = [];
  let examples: { en: string; ru: string }[] = [];

  const flush = (key: string) => {
    if (examples.length === 0) return;
    const rows = examples;
    examples = [];
    blocks.push(
      <div key={key} className="grm-examples">
        {rows.map((ex, i) => (
          <div key={i} className="grm-examples__row">
            <span className="grm-examples__en">{renderBold(ex.en)}</span>
            <span className="grm-examples__ru">{ex.ru}</span>
          </div>
        ))}
      </div>,
    );
  };

  lines.forEach((line, i) => {
    const t = line.trim();
    if (!t) {
      flush(`ex-${i}`);
      return;
    }
    const m = t.match(/^(.{2,}?) — (.+)$/);
    const en = m?.[1] ?? "";
    const isExample =
      m !== null &&
      /^[A-Za-z"'❌✅(]/.test(en) &&
      /[.!?…»")]$/.test(en) &&
      !/[а-яА-ЯёЁ]/.test(en);
    if (isExample && m) {
      examples.push({ en: m[1], ru: m[2] });
      return;
    }
    flush(`ex-${i}`);
    blocks.push(
      <p key={`p-${i}`} className="grm-theory__para">
        {renderBold(t)}
      </p>,
    );
  });
  flush("ex-end");
  return blocks;
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
  const [paywall, setPaywall] = useState<boolean>(false);
  const [subscribeOpen, setSubscribeOpen] = useState<boolean>(false);
  const inTelegram = !!WebApp.initData;
  const abortRef = useRef<AbortController | null>(null);
  useLucide(`${progressOpen}-${wordsOpen}-${paywall}`);

  // Learn-трек
  const [topicLevels, setTopicLevels] = useState<Record<string, TopicInfo[]> | null>(null);
  const [topicsLevel, setTopicsLevel] = useState<TopicLevel>("A1");
  const [topicsError, setTopicsError] = useState<string>("");
  const [lesson, setLesson] = useState<LessonData | null>(null);
  const [lessonResult, setLessonResult] = useState<LessonResult | null>(null);
  const [exState, setExState] = useState<ExercisesState>("idle");

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
        /* heartbeat best-effort */
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

  // ── Learn: фоновая генерация упражнений (пока юзер читает теорию) ──────
  const fetchLessonExercises = async (topicKey: string) => {
    setExState("pending");
    try {
      const res = await fetch(`${API_BASE}/api/grammar/lesson/exercises`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          init_data: WebApp.initData || "",
          topic_key: topicKey,
        }),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 200)}` : ""}`);
      }
      const data: { exercises: Exercise[]; session_id: string } = await res.json();
      if (!Array.isArray(data.exercises) || data.exercises.length === 0) {
        throw new Error("Сервер не вернул упражнений");
      }
      setExercises(data.exercises);
      setSessionId(data.session_id);
      setExState("ready");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setExState("error");
    }
  };

  // ── Learn: открыть урок — теория приходит мгновенно (рукописная) ──────
  const openLesson = async (topicKey: string) => {
    setError("");
    setExercises([]);
    setAnswers([]);
    setCurrentIndex(0);
    setSessionId("");
    setLessonResult(null);
    setExState("idle");
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
      if (res.status === 402) {
        setPaywall(true);
        setPhase("topics");
        return;
      }
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 200)}` : ""}`);
      }
      const data: { topic_key: string; title_ru: string; theory: string } =
        await res.json();
      setLesson({
        topic_key: data.topic_key,
        title_ru: data.title_ru,
        theory: data.theory,
      });
      setSettings((s) => ({ ...s, lastTopicKey: data.topic_key }));
      setPhase("theory");
      // Упражнения готовятся в фоне, пока юзер читает теорию.
      void fetchLessonExercises(topicKey);
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
    if (exState === "ready") {
      sessionStartRef.current = Date.now();
      setPhase("exercise");
    } else if (exState === "error") {
      setPhase("error");
    } else {
      // Ещё генерятся — показываем короткое ожидание; useEffect ниже
      // переключит на exercise, как только будут готовы.
      setPhase("loading");
    }
  };

  // Автопереход из ожидания, когда фоновая генерация завершилась.
  useEffect(() => {
    if (phase !== "loading" || track !== "learn") return;
    if (exState === "ready") {
      sessionStartRef.current = Date.now();
      setPhase("exercise");
    } else if (exState === "error") {
      setPhase("error");
    }
  }, [phase, track, exState]);

  // ── Test: «Проверить себя» — всегда по реальным ошибкам, без настроек ──
  const startWeakPoints = async () => {
    setError("");
    setExercises([]);
    setAnswers([]);
    setCurrentIndex(0);
    setSessionId("");
    setLesson(null);
    setLessonResult(null);
    setPhase("loading");
    setTrack("test");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch(`${API_BASE}/api/grammar/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          init_data: WebApp.initData || "",
          mode: "weak_points",
          // Уровень берём из настроек speaking-тьютора — отдельных
          // настроек у теста больше нет.
          level: loadTutorSettings().level,
        }),
        signal: controller.signal,
      });
      if (res.status === 409) {
        // Нет накопленных ошибок — показываем заглушку.
        setPhase("no_mistakes");
        return;
      }
      if (res.status === 402) {
        setPaywall(true);
        setPhase("home");
        return;
      }
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
        setPhase("home");
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
    setExState("idle");
  };

  // Назад в шапке — фазозависимый.
  const handleBack = () => {
    if (phase === "home") {
      onExit();
    } else if (phase === "topics" || phase === "no_mistakes") {
      resetRound();
      setPhase("home");
    } else if (phase === "theory") {
      resetRound();
      void openTopics();
    } else if (phase === "summary" || phase === "error") {
      resetRound();
      if (track === "learn") void openTopics();
      else setPhase("home");
    } else {
      onExit();
    }
  };

  const levelTabs: TopicLevel[] = ["A1", "A2", "B1", "B2", "C1"];
  const currentTopics = topicLevels?.[topicsLevel] ?? [];

  return (
    <div className="tutor-shell grm-screen">
      <div className="bg-orb bg-orb--one" aria-hidden />
      <div className="bg-orb bg-orb--two" aria-hidden />

      <header className="mode-v2-top">
        <button type="button" className="mode-v2-back" onClick={handleBack} aria-label="Назад">
          <Icon name="arrow-left" size={16} />
          <span>Назад</span>
        </button>
        <div className="mode-v2-title">
          <span className="mode-v2-title__icon mode-v2-title__icon--warn">
            <Icon name="book-open" size={16} />
          </span>
          <SerifH as="h1" size={22}>Грамматика</SerifH>
        </div>
        <div className="mode-v2-actions">
          <span className="mode-v2-hello">Hi, {userName}</span>
          <IconButton icon="chart-no-axes-column" size="sm" label="Мой прогресс" onClick={() => setProgressOpen(true)} />
          <IconButton icon="book-marked" size="sm" label="Мои слова" onClick={() => setWordsOpen(true)} />
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
                Программа тем от A1 до C1: правило с примерами → практика →
                следующая тема открывается после прохождения.
              </span>
            </button>

            <button
              type="button"
              className="grm-mode-card"
              onClick={() => void startWeakPoints()}
            >
              <span className="grm-mode-card__emoji" aria-hidden>🎯</span>
              <span className="grm-mode-card__title">Проверить себя</span>
              <span className="grm-mode-card__hint">
                10 упражнений по ошибкам из твоих реальных разговоров с
                AI Tutor — без настроек, сразу в бой.
              </span>
            </button>
          </div>
        )}

        {/* ── NO MISTAKES: заглушка test-трека ── */}
        {phase === "no_mistakes" && (
          <div className="grm-stub">
            <span className="grm-stub__emoji" aria-hidden>🎙️</span>
            <p className="grm-stub__title">Пока нечего разбирать</p>
            <p className="grm-stub__hint">
              Сначала поговори с AI Tutor — я запомню твои ошибки из живой
              речи и соберу из них персональные упражнения.
            </p>
            <button
              type="button"
              className="grm-primary-btn"
              onClick={() => { resetRound(); setPhase("home"); }}
            >
              Понятно
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
              {renderTheoryBlocks(lesson.theory)}
            </div>
            <button type="button" className="grm-primary-btn" onClick={startLessonPractice}>
              {exState === "ready" ? "К практике →" : "К практике (задания готовятся…)"}
            </button>
          </div>
        )}

        {/* ── LOADING ── */}
        {phase === "loading" && (
          <div className="lst-loading">
            <div className="lst-spinner" aria-hidden />
            <p className="lst-loading__title">
              {track === "learn" ? "Готовлю урок…" : "Собираю твои ошибки…"}
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
                    onClick={() => { resetRound(); void startWeakPoints(); }}
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
                  else setPhase("home");
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
                    void startWeakPoints();
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
        <div className="modal-screen">
          <WordsScreen apiBase={API_BASE} onClose={() => setWordsOpen(false)} />
        </div>
      )}
      {progressOpen && (
        <div className="modal-screen">
          <ProgressScreen
            apiBase={API_BASE}
            initData={WebApp.initData || ""}
            onClose={() => setProgressOpen(false)}
          />
        </div>
      )}

      {paywall && (
        <LockScreen
          kind="limit_reached"
          botUsername={BOT_USERNAME}
          message="Бесплатные уроки грамматики на сегодня закончились. С подпиской — без лимитов."
          onDismiss={() => setPaywall(false)}
          onOpenSubscribe={inTelegram ? undefined : () => setSubscribeOpen(true)}
        />
      )}

      {subscribeOpen && (
        <SubscribeScreen
          onClose={() => setSubscribeOpen(false)}
          onPaid={() => setPaywall(false)}
        />
      )}
    </div>
  );
}
