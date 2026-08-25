/**
 * LevelTestScreen.tsx — тест уровня английского (CEFR A1–C1).
 *
 * Главное про скорость: во время прохождения LLM не участвует. Вопросы
 * приходят из калиброванного банка на бэкенде, каждый шаг — обычный
 * быстрый POST. Уровень считается там же и приходит ВМЕСТЕ с последним
 * ответом, поэтому результат виден мгновенно.
 *
 * AI-разбор запрашивается отдельно и подгружается уже поверх показанного
 * результата — человек не ждёт генерацию, чтобы узнать свой уровень.
 */

import { useCallback, useEffect, useState } from "react";
import {
  startLevelTest,
  answerLevelTest,
  fetchLevelReport,
  pollLevelAudio,
  API_BASE,
  type LevelScreen,
  type LevelResult,
} from "./auth";
import { ymReachGoal } from "./metrika";
import { Icon } from "./ds-react/Icon";
import { IconButton } from "./ds-react/IconButton";
import { SerifH } from "./ds-react/typography";
import { loadSettings, saveSettings, type Level } from "./tutorSettings";
import { useLucide } from "./lucide";

interface Props {
  onExit: () => void;
}

type Phase = "intro" | "running" | "result";

const SKILL_LABELS: Record<string, string> = {
  tense: "времена",
  article: "артикли",
  preposition: "предлоги",
  word_choice: "выбор слов",
  structure: "конструкции",
  vocab: "слова",
  listening: "на слух",
};

/** Подпись над заданием — чтобы человек понимал, что от него хотят. */
const SUBKIND_HINT: Record<string, string> = {
  grammar: "Выбери правильную форму",
  vocab: "Как переводится это слово?",
  listening: "Вопрос по подкасту",
};

const CEFR_MEANING: Record<string, string> = {
  A1: "Начальный — простые фразы о себе и повседневных вещах.",
  A2: "Элементарный — бытовые темы, короткие диалоги, простое прошедшее.",
  B1: "Средний — можешь поддержать разговор на знакомые темы и объясниться.",
  B2: "Выше среднего — свободно обсуждаешь сложные темы, спорить уже получается.",
  C1: "Продвинутый — говоришь бегло и точно, улавливаешь оттенки смысла.",
};

/** Уровень теста мапится на уровень тьютора: у настроек нет A1. */
function toTutorLevel(cefr: string): Level {
  if (cefr === "A1") return "A2";
  if (cefr === "A2" || cefr === "B1" || cefr === "B2" || cefr === "C1") return cefr;
  return "B1";
}

export function LevelTestScreen({ onExit }: Props) {
  const [phase, setPhase] = useState<Phase>("intro");
  const [testId, setTestId] = useState<string>("");
  const [screen, setScreen] = useState<LevelScreen | null>(null);
  const [previous, setPrevious] = useState<{ cefr: string } | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [feedback, setFeedback] = useState<
    { correct: boolean; correct_answer: string; note: string } | null
  >(null);
  const [waiting, setWaiting] = useState<boolean>(false);
  const [pending, setPending] = useState<LevelScreen | null>(null);
  const [result, setResult] = useState<LevelResult | null>(null);
  const [report, setReport] = useState<string>("");
  const [reportFailed, setReportFailed] = useState<boolean>(false);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string>("");
  const [applied, setApplied] = useState<boolean>(false);

  useLucide(`${phase}-${screen?.index ?? 0}-${!!feedback}`);

  const begin = useCallback(async () => {
    setBusy(true);
    setError("");
    ymReachGoal("level_test_started");
    const r = await startLevelTest();
    setBusy(false);
    if (!r) {
      setError("Не получилось начать тест. Попробуй ещё раз.");
      return;
    }
    setTestId(r.test_id);
    setScreen(r.question);
    setPrevious(r.previous);
    setPhase("running");
  }, []);

  const applyAnswer = useCallback((r: NonNullable<Awaited<ReturnType<typeof answerLevelTest>>>) => {
    if (r.done && r.result) {
      setResult(r.result);
      setPending(null);
    } else if (r.question) {
      setPending(r.question);
    }
  }, []);

  const choose = useCallback(async (choice: string) => {
    if (!screen || screen.kind !== "quiz" || feedback || busy) return;
    setPicked(choice);
    setBusy(true);
    const r = await answerLevelTest(testId, screen.id, choice);
    setBusy(false);
    if (!r) {
      setError("Ответ не отправился. Проверь соединение.");
      setPicked("");
      return;
    }
    setFeedback({
      correct: !!r.correct,
      correct_answer: r.correct_answer || "",
      note: r.note || "",
    });
    applyAnswer(r);
  }, [screen, feedback, busy, testId, applyAnswer]);

  /** Подкаст прослушан — листаем дальше. Ответа тут нет. */
  const finishAudio = useCallback(async () => {
    if (!screen || screen.kind !== "listen_audio" || busy) return;
    setBusy(true);
    const r = await answerLevelTest(testId, `audio:${screen.block}`, "");
    setBusy(false);
    if (!r) {
      setError("Не получилось перейти дальше. Проверь соединение.");
      return;
    }
    if (r.done && r.result) {
      setResult(r.result);
      setPhase("result");
      return;
    }
    if (r.question) setScreen(r.question);
  }, [screen, busy, testId]);

  // Подкаст готовится в фоне и почти всегда успевает. Если нет — опрашиваем,
  // пока не будет готов, а человек видит честное «готовим подкаст».
  useEffect(() => {
    if (!screen || screen.kind !== "listen_audio") { setWaiting(false); return; }
    if (screen.ready || screen.failed) { setWaiting(false); return; }
    setWaiting(true);
    let alive = true;
    const tick = async () => {
      if (!alive) return;
      const st = await pollLevelAudio(testId, screen.block);
      if (!alive) return;
      if (st && (st.ready || st.failed)) {
        setScreen(st);
        setWaiting(false);
        // Генерация не удалась — блок выкинут, просто идём дальше.
        if (st.failed) void finishAudio();
        return;
      }
      window.setTimeout(tick, 2000);
    };
    const id = window.setTimeout(tick, 1500);
    return () => { alive = false; window.clearTimeout(id); };
  }, [screen, testId, finishAudio]);

  const next = useCallback(() => {
    setFeedback(null);
    setPicked("");
    if (pending) {
      setScreen(pending);
      setPending(null);
      return;
    }
    // Тест окончен — показываем результат СРАЗУ, разбор догрузим следом.
    setPhase("result");
    if (result) {
      ymReachGoal("level_test_completed", {
        cefr: result.cefr,
        score: result.correct_cnt,
      });
    }
  }, [pending, result]);

  // AI-разбор: запрашиваем только когда результат уже на экране.
  useEffect(() => {
    if (phase !== "result" || !testId || report || reportFailed) return;
    let alive = true;
    void (async () => {
      const r = await fetchLevelReport(testId);
      if (!alive) return;
      if (r?.report) setReport(r.report);
      else setReportFailed(true);
    })();
    return () => { alive = false; };
  }, [phase, testId, report, reportFailed]);

  const applyLevel = useCallback(() => {
    if (!result) return;
    const s = loadSettings();
    saveSettings({ ...s, level: toTutorLevel(result.cefr) });
    setApplied(true);
    ymReachGoal("level_test_applied", { cefr: result.cefr });
  }, [result]);

  const shell = (body: React.ReactNode) => (
    <div className="tutor-shell grm-screen">
      <div className="bg-orb bg-orb--one" aria-hidden />
      <div className="bg-orb bg-orb--two" aria-hidden />
      <header className="mode-v2-top">
        <button type="button" className="mode-v2-back" onClick={onExit} aria-label="Назад">
          <Icon name="arrow-left" size={16} />
          <span>Назад</span>
        </button>
        <div className="mode-v2-title">
          <span className="mode-v2-title__icon mode-v2-title__icon--accent">
            <Icon name="gauge" size={16} />
          </span>
          <SerifH as="h1" size={22}>Уровень</SerifH>
        </div>
        <div className="mode-v2-actions" />
      </header>
      <main className="lst-main">{body}</main>
    </div>
  );

  // ── Вступление ────────────────────────────────────────────────────
  if (phase === "intro") {
    return shell(
      <div className="lvt-intro">
        <div className="lvt-intro__icon" aria-hidden>🎯</div>
        <SerifH as="h2" size={26}>Какой у тебя уровень английского?</SerifH>
        <p className="lvt-intro__lead">
          Около семи минут. Тест адаптивный: ответил верно — следующий
          вопрос сложнее, ошибся — проще. Так уровень определяется точнее,
          чем стандартным набором. Задания идут вперемешку.
        </p>
        <ul className="lvt-intro__list">
          <li>Грамматика, слова и два коротких подкаста на понимание</li>
          <li>Определим уровень по шкале CEFR: от A1 до C1</li>
          <li>Покажем, что уже уверенно, а что провисает</li>
          <li>Настроим под тебя разговор, подкасты и грамматику</li>
        </ul>
        {error && <p className="lvt-error">{error}</p>}
        <button
          type="button"
          className="grm-primary-btn"
          onClick={() => void begin()}
          disabled={busy}
        >
          {busy ? "Готовим…" : "Начать тест"}
        </button>
        <p className="lvt-intro__note">
          Отвечай сам, без подсказок и переводчика — иначе тест покажет не
          твой уровень, а уровень переводчика.
        </p>
      </div>,
    );
  }

  // ── Прохождение ───────────────────────────────────────────────────
  if (phase === "running" && screen) {
    const pct = Math.round((screen.index / screen.total) * 100);
    const progress = (
      <div className="grm-progress">
        <div className="grm-progress__bar">
          <div className="grm-progress__fill" style={{ width: `${pct}%` }} aria-hidden />
        </div>
        <span className="grm-progress__label">
          {screen.index} / {screen.total}
        </span>
      </div>
    );

    // Экран прослушивания: плеер и кнопка «дальше». Ответа тут нет.
    if (screen.kind === "listen_audio") {
      return shell(
        <div className="grm-exercise">
          {progress}
          <div className="lvt-audio">
            <div className="lvt-audio__icon" aria-hidden>🎧</div>
            <div className="lvt-audio__title">
              Подкаст {screen.block + 1} из 2
              {screen.level ? ` · уровень ${screen.level}` : ""}
            </div>
            <p className="lvt-audio__lead">
              Послушай запись целиком — примерно минута. Потом будут два
              вопроса по содержанию. Можно переслушать.
            </p>
            {screen.ready && screen.audio_url ? (
              <audio
                className="lvt-audio__player"
                src={`${API_BASE}${screen.audio_url}`}
                controls
                preload="auto"
              />
            ) : (
              <div className="lvt-audio__wait">
                <div className="lvt-skeleton" aria-label="Готовим подкаст">
                  <span /><span />
                </div>
                <p className="lvt-audio__waittext">
                  {waiting ? "Готовим подкаст…" : "Почти готово…"}
                </p>
              </div>
            )}
          </div>
          {error && <p className="lvt-error">{error}</p>}
          <button
            type="button"
            className="grm-primary-btn"
            onClick={() => void finishAudio()}
            disabled={!screen.ready || busy}
          >
            {screen.ready ? "Прослушал, дальше →" : "Ждём подкаст…"}
          </button>
        </div>,
      );
    }

    const isLast = screen.index >= screen.total;
    return shell(
      <div className="grm-exercise">
        {progress}

        {previous && !feedback && screen.index === 1 && (
          <p className="lvt-prev">
            Прошлый результат: <b>{previous.cefr}</b>. Посмотрим, что изменилось.
          </p>
        )}

        <div className="lvt-hint">{SUBKIND_HINT[screen.subkind]}</div>
        <p
          className={
            screen.subkind === "vocab" ? "grm-prompt lvt-word" : "grm-prompt"
          }
        >
          {screen.prompt}
        </p>

        <div className="grm-choices">
          {screen.choices.map((c) => {
            let state: "idle" | "picked" | "right" | "wrong" = "idle";
            if (feedback) {
              if (c === feedback.correct_answer) state = "right";
              else if (c === picked) state = "wrong";
            } else if (c === picked) {
              state = "picked";
            }
            return (
              <button
                key={c}
                type="button"
                className="grm-choice"
                data-state={state}
                disabled={!!feedback || busy}
                onClick={() => void choose(c)}
              >
                {c}
              </button>
            );
          })}
        </div>

        {feedback && (
          <div
            className={
              "grm-feedback " +
              (feedback.correct ? "grm-feedback--correct" : "grm-feedback--wrong")
            }
            role="status"
          >
            <div className="grm-feedback__head">
              {feedback.correct
                ? "✅ Верно"
                : `❌ Правильный ответ: ${feedback.correct_answer}`}
            </div>
            {feedback.note && (
              <div className="grm-feedback__explanation">{feedback.note}</div>
            )}
          </div>
        )}

        {error && <p className="lvt-error">{error}</p>}

        {feedback && (
          <button type="button" className="grm-primary-btn" onClick={next}>
            {isLast ? "Узнать уровень" : "Дальше →"}
          </button>
        )}
      </div>,
    );
  }

  // ── Результат ─────────────────────────────────────────────────────
  if (phase === "result" && result) {
    const skills = Object.entries(result.by_skill)
      .filter(([, st]) => st.total > 0)
      .sort((a, b) => b[1].total - a[1].total);
    const grew = previous && previous.cefr !== result.cefr;
    return shell(
      <div className="lvt-result">
        <div className="lvt-badge">
          <span className="lvt-badge__label">Твой уровень</span>
          <span className="lvt-badge__cefr">{result.cefr}</span>
          <span className="lvt-badge__score">
            {result.correct_cnt} из {result.total_cnt} верных
          </span>
        </div>

        <p className="lvt-meaning">{CEFR_MEANING[result.cefr]}</p>

        {grew && (
          <p className="lvt-prev">
            В прошлый раз было <b>{previous!.cefr}</b>.
          </p>
        )}

        {result.listening && (
          <p className="lvt-listen-line">
            🎧 Понимание на слух: <b>{result.listening.correct} из{" "}
            {result.listening.total}</b>. В уровень не входит — сложность
            подкаста задавали мы, это отдельный показатель.
          </p>
        )}

        <div className="lvt-skills">
          <div className="lvt-skills__title">По навыкам</div>
          {skills.map(([sk, st]) => {
            const pct = Math.round((st.correct / st.total) * 100);
            return (
              <div key={sk} className="lvt-skill">
                <span className="lvt-skill__name">{SKILL_LABELS[sk] || sk}</span>
                <span className="lvt-skill__bar" aria-hidden>
                  <span
                    className="lvt-skill__fill"
                    data-weak={pct < 60 ? "1" : undefined}
                    style={{ width: `${pct}%` }}
                  />
                </span>
                <span className="lvt-skill__num">
                  {st.correct}/{st.total}
                </span>
              </div>
            );
          })}
        </div>

        {/* AI-разбор грузится уже поверх показанного результата */}
        <div className="lvt-report">
          <div className="lvt-report__title">Что это значит и что делать</div>
          {report ? (
            <p className="lvt-report__text">{report}</p>
          ) : reportFailed ? (
            <p className="lvt-report__text lvt-report__text--muted">
              Разбор не загрузился, но уровень засчитан. Можно попробовать
              пройти тест ещё раз позже.
            </p>
          ) : (
            <div className="lvt-skeleton" aria-label="Готовим разбор">
              <span /><span /><span />
            </div>
          )}
        </div>

        <button
          type="button"
          className="grm-primary-btn"
          onClick={applyLevel}
          disabled={applied}
        >
          {applied
            ? `✓ Уровень ${toTutorLevel(result.cefr)} применён`
            : `Настроить занятия под ${toTutorLevel(result.cefr)}`}
        </button>
        <button type="button" className="lvt-secondary" onClick={onExit}>
          Вернуться к режимам
        </button>
      </div>,
    );
  }

  return shell(<p className="lvt-intro__lead">Загрузка…</p>);
}
