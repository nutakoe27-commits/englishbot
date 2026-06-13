/**
 * SrsScreen.tsx — режим «📚 Слова»: интервальное повторение карточек.
 *
 * Фазы:
 *   home   — стартовый экран: бейджи «готово к повтору / в словаре», кнопки
 *            «Начать повторение» и «Мои слова».
 *   review — карточка: лицо (EN) → кнопка «Показать перевод» → оборот
 *            (EN + RU) → «Не знаю» / «Знаю» → следующая карточка.
 *   summary— итог: N карточек, M правильных, «Следующий повтор: <дата>».
 *   words  — оверлей-список «Мои слова» (переиспользуем WordsScreen).
 *   error  — фейл сети/сервера + retry.
 *
 * Леrner-логика на бэке (Leitner box). UI просто шлёт correct: true|false
 * и показывает результат. См. backend/app/srs.py.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import WebApp from "@twa-dev/sdk";
import "./App.css";
import { WordsScreen } from "./WordsScreen";

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  "https://api-english.krichigindocs.ru";

const HEARTBEAT_MS = 20_000;

interface Props {
  onExit: () => void;
}

interface Card {
  word: string;
  translation: string | null;
  box: number;
}

interface Stats {
  due_count: number;
  total_count: number;
  limit: number;
}

interface ReviewResult {
  ok: boolean;
  new_box: number;
  next_due_at: string | null;
}

type Phase = "home" | "review" | "summary" | "words" | "error";

interface Summary {
  reviewed: number;
  correct: number;
  next_due_at: string | null;
}

function formatNextDue(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("ru-RU", {
      day: "numeric",
      month: "long",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function SrsScreen({ onExit }: Props) {
  const initData = useMemo(() => WebApp.initData || "", []);

  const [phase, setPhase] = useState<Phase>("home");
  const [stats, setStats] = useState<Stats | null>(null);
  const [cards, setCards] = useState<Card[]>([]);
  const [idx, setIdx] = useState<number>(0);
  const [revealed, setRevealed] = useState<boolean>(false);
  const [correctCount, setCorrectCount] = useState<number>(0);
  const [busy, setBusy] = useState<boolean>(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);

  const sessionIdRef = useRef<string | null>(null);
  const sessionStartRef = useRef<number>(0);
  const heartbeatRef = useRef<number | null>(null);
  // Самая дальняя next_due, чтобы показать в summary («следующий повтор не
  // раньше чем …»). Берём max — то есть карточки в высоких боксах сдвигают её.
  const latestNextDueRef = useRef<string | null>(null);

  // ── Загрузка stats при входе на home ─────────────────────────────────
  const loadStats = useCallback(async () => {
    try {
      const r = await fetch(
        `${API_BASE}/api/srs/stats?init_data=${encodeURIComponent(initData)}`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = (await r.json()) as Stats;
      setStats(d);
    } catch {
      setErrorText("Не удалось загрузить статистику. Попробуй позже.");
      setPhase("error");
    }
  }, [initData]);

  useEffect(() => {
    if (phase === "home") void loadStats();
  }, [phase, loadStats]);

  // ── Heartbeat пока в review ──────────────────────────────────────────
  useEffect(() => {
    if (phase !== "review") return;
    if (!sessionIdRef.current) return;
    const tick = async () => {
      try {
        await fetch(`${API_BASE}/api/srs/heartbeat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            init_data: initData,
            session_id: sessionIdRef.current,
          }),
        });
      } catch {
        /* silent */
      }
    };
    heartbeatRef.current = window.setInterval(tick, HEARTBEAT_MS);
    return () => {
      if (heartbeatRef.current !== null) {
        window.clearInterval(heartbeatRef.current);
        heartbeatRef.current = null;
      }
    };
  }, [phase, initData]);

  // ── Старт review-сессии ──────────────────────────────────────────────
  const startReview = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setErrorText(null);
    try {
      // 1. Открываем сессию (для presence + DailyUsage).
      const r1 = await fetch(`${API_BASE}/api/srs/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init_data: initData }),
      });
      if (!r1.ok) throw new Error(`start HTTP ${r1.status}`);
      const d1 = (await r1.json()) as { session_id: string };
      sessionIdRef.current = d1.session_id;
      sessionStartRef.current = Date.now();

      // 2. Берём карточки.
      const r2 = await fetch(
        `${API_BASE}/api/srs/session?init_data=${encodeURIComponent(initData)}&limit=20`,
      );
      if (!r2.ok) throw new Error(`session HTTP ${r2.status}`);
      const d2 = (await r2.json()) as { cards: Card[] };
      const list = Array.isArray(d2.cards) ? d2.cards : [];
      if (list.length === 0) {
        // На бэке решили что due_count > 0, но между stats и session кто-то
        // успел всё повторить (или race). Просто покажем home.
        await finishSessionSilent();
        await loadStats();
        setPhase("home");
        return;
      }
      setCards(list);
      setIdx(0);
      setRevealed(false);
      setCorrectCount(0);
      latestNextDueRef.current = null;
      setPhase("review");
    } catch {
      setErrorText("Не удалось начать повторение. Проверь сеть.");
      setPhase("error");
    } finally {
      setBusy(false);
    }
  }, [busy, initData, loadStats]);

  // ── Тихо закрыть сессию (на случай race / ручного выхода) ────────────
  const finishSessionSilent = useCallback(async () => {
    if (!sessionIdRef.current) return;
    const elapsedSec = Math.max(
      0,
      Math.round((Date.now() - sessionStartRef.current) / 1000),
    );
    try {
      await fetch(`${API_BASE}/api/srs/session/finish`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          init_data: initData,
          session_id: sessionIdRef.current,
          reviewed: 0,
          correct: 0,
          duration_sec: elapsedSec,
        }),
      });
    } catch {
      /* silent */
    } finally {
      sessionIdRef.current = null;
    }
  }, [initData]);

  // ── Ответ на карточку ────────────────────────────────────────────────
  const answer = useCallback(
    async (correct: boolean) => {
      if (busy) return;
      const card = cards[idx];
      if (!card) return;
      setBusy(true);
      try {
        const r = await fetch(`${API_BASE}/api/srs/review`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            init_data: initData,
            word: card.word,
            correct,
          }),
        });
        if (r.ok) {
          const d = (await r.json()) as ReviewResult;
          if (d.next_due_at) {
            const prev = latestNextDueRef.current;
            if (!prev || new Date(d.next_due_at).getTime() > new Date(prev).getTime()) {
              latestNextDueRef.current = d.next_due_at;
            }
          }
        }
        // Не блокируем UX на сетевую ошибку — просто продолжаем (хуже того,
        // юзер увидит ту же карточку завтра, если ответ не записался).
        if (correct) setCorrectCount((c) => c + 1);

        const isLast = idx >= cards.length - 1;
        if (isLast) {
          // Финализируем сессию.
          const reviewedTotal = cards.length;
          const correctTotal = correctCount + (correct ? 1 : 0);
          const elapsedSec = Math.max(
            0,
            Math.round((Date.now() - sessionStartRef.current) / 1000),
          );
          try {
            await fetch(`${API_BASE}/api/srs/session/finish`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                init_data: initData,
                session_id: sessionIdRef.current,
                reviewed: reviewedTotal,
                correct: correctTotal,
                duration_sec: elapsedSec,
              }),
            });
          } catch {
            /* silent */
          }
          sessionIdRef.current = null;
          setSummary({
            reviewed: reviewedTotal,
            correct: correctTotal,
            next_due_at: latestNextDueRef.current,
          });
          setPhase("summary");
        } else {
          setIdx((i) => i + 1);
          setRevealed(false);
        }
      } finally {
        setBusy(false);
      }
    },
    [busy, cards, idx, correctCount, initData],
  );

  // ── Кнопки навигации ─────────────────────────────────────────────────
  const onBackToHome = useCallback(async () => {
    setSummary(null);
    setCards([]);
    setIdx(0);
    setRevealed(false);
    setCorrectCount(0);
    await loadStats();
    setPhase("home");
  }, [loadStats]);

  // ── Render ───────────────────────────────────────────────────────────

  if (phase === "words") {
    return (
      <WordsScreen
        apiBase={API_BASE}
        onClose={async () => {
          await loadStats();
          setPhase("home");
        }}
      />
    );
  }

  if (phase === "error") {
    return (
      <div className="srs-screen">
        <div className="srs-card">
          <p className="srs-error">{errorText ?? "Что-то пошло не так."}</p>
          <button className="srs-btn srs-btn--primary" onClick={() => setPhase("home")}>
            Назад
          </button>
        </div>
      </div>
    );
  }

  if (phase === "summary" && summary) {
    return (
      <div className="srs-screen">
        <div className="srs-card srs-summary">
          <h2 className="srs-summary__title">Готово</h2>
          <div className="srs-summary__score">
            {summary.correct} / {summary.reviewed}
          </div>
          <p className="srs-summary__note">
            Следующий повтор: {formatNextDue(summary.next_due_at)}
          </p>
          <div className="srs-summary__actions">
            <button className="srs-btn srs-btn--primary" onClick={onBackToHome}>
              К списку слов
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (phase === "review") {
    const card = cards[idx];
    if (!card) return null;
    const progress = `${idx + 1} / ${cards.length}`;
    return (
      <div className="srs-screen">
        <div className="srs-review-top">
          <button
            type="button"
            className="srs-back"
            onClick={async () => {
              await finishSessionSilent();
              await loadStats();
              setPhase("home");
            }}
          >
            ← Выйти
          </button>
          <span className="srs-progress">{progress}</span>
        </div>
        <div className="srs-card srs-flip">
          <div className="srs-flip__word">{card.word}</div>
          {!revealed && (
            <button
              className="srs-btn srs-btn--reveal"
              onClick={() => setRevealed(true)}
              disabled={busy}
            >
              Показать перевод
            </button>
          )}
          {revealed && (
            <>
              <div className="srs-flip__translation">
                {card.translation || <em>перевод не задан</em>}
              </div>
              <div className="srs-actions">
                <button
                  className="srs-btn srs-btn--dont"
                  onClick={() => void answer(false)}
                  disabled={busy}
                >
                  Не знаю
                </button>
                <button
                  className="srs-btn srs-btn--know"
                  onClick={() => void answer(true)}
                  disabled={busy}
                >
                  Знаю
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    );
  }

  // phase === "home"
  const dueCount = stats?.due_count ?? 0;
  const totalCount = stats?.total_count ?? 0;
  const limitCount = stats?.limit ?? 3000;
  return (
    <div className="srs-screen">
      <div className="srs-home-top">
        <button type="button" className="srs-back" onClick={onExit}>
          ← Назад
        </button>
        <h1 className="srs-home__title">📚 Слова</h1>
      </div>
      <div className="srs-card srs-home">
        <div className="srs-stat">
          <div className="srs-stat__num">{dueCount}</div>
          <div className="srs-stat__label">готово к повтору</div>
        </div>
        <div className="srs-stat srs-stat--muted">
          <div className="srs-stat__num">
            {totalCount} <span className="srs-stat__limit">/ {limitCount}</span>
          </div>
          <div className="srs-stat__label">в словаре</div>
        </div>
        {dueCount > 0 ? (
          <button
            className="srs-btn srs-btn--primary"
            onClick={() => void startReview()}
            disabled={busy}
          >
            Начать повторение
          </button>
        ) : (
          <p className="srs-empty">
            {totalCount === 0
              ? "Словарь пуст. Добавь слова через «Мои слова» или тапни на слово в разговоре/подкасте."
              : "Сегодня всё повторено. Возвращайся завтра 👋"}
          </p>
        )}
        <button
          className="srs-btn srs-btn--secondary"
          onClick={() => setPhase("words")}
        >
          Мои слова
        </button>
      </div>
    </div>
  );
}
