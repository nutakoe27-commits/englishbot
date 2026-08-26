/**
 * LevelLanding.tsx — публичный лендинг теста уровня английского (/level).
 *
 * Зачем отдельная страница. Мини-апп в Telegram открывается не у всех (VPN,
 * блокировки), а тест уровня — самый лёгкий вход в продукт: он полезен сам
 * по себе и не требует ничего вводить. Поэтому его можно пройти прямо здесь,
 * анонимно, и уже потом решать про аккаунт.
 *
 * Стена регистрации стоит ровно в одном месте: **уровень показываем сразу и
 * бесплатно**, а разбор по навыкам, план от ИИ и сохранение результата —
 * после входа. Полный результат до этого момента вообще не покидает сервер
 * (см. level_test._trim_result), так что «замочек» здесь настоящий, а не
 * нарисованный поверх уже полученных данных.
 *
 * Версия теста короткая: 10 заданий из банка, без подкастов. Это осознанно —
 * анонимный трафик не должен ни разу дёрнуть LLM или синтез речи.
 *
 * Переживание входа. Вход через Яндекс ID делает полную перезагрузку и
 * возвращает на корень сайта, поэтому test_id и уже показанный уровень
 * лежат в localStorage, а намерение «я шёл открывать разбор» — в
 * et_level_intent, который main.tsx превращает в возврат на /level?claim=1.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  answerLevelTest,
  claimLevelTest,
  fetchLevelReport,
  startLevelTest,
  type LevelQuestion,
  type LevelResult,
} from "./auth";
import { LandingNav } from "./LandingNav";
import { ymHit, ymReachGoal } from "./metrika";
import "./Landing.css";
import "./LevelLanding.css";

/** Намерение «вошёл, чтобы открыть разбор» — переживает OAuth-redirect. */
export const LEVEL_INTENT_KEY = "et_level_intent";
/** Пройденный тест: id и уже показанный уровень. */
const SAVE_KEY = "et_level_result";
/** Столько же живёт состояние теста на сервере (_TEST_TTL_SEC). */
const SAVE_TTL_MS = 60 * 60 * 1000;

interface Props {
  /** Юзер уже вошёл — разбор открывается без стены. */
  authed: boolean;
  /** Открыть экран входа (им владеет main.tsx). */
  onLogin: () => void;
  /** Перейти в приложение. */
  onOpenApp: () => void;
}

interface Saved {
  test_id: string;
  result: LevelResult;
  ts: number;
}

const CEFR_MEANING: Record<string, string> = {
  A1: "Начальный — простые фразы о себе и повседневных вещах.",
  A2: "Элементарный — бытовые темы, короткие диалоги, простое прошедшее.",
  B1: "Средний — можешь поддержать разговор на знакомые темы и объясниться.",
  B2: "Выше среднего — свободно обсуждаешь сложные темы, спорить уже получается.",
  C1: "Продвинутый — говоришь бегло и точно, улавливаешь оттенки смысла.",
};

const SKILL_LABELS: Record<string, string> = {
  tense: "времена",
  article: "артикли",
  preposition: "предлоги",
  word_choice: "выбор слов",
  structure: "конструкции",
  vocab: "слова",
  listening: "на слух",
};

const SUBKIND_HINT: Record<string, string> = {
  grammar: "Выбери правильную форму",
  vocab: "Как переводится это слово?",
  listening: "Вопрос по подкасту",
};

function readSaved(): Saved | null {
  try {
    const raw = localStorage.getItem(SAVE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Saved;
    if (!v?.test_id || !v?.result?.cefr) return null;
    if (Date.now() - (v.ts || 0) > SAVE_TTL_MS) {
      localStorage.removeItem(SAVE_KEY);
      return null;
    }
    return v;
  } catch {
    return null;
  }
}

function writeSaved(test_id: string, result: LevelResult): void {
  try {
    localStorage.setItem(SAVE_KEY, JSON.stringify({ test_id, result, ts: Date.now() }));
  } catch {
    /* приватный режим — переживать вход просто нечем */
  }
}

/** Пришли ли мы сюда сразу после входа «чтобы открыть разбор». */
function consumeClaimIntent(): boolean {
  if (typeof window === "undefined") return false;
  let intent = false;
  try {
    if (new URLSearchParams(window.location.search).get("claim") === "1") {
      intent = true;
      const url = new URL(window.location.href);
      url.searchParams.delete("claim");
      window.history.replaceState(null, "", url.pathname + (url.search || "") + url.hash);
    }
    if (localStorage.getItem(LEVEL_INTENT_KEY) === "1") {
      intent = true;
      localStorage.removeItem(LEVEL_INTENT_KEY);
    }
  } catch { /* private mode */ }
  return intent;
}

type Phase = "intro" | "running" | "result";

export function LevelLanding({ authed, onLogin, onOpenApp }: Props) {
  const restored = useRef<Saved | null>(null);
  if (restored.current === null) restored.current = readSaved();

  const [phase, setPhase] = useState<Phase>(restored.current ? "result" : "intro");
  const [testId, setTestId] = useState<string>(restored.current?.test_id || "");
  const [result, setResult] = useState<LevelResult | null>(restored.current?.result || null);
  const [screen, setScreen] = useState<LevelQuestion | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [feedback, setFeedback] = useState<
    { correct: boolean; correct_answer: string; note: string } | null
  >(null);
  const [pending, setPending] = useState<LevelQuestion | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string>("");
  const [report, setReport] = useState<string>("");
  const [reportFailed, setReportFailed] = useState<boolean>(false);
  /** Результат не удалось привязать: сервер уже забыл тест (перезапуск/час). */
  const [claimLost, setClaimLost] = useState<boolean>(false);

  const locked = !!result?.locked;

  useEffect(() => {
    // Глобально html/body/#root — overflow:hidden (SPA с фиксированным
    // вьюпортом). Лендингу нужен обычный скролл документа.
    document.body.classList.add("lp-active");
    ymHit(window.location.origin + "/level", "Тест уровня английского");
    ymReachGoal("level_landing_view");
    // Флаг возврата после входа гасим сразу, иначе он сработает ещё раз.
    consumeClaimIntent();
    return () => { document.body.classList.remove("lp-active"); };
  }, []);

  // Вошёл — забираем анонимно пройденный тест себе. Тут же приходит полный
  // результат: разбивка по навыкам, которую аноним не получал.
  useEffect(() => {
    if (!authed || !testId || !locked) return;
    let alive = true;
    void (async () => {
      const r = await claimLevelTest(testId);
      if (!alive) return;
      if (r?.result) {
        setResult(r.result);
        writeSaved(testId, r.result);
        ymReachGoal("level_landing_claimed", { cefr: r.result.cefr });
      } else {
        setClaimLost(true);
      }
    })();
    return () => { alive = false; };
  }, [authed, testId, locked]);

  // AI-разбор — только когда стена уже пройдена. Грузится поверх уровня.
  useEffect(() => {
    if (!authed || locked || phase !== "result" || !testId) return;
    if (report || reportFailed || claimLost) return;
    let alive = true;
    void (async () => {
      const r = await fetchLevelReport(testId);
      if (!alive) return;
      if (r?.report) setReport(r.report);
      else setReportFailed(true);
    })();
    return () => { alive = false; };
  }, [authed, locked, phase, testId, report, reportFailed, claimLost]);

  const begin = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    ymReachGoal("level_landing_start");
    const r = await startLevelTest("short");
    setBusy(false);
    if (!r || r.question.kind !== "quiz") {
      setError("Не получилось начать тест. Обнови страницу и попробуй ещё раз.");
      return;
    }
    setTestId(r.test_id);
    setScreen(r.question);
    setResult(null);
    setReport("");
    setReportFailed(false);
    setClaimLost(false);
    setPhase("running");
    window.scrollTo({ top: 0, behavior: "auto" });
  }, [busy]);

  const choose = useCallback(async (choice: string) => {
    if (!screen || feedback || busy) return;
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
    if (r.done && r.result) {
      setResult(r.result);
      writeSaved(testId, r.result);
      ymReachGoal("level_landing_done", {
        cefr: r.result.cefr,
        score: r.result.correct_cnt,
      });
    } else if (r.question && r.question.kind === "quiz") {
      setPending(r.question);
    }
  }, [screen, feedback, busy, testId]);

  const next = useCallback(() => {
    setFeedback(null);
    setPicked("");
    if (pending) {
      setScreen(pending);
      setPending(null);
      return;
    }
    setPhase("result");
    window.scrollTo({ top: 0, behavior: "auto" });
  }, [pending]);

  const openSignup = useCallback(() => {
    ymReachGoal("level_landing_signup_click", { cefr: result?.cefr || "" });
    try { localStorage.setItem(LEVEL_INTENT_KEY, "1"); } catch { /* private mode */ }
    onLogin();
  }, [onLogin, result]);

  const nav = (
    <LandingNav
      badge="тест уровня"
      items={[
        { label: "Главная", href: "/" },
        { label: "Для школ английского", href: "/schools" },
      ]}
      cta={
        authed
          ? { label: "Открыть приложение", onClick: onOpenApp }
          : { label: "Войти", onClick: onLogin }
      }
    />
  );

  // ── Прохождение ───────────────────────────────────────────────────
  if (phase === "running" && screen) {
    const pct = Math.round((screen.index / screen.total) * 100);
    const isLast = screen.index >= screen.total;
    return (
      <div className="lp">
        {nav}
        <section className="lvl-test">
          <div className="lp-container">
            <div className="lvl-card">
              <div className="lvl-progress">
                <div className="lvl-progress__bar">
                  <span className="lvl-progress__fill" style={{ width: `${pct}%` }} aria-hidden />
                </div>
                <span className="lvl-progress__label">
                  {screen.index} / {screen.total}
                </span>
              </div>

              <div className="lvl-hint">{SUBKIND_HINT[screen.subkind]}</div>
              <p className={screen.subkind === "vocab" ? "lvl-prompt lvl-prompt--word" : "lvl-prompt"}>
                {screen.prompt}
              </p>

              <div className="lvl-choices">
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
                      className="lvl-choice"
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
                <div className="lvl-feedback" role="status">
                  <div className="lvl-feedback__head">
                    {feedback.correct
                      ? "✅ Верно"
                      : `❌ Правильный ответ: ${feedback.correct_answer}`}
                  </div>
                  {feedback.note && <div className="lvl-feedback__note">{feedback.note}</div>}
                </div>
              )}

              {error && <p className="lvl-error">{error}</p>}

              {feedback && (
                <button
                  type="button"
                  className="lp-btn lp-btn--primary lp-btn--lg lvl-next"
                  onClick={next}
                >
                  {isLast ? "Узнать уровень" : "Дальше →"}
                </button>
              )}
            </div>
          </div>
        </section>
        <Footer />
      </div>
    );
  }

  // ── Результат ─────────────────────────────────────────────────────
  if (phase === "result" && result) {
    const skills = Object.entries(result.by_skill)
      .filter(([, st]) => st.total > 0)
      .sort((a, b) => b[1].total - a[1].total);
    return (
      <div className="lp">
        {nav}
        <section className="lvl-test">
          <div className="lp-container">
            <div className="lvl-card">
              <div className="lvl-badge">
                <span className="lvl-badge__label">Твой уровень</span>
                <span className="lvl-badge__cefr">{result.cefr}</span>
                <span className="lvl-badge__score">
                  {result.correct_cnt} из {result.total_cnt} верных
                </span>
              </div>
              <p className="lvl-meaning">{CEFR_MEANING[result.cefr]}</p>

              {locked && !authed && (
                <div className="lvl-locked">
                  <p className="lvl-locked__title">Уровень — это половина ответа</p>
                  <p className="lvl-locked__sub">
                    Дальше интереснее: что именно провисает и что с этим делать.
                    Это открывается после входа — там же результат сохраняется.
                  </p>
                  <ul className="lvl-locked__list">
                    <li className="lvl-locked__item">
                      <span className="lvl-locked__icon" aria-hidden>📊</span>
                      <span><b>Разбор по навыкам.</b> Времена, артикли, предлоги,
                      конструкции, слова — где ты уверен, а где сыплешься.</span>
                    </li>
                    <li className="lvl-locked__item">
                      <span className="lvl-locked__icon" aria-hidden>🧭</span>
                      <span><b>План от ИИ.</b> Что подтянуть в первую очередь,
                      с опорой на твои конкретные ошибки в этом тесте.</span>
                    </li>
                    <li className="lvl-locked__item">
                      <span className="lvl-locked__icon" aria-hidden>🎯</span>
                      <span><b>Занятия под твой уровень.</b> Разговор, подкасты и
                      грамматика сразу настраиваются на {result.cefr}.</span>
                    </li>
                  </ul>
                  <button
                    type="button"
                    className="lp-btn lp-btn--primary lp-btn--lg lvl-locked__cta"
                    onClick={openSignup}
                  >
                    Открыть разбор
                  </button>
                  <p className="lvl-locked__note">
                    Вход через Яндекс ID или email — минута. Первые три дня
                    занятий без ограничений.
                  </p>
                </div>
              )}

              {claimLost && (
                <div className="lvl-locked">
                  <p className="lvl-locked__title">Разбор уже не собрать</p>
                  <p className="lvl-locked__sub">
                    Результат теста хранится час, и этот час истёк. Уровень
                    выше — настоящий, а за разбором пройди тест в приложении:
                    полная версия и точнее, и подробнее.
                  </p>
                  <button
                    type="button"
                    className="lp-btn lp-btn--primary lp-btn--lg lvl-locked__cta"
                    onClick={onOpenApp}
                  >
                    Открыть приложение
                  </button>
                </div>
              )}

              {!locked && !claimLost && (
                <>
                  <div className="lvl-skills">
                    <div className="lvl-skills__title">По навыкам</div>
                    {skills.map(([sk, st]) => {
                      const pct = Math.round((st.correct / st.total) * 100);
                      return (
                        <div key={sk} className="lvl-skill">
                          <span className="lvl-skill__name">{SKILL_LABELS[sk] || sk}</span>
                          <span className="lvl-skill__bar" aria-hidden>
                            <span
                              className="lvl-skill__fill"
                              data-weak={pct < 60 ? "1" : undefined}
                              style={{ width: `${pct}%` }}
                            />
                          </span>
                          <span className="lvl-skill__num">{st.correct}/{st.total}</span>
                        </div>
                      );
                    })}
                  </div>

                  <div className="lvl-report">
                    <div className="lvl-report__title">Что это значит и что делать</div>
                    {report ? (
                      <p className="lvl-report__text">{report}</p>
                    ) : reportFailed ? (
                      <p className="lvl-report__text lvl-report__text--muted">
                        Разбор не загрузился, но уровень засчитан и сохранён.
                      </p>
                    ) : (
                      <div className="lvl-skeleton" aria-label="Готовим разбор">
                        <span /><span /><span />
                      </div>
                    )}
                  </div>

                  <div className="lvl-actions">
                    <button
                      type="button"
                      className="lp-btn lp-btn--primary lp-btn--lg"
                      onClick={onOpenApp}
                    >
                      Начать заниматься
                    </button>
                  </div>
                </>
              )}

              {locked && authed && !claimLost && (
                <div className="lvl-skeleton" aria-label="Открываем разбор">
                  <span /><span /><span />
                </div>
              )}
            </div>

            <Accuracy />
          </div>
        </section>
        <Footer />
      </div>
    );
  }

  // ── Лендинг ───────────────────────────────────────────────────────
  return (
    <div className="lp">
      {nav}

      <section className="lvl-hero">
        <div className="lp-container lvl-hero__inner">
          <span className="lp-eyebrow">Бесплатно, без регистрации</span>
          <h1 className="lp-h1">Узнай свой уровень английского за 3 минуты</h1>
          <p className="lvl-hero__sub">
            Десять заданий на грамматику и слова. Тест адаптивный: ответил
            верно — следующее сложнее, ошибся — проще. Уровень по шкале CEFR
            увидишь сразу на этой же странице.
          </p>
          {error && <p className="lvl-error">{error}</p>}
          <button
            type="button"
            className="lp-btn lp-btn--primary lp-btn--lg"
            onClick={() => void begin()}
            disabled={busy}
          >
            {busy ? "Готовим…" : "Начать тест"}
          </button>
          <p className="lvl-hero__note">
            Ничего вводить не нужно — просто выбирай варианты.
          </p>
          <div className="lvl-hero__facts">
            <span className="lvl-hero__fact">10 заданий</span>
            <span className="lvl-hero__fact">около 3 минут</span>
            <span className="lvl-hero__fact">результат сразу на экране</span>
          </div>
        </div>
      </section>

      <section className="lp-section">
        <div className="lp-container">
          <h2 className="lp-h2 lp-h2--center">Как это устроено</h2>
          <div className="lvl-steps">
            <div className="lvl-step">
              <span className="lvl-step__num">1</span>
              <p className="lvl-step__title">Отвечаешь на 10 заданий</p>
              <p className="lvl-step__body">
                Вопросы подстраиваются под твои ответы, поэтому уровень
                определяется точнее, чем фиксированным списком. После каждого
                ответа сразу видно, где ошибся и почему.
              </p>
            </div>
            <div className="lvl-step">
              <span className="lvl-step__num">2</span>
              <p className="lvl-step__title">Сразу видишь уровень</p>
              <p className="lvl-step__body">
                От A1 до C1 по европейской шкале CEFR — той самой, на которую
                ссылаются курсы, вузы и работодатели. Ждать генерации не
                придётся: результат считается мгновенно.
              </p>
            </div>
            <div className="lvl-step">
              <span className="lvl-step__num">3</span>
              <p className="lvl-step__title">Открываешь разбор</p>
              <p className="lvl-step__body">
                Что провисает по навыкам и что с этим делать — разбор от ИИ.
                Для него нужен аккаунт: результат сохраняется, а занятия
                настраиваются под твой уровень.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="lp-section">
        <div className="lp-container">
          <h2 className="lp-h2 lp-h2--center">Что означают уровни</h2>
          <p className="lp-section__sub">
            Шкала CEFR — общий язык для всех курсов и экзаменов.
          </p>
          <div className="lvl-scale">
            {Object.entries(CEFR_MEANING).map(([code, text]) => (
              <div key={code} className="lvl-scale__row">
                <span className="lvl-scale__code">{code}</span>
                <span className="lvl-scale__text">{text}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="lp-section">
        <div className="lp-container">
          <Accuracy />
        </div>
      </section>

      <section className="lp-section">
        <div className="lp-container">
          <h2 className="lp-h2 lp-h2--center">Частые вопросы</h2>
          <div className="lp-faq__list">
            <details className="lp-faq__item">
              <summary>Правда без регистрации?</summary>
              <div className="lp-faq__answer">
                Чтобы узнать уровень — да, ничего вводить не нужно. Аккаунт
                нужен только для разбора по навыкам, плана от ИИ и сохранения
                результата.
              </div>
            </details>
            <details className="lp-faq__item">
              <summary>Сколько это займёт?</summary>
              <div className="lp-faq__answer">
                Около трёх минут. Десять заданий с вариантами ответа, писать
                ничего не надо.
              </div>
            </details>
            <details className="lp-faq__item">
              <summary>Можно подсматривать в переводчик?</summary>
              <div className="lp-faq__answer">
                Можно, но тогда тест покажет уровень переводчика, а не твой.
                Отвечай как есть — ошибиться тут нормально, на этом и строится
                разбор.
              </div>
            </details>
            <details className="lp-faq__item">
              <summary>Что будет после теста?</summary>
              <div className="lp-faq__answer">
                Уровень увидишь сразу. Дальше можно открыть разбор и заниматься
                в приложении: разговор с ИИ голосом, подкасты, грамматика и
                слова — всё под твой уровень. Первые три дня без ограничений.
              </div>
            </details>
          </div>
        </div>
      </section>

      <section className="lp-section lp-final">
        <div className="lp-container lp-final__inner">
          <h2 className="lp-h2 lp-h2--center">Три минуты — и ты знаешь, где ты</h2>
          <button
            type="button"
            className="lp-btn lp-btn--primary lp-btn--lg"
            onClick={() => void begin()}
            disabled={busy}
          >
            {busy ? "Готовим…" : "Начать тест"}
          </button>
          <p className="lp-final__note">Бесплатно, без регистрации, без звонков.</p>
        </div>
      </section>

      <Footer />
    </div>
  );
}

/* ─── Честно про точность ────────────────────────────────────────────
 * Блок работает дважды: на лендинге снимает недоверие к «тесту из десяти
 * вопросов», а на экране результата объясняет, зачем идти в приложение.
 */
function Accuracy() {
  return (
    <div className="lvl-accuracy">
      <h2 className="lp-h2 lp-h2--center">Насколько это точно</h2>
      <p className="lvl-accuracy__text">
        Десять заданий — экспресс-оценка. Почти всегда она попадает в твой
        уровень или в соседний, и для «понять, где я» этого достаточно.
        Полная версия внутри приложения длиннее: двадцать четыре задания плюс
        два подкаста на понимание на слух. Там оценка точнее, и она же
        настраивает разговор, подкасты и грамматику под тебя.
      </p>
    </div>
  );
}

function Footer() {
  return (
    <footer className="lp-footer">
      <div className="lp-container lp-footer__inner">
        <div className="lp-footer__brand">
          <span className="lp-brand__dot" aria-hidden />
          <span>English Tutor</span>
        </div>
        <div className="lp-footer__links">
          <a href="/">Главная</a>
          <a href="/schools">Для школ английского</a>
          <a href="/oferta.html" target="_blank" rel="noreferrer">Публичная оферта</a>
          <a href="https://t.me/kmo_ai" target="_blank" rel="noreferrer">Канал @kmo_ai</a>
          <a href="https://t.me/kmo_ai_english_bot" target="_blank" rel="noreferrer">Telegram-бот</a>
        </div>
        <div className="lp-footer__copyright">
          © {new Date().getFullYear()} English Tutor
        </div>
      </div>
    </footer>
  );
}
