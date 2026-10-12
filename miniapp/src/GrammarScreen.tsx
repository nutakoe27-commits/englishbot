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
import { ModalScreen } from "./ModalScreen";
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
import { GrammarRound, GrammarRequestError, grammarRequest, type GrammarTicket } from "./grammarRequests";

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
  const roundRef = useRef(new GrammarRound());
  useLucide(`${progressOpen}-${wordsOpen}-${paywall}`);

  // Learn-трек
  const [topicLevels, setTopicLevels] = useState<Record<string, TopicInfo[]> | null>(null);
  const [topicsLevel, setTopicsLevel] = useState<TopicLevel>("A1");
  const [topicsError, setTopicsError] = useState<string>("");
  const [lesson, setLesson] = useState<LessonData | null>(null);
  const [lessonResult, setLessonResult] = useState<LessonResult | null>(null);
  const [exState, setExState] = useState<ExercisesState>("idle");
  type PreparedRound = { exercises: Exercise[]; session_id: string };
  const preparedRef = useRef<PreparedRound | null>(null);
  const practiceRequestedRef = useRef(false);
  const activationRef = useRef<Promise<void> | null>(null);
  const activeSessionRef = useRef("");
  const [retryPractice, setRetryPractice] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState("");
  const finishRef = useRef<{ path: string; body: unknown } | null>(null);

  const duration = () => sessionStartRef.current ? Math.max(0, Math.round((Date.now() - sessionStartRef.current) / 1000)) : 0;
  const cancelRound = () => {
    roundRef.current.cancel();
    const key = activeSessionRef.current || preparedRef.current?.session_id;
    if (key) void grammarRequest(`${API_BASE}/api/grammar/abandon`, {
      init_data: WebApp.initData || "", session_id: key, duration_sec: duration(),
    }, { keepalive: true }).catch(() => {});
    activeSessionRef.current = "";
    preparedRef.current = null;
    practiceRequestedRef.current = false;
    activationRef.current = null;
  };
  useEffect(() => {
    const hide = () => cancelRound();
    window.addEventListener("pagehide", hide);
    return () => { window.removeEventListener("pagehide", hide); cancelRound(); };
  }, []);

  const activatePractice = (prepared: PreparedRound, ticket: GrammarTicket): Promise<void> => {
    if (activationRef.current) return activationRef.current;
    const pending = (async () => {
      setPhase("loading");
      try {
        const data = await grammarRequest<{ session_id: string }>(`${API_BASE}/api/grammar/start`, {
          init_data: WebApp.initData || "", session_id: prepared.session_id,
        }, { signal: ticket.signal, retry: true });
        if (!roundRef.current.owns(ticket)) return;
        activeSessionRef.current = data.session_id;
        setSessionId(data.session_id);
        sessionStartRef.current = Date.now();
        setRetryPractice(false);
        setPhase("exercise");
      } catch (e) {
        if (!roundRef.current.owns(ticket)) return;
        if (e instanceof GrammarRequestError && e.status === 402) { setPaywall(true); setPhase("home"); return; }
        const expired = e instanceof GrammarRequestError && e.status === 410;
        if (expired) preparedRef.current = null;
        setRetryPractice(!expired);
        setError(e instanceof Error ? e.message : String(e));
        setPhase("error");
      }
    })();
    activationRef.current = pending;
    void pending.finally(() => { if (activationRef.current === pending) activationRef.current = null; });
    return pending;
  };

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
          duration_sec: duration(),
        }),
      }).catch(() => {
        /* heartbeat best-effort */
      });
    };
    tick();
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
  const fetchLessonExercises = async (topicKey: string, ticket: GrammarTicket) => {
    setExState("pending");
    try {
      const data = await grammarRequest<PreparedRound>(`${API_BASE}/api/grammar/lesson/exercises`, {
        init_data: WebApp.initData || "", topic_key: topicKey,
      }, { signal: ticket.signal });
      if (!roundRef.current.owns(ticket)) return;
      if (!Array.isArray(data.exercises) || data.exercises.length === 0) throw new Error("Сервер не вернул упражнений");
      preparedRef.current = data;
      setExercises(data.exercises);
      setExState("ready");
      if (practiceRequestedRef.current) await activatePractice(data, ticket);
    } catch (e) {
      if (!roundRef.current.owns(ticket)) return;
      setError(e instanceof Error ? e.message : String(e));
      setExState("error");
      if (practiceRequestedRef.current) setPhase("error");
    }
  };

  // ── Learn: открыть урок — теория приходит мгновенно (рукописная) ──────
  const openLesson = async (topicKey: string) => {
    cancelRound();
    const ticket = roundRef.current.begin();
    sessionStartRef.current = 0;
    finishRef.current = null;
    setRetryPractice(false);
    setSaveState("idle");
    setError("");
    setExercises([]);
    setAnswers([]);
    setCurrentIndex(0);
    setSessionId("");
    setLessonResult(null);
    setExState("idle");
    setPhase("loading");
    setTrack("learn");

    try {
      const res = await fetch(`${API_BASE}/api/grammar/lesson`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          init_data: WebApp.initData || "",
          topic_key: topicKey,
        }),
        signal: ticket.signal,
      });
      if (!roundRef.current.owns(ticket)) return;
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
      if (!roundRef.current.owns(ticket)) return;
      setLesson({
        topic_key: data.topic_key,
        title_ru: data.title_ru,
        theory: data.theory,
      });
      setSettings((s) => ({ ...s, lastTopicKey: data.topic_key }));
      setPhase("theory");
      // Упражнения готовятся в фоне, пока юзер читает теорию.
      void fetchLessonExercises(topicKey, ticket);
    } catch (e: unknown) {
      if (!roundRef.current.owns(ticket)) return;
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  };

  const startLessonPractice = () => {
    practiceRequestedRef.current = true;
    const ticket = roundRef.current.current();
    if (preparedRef.current && ticket) void activatePractice(preparedRef.current, ticket);
    else if (exState === "error") setPhase("error");
    else setPhase("loading");
  };

  // ── Test: «Проверить себя» — всегда по реальным ошибкам, без настроек ──
  const startWeakPoints = async () => {
    cancelRound();
    const ticket = roundRef.current.begin();
    sessionStartRef.current = 0;
    finishRef.current = null;
    setRetryPractice(false);
    setSaveState("idle");
    setError(""); setExercises([]); setAnswers([]); setCurrentIndex(0); setSessionId("");
    setLesson(null); setLessonResult(null); setPhase("loading"); setTrack("test");
    try {
      const data = await grammarRequest<PreparedRound>(`${API_BASE}/api/grammar/generate`, {
        init_data: WebApp.initData || "", mode: "weak_points", level: loadTutorSettings().level,
      }, { signal: ticket.signal });
      if (!roundRef.current.owns(ticket)) return;
      if (!Array.isArray(data.exercises) || data.exercises.length === 0) throw new Error("Сервер не вернул упражнений");
      preparedRef.current = data;
      setExercises(data.exercises);
      await activatePractice(data, ticket);
    } catch (e) {
      if (!roundRef.current.owns(ticket)) return;
      if (e instanceof GrammarRequestError && e.status === 409) { setPhase("no_mistakes"); return; }
      if (e instanceof GrammarRequestError && e.status === 402) { setPaywall(true); setPhase("home"); return; }
      setError(e instanceof Error ? e.message : String(e)); setPhase("error");
    }
  };

  const cancelGeneration = () => { cancelRound(); setPhase(track === "learn" ? "topics" : "home"); };

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

  const saveResult = async (path: string, body: unknown) => {
    const ticket = roundRef.current.current();
    const submission = finishRef.current || { path, body };
    finishRef.current = submission;
    setPhase("summary"); setSaveState("saving"); setSaveError("");
    try {
      const result = await grammarRequest<LessonResult>(`${API_BASE}${submission.path}`, submission.body, { retry: true, keepalive: true });
      if (ticket && !roundRef.current.owns(ticket)) return;
      activeSessionRef.current = "";
      setSaveState("saved");
      if (submission.path.includes("lesson")) setLessonResult(result);
    } catch (e) {
      if (ticket && !roundRef.current.owns(ticket)) return;
      setSaveState("error");
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  };

  const finishSession = () => saveResult("/api/grammar/finish", {
    init_data: WebApp.initData || "", session_id: sessionId, duration_sec: duration(),
    results: answers.map(a => ({ exercise_id: a.exercise_id, user_answer: a.user_answer, is_correct: a.is_correct, category: a.category })),
  });
  const finishLesson = () => saveResult("/api/grammar/lesson/finish", {
    init_data: WebApp.initData || "", session_id: sessionId, topic_key: lesson?.topic_key || "",
    correct: answers.filter(a => a.is_correct).length, total: exercises.length, duration_sec: duration(),
  });

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
    cancelRound();
    sessionStartRef.current = 0;
    finishRef.current = null;
    setRetryPractice(false);
    setSaveState("idle");
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
              <div className="lst-chips lst-chips--grid" style={{ "--cols": levelTabs.length } as React.CSSProperties}>
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
            <p className="lst-loading__hint">Готовлю задания. Иногда это занимает несколько минут.</p>
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
            {saveState === "saving" && <p role="status">Сохраняю результат…</p>}
            {saveState === "error" && <div role="alert">
              <p>Результат пока не сохранён. {saveError}</p>
              <button type="button" className="grm-primary-btn" onClick={() => { if (finishRef.current) void saveResult(finishRef.current.path, finishRef.current.body); }}>Сохранить ещё раз</button>
            </div>}
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
            <p className="lst-error__title">{retryPractice ? "Не получилось начать практику" : "Не получилось сгенерировать"}</p>
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
                  if (retryPractice && preparedRef.current && roundRef.current.current()) {
                    void activatePractice(preparedRef.current, roundRef.current.current()!);
                  } else if (track === "learn" && settings.lastTopicKey) {
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
        <ModalScreen>
          <WordsScreen apiBase={API_BASE} onClose={() => setWordsOpen(false)} />
        </ModalScreen>
      )}
      {progressOpen && (
        <ModalScreen>
          <ProgressScreen
            apiBase={API_BASE}
            initData={WebApp.initData || ""}
            onClose={() => setProgressOpen(false)}
          />
        </ModalScreen>
      )}

      {paywall && (
        <LockScreen
          kind="limit_reached"
          botUsername={BOT_USERNAME}
          message="Бесплатные уроки грамматики на сегодня закончились. С подпиской — без лимитов."
          onDismiss={() => setPaywall(false)}
          onOpenSubscribe={() => setSubscribeOpen(true)}
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
