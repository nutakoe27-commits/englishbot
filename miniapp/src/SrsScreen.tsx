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
 * Leitner-логика на бэке (Leitner box). UI просто шлёт correct: true|false
 * и показывает результат. См. backend/app/srs.py.
 *
 * UI v2: notebook-paper тон, sage layers-плитка, Source Serif counters,
 * lucide-иконки в кнопках.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import WebApp from "@twa-dev/sdk";
import "./App.css";
import { WordsScreen } from "./WordsScreen";
import { NoteCard } from "./ds-react/NoteCard";
import { Button } from "./ds-react/Button";
import { SerifH } from "./ds-react/typography";
import { Icon } from "./ds-react/Icon";
import { useLucide } from "./lucide";

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

export function SrsScreen({ onExit: _onExit }: Props) {
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
  const latestNextDueRef = useRef<string | null>(null);

  useLucide(`${phase}-${idx}-${revealed}`);

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
      } catch { /* silent */ }
    };
    heartbeatRef.current = window.setInterval(tick, HEARTBEAT_MS);
    return () => {
      if (heartbeatRef.current !== null) {
        window.clearInterval(heartbeatRef.current);
        heartbeatRef.current = null;
      }
    };
  }, [phase, initData]);

  const finishSessionSilent = useCallback(async () => {
    if (!sessionIdRef.current) return;
    const elapsedSec = Math.max(0, Math.round((Date.now() - sessionStartRef.current) / 1000));
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
    } catch { /* silent */ }
    finally { sessionIdRef.current = null; }
  }, [initData]);

  const startReview = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setErrorText(null);
    try {
      const r1 = await fetch(`${API_BASE}/api/srs/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init_data: initData }),
      });
      if (!r1.ok) throw new Error(`start HTTP ${r1.status}`);
      const d1 = (await r1.json()) as { session_id: string };
      sessionIdRef.current = d1.session_id;
      sessionStartRef.current = Date.now();

      const r2 = await fetch(
        `${API_BASE}/api/srs/session?init_data=${encodeURIComponent(initData)}&limit=20`,
      );
      if (!r2.ok) throw new Error(`session HTTP ${r2.status}`);
      const d2 = (await r2.json()) as { cards: Card[] };
      const list = Array.isArray(d2.cards) ? d2.cards : [];
      if (list.length === 0) {
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
  }, [busy, initData, loadStats, finishSessionSilent]);

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
        if (correct) setCorrectCount((c) => c + 1);

        const isLast = idx >= cards.length - 1;
        if (isLast) {
          const reviewedTotal = cards.length;
          const correctTotal = correctCount + (correct ? 1 : 0);
          const elapsedSec = Math.max(0, Math.round((Date.now() - sessionStartRef.current) / 1000));
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
          } catch { /* silent */ }
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
      <div className="srs-v2">
        <header className="srs-v2__top">
          <span className="srs-v2__brand">
            <span className="srs-v2__brand-icon"><Icon name="layers" size={18} /></span>
            <SerifH as="h1" size={26}>Слова</SerifH>
          </span>
        </header>
        <NoteCard padding={20} tone="warn">
          <p style={{ margin: 0, fontSize: 14, color: "var(--text)" }}>{errorText ?? "Что-то пошло не так."}</p>
        </NoteCard>
        <Button variant="primary" fullWidth onClick={() => setPhase("home")}>Назад</Button>
      </div>
    );
  }

  if (phase === "summary" && summary) {
    return (
      <div className="srs-v2">
        <header className="srs-v2__top">
          <span className="srs-v2__brand">
            <span className="srs-v2__brand-icon"><Icon name="layers" size={18} /></span>
            <SerifH as="h1" size={26}>Слова</SerifH>
          </span>
        </header>
        <NoteCard padding="22px 20px" tone="sage" style={{ textAlign: "center", display: "flex", flexDirection: "column", gap: 10 }}>
          <SerifH as="h2" size={24}>Готово</SerifH>
          <div className="srs-v2__score">
            {summary.correct} <span className="srs-v2__score-sep">/</span> {summary.reviewed}
          </div>
          <p className="srs-v2__note">Следующий повтор: {formatNextDue(summary.next_due_at)}</p>
        </NoteCard>
        <Button variant="primary" fullWidth icon="arrow-left" onClick={onBackToHome}>
          К списку слов
        </Button>
      </div>
    );
  }

  if (phase === "review") {
    const card = cards[idx];
    if (!card) return null;
    return (
      <div className="srs-v2">
        <header className="srs-v2__top">
          <button
            type="button"
            className="srs-v2__back"
            onClick={async () => {
              await finishSessionSilent();
              await loadStats();
              setPhase("home");
            }}
          >
            <Icon name="arrow-left" size={16} /> <span>Выйти</span>
          </button>
          <span className="srs-v2__progress">{idx + 1} / {cards.length}</span>
        </header>

        <NoteCard padding="32px 24px" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 18, minHeight: 220 }}>
          <div className="srs-v2__flip-word">{card.word}</div>
          {!revealed ? (
            <Button variant="secondary" onClick={() => setRevealed(true)} disabled={busy}>
              Показать перевод
            </Button>
          ) : (
            <div className="srs-v2__flip-translation">
              {card.translation || <em>перевод не задан</em>}
            </div>
          )}
        </NoteCard>

        {revealed && (
          <div className="srs-v2__answer-row">
            <button
              type="button"
              className="srs-v2__ans srs-v2__ans--dont"
              onClick={() => void answer(false)}
              disabled={busy}
            >
              <Icon name="x" size={18} /> <span>Не знаю</span>
            </button>
            <button
              type="button"
              className="srs-v2__ans srs-v2__ans--know"
              onClick={() => void answer(true)}
              disabled={busy}
            >
              <Icon name="check" size={18} /> <span>Знаю</span>
            </button>
          </div>
        )}
      </div>
    );
  }

  // phase === "home"
  const dueCount = stats?.due_count ?? 0;
  const totalCount = stats?.total_count ?? 0;
  const limitCount = stats?.limit ?? 3000;
  return (
    <div className="srs-v2">
      <header className="srs-v2__top">
        <span className="srs-v2__brand">
          <span className="srs-v2__brand-icon"><Icon name="layers" size={18} /></span>
          <SerifH as="h1" size={26}>Слова</SerifH>
        </span>
      </header>

      <NoteCard padding="22px 22px">
        <div className="srs-v2__big">{dueCount}</div>
        <div className="srs-v2__sub">готово к повтору</div>
      </NoteCard>

      <NoteCard padding="22px 22px">
        <div className="srs-v2__big">
          {totalCount} <span className="srs-v2__big-faint">/ {limitCount}</span>
        </div>
        <div className="srs-v2__sub">в словаре</div>
      </NoteCard>

      {dueCount > 0 ? (
        <Button variant="primary" size="lg" fullWidth icon="play" onClick={() => void startReview()} disabled={busy}>
          Начать повторение
        </Button>
      ) : (
        <p className="srs-v2__empty">
          {totalCount === 0
            ? "Словарь пуст. Добавь слова через «Мои слова» или тапни на слово в разговоре/подкасте."
            : "Сегодня всё повторено. Возвращайся завтра 👋"}
        </p>
      )}
      <Button variant="ghost" size="lg" fullWidth icon="book-marked" onClick={() => setPhase("words")}>
        Мои слова
      </Button>
    </div>
  );
}
