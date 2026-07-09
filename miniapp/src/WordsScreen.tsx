/**
 * WordsScreen.tsx — экран «Мои слова».
 *
 * Юзер добавляет слова, которые сейчас учит. Бэк хранит их в
 * user_vocabulary с source='user'. Они подмешиваются в system_prompt
 * с пометкой «ACTIVELY WANTS to practice» — тьютор будет вкручивать
 * их в разговор. Эти же слова — карточки для SRS-режима «Слова».
 *
 * REST:
 *   GET    /api/user-words?init_data=…           → {words, total, limit}
 *   POST   /api/user-words     body {init_data, word, translation?}
 *   DELETE /api/user-words/{word}?init_data=…
 *
 * UI v2: warm cream surface, sage book-marked иконка, NoteCard для формы
 * и списка, lucide x для удаления.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import WebApp from "@twa-dev/sdk";
import { NoteCard } from "./ds-react/NoteCard";
import { Button } from "./ds-react/Button";
import { SerifH } from "./ds-react/typography";
import { Icon } from "./ds-react/Icon";
import { useLucide } from "./lucide";
import { playWord } from "./tts";

interface WordItem {
  word: string;
  translation: string | null;
  note: string | null;
  last_seen_at: string | null;
  srs_box?: number;
  srs_due_at?: string | null;
}

interface Props {
  apiBase: string;
  onClose: () => void;
}

// Статичные SVG для строк списка (те же lucide volume-2 / x). НЕ <Icon>:
// каждый <Icon> зовёт глобальный lucide.createIcons(), который сканирует весь
// документ и ПОДМЕНЯЕТ React-овые <i> на <svg> — на сотнях слов это O(n²)
// (зависание WebView) плюс NotFoundError в React-реконсиляции (краш экрана).
// Inline-SVG принадлежат React'у целиком — lucide их не трогает.
const _rowSvg = {
  width: 14, height: 14, viewBox: "0 0 24 24", fill: "none",
  stroke: "currentColor", strokeWidth: 1.75,
  strokeLinecap: "round", strokeLinejoin: "round",
} as const;

function SpeakSvg() {
  return (
    <svg {..._rowSvg} aria-hidden>
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
    </svg>
  );
}

function XSvg() {
  return (
    <svg {..._rowSvg} aria-hidden>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  );
}

export function WordsScreen({ apiBase, onClose }: Props) {
  const [words, setWords] = useState<WordItem[]>([]);
  const [limit, setLimit] = useState<number>(3000);
  const [draftWord, setDraftWord] = useState<string>("");
  const [draftTranslation, setDraftTranslation] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const initData = useMemo(() => WebApp.initData || "", []);

  useLucide(`${loading}-${words.length}-${error ? 1 : 0}`);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(
        `${apiBase}/api/user-words?init_data=${encodeURIComponent(initData)}`,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setWords(Array.isArray(d.words) ? d.words : []);
      if (typeof d.limit === "number") setLimit(d.limit);
    } catch {
      setError("Не удалось загрузить словарь. Попробуй позже.");
    } finally {
      setLoading(false);
    }
  }, [apiBase, initData]);

  useEffect(() => { void reload(); }, [reload]);

  const addWord = useCallback(async () => {
    const word = draftWord.trim().toLowerCase();
    if (!word) return;
    if (busy) return;
    if (words.length >= limit) {
      setError(`Достиг лимита ${limit} слов. Удали что-то перед добавлением.`);
      return;
    }
    const translation = draftTranslation.trim();
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`${apiBase}/api/user-words`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          init_data: initData,
          word,
          translation: translation || undefined,
        }),
      });
      if (!r.ok) {
        const text = await r.text();
        if (text.includes("limit_reached")) setError(`Достиг лимита ${limit} слов.`);
        else if (text.includes("too_long")) setError("Слишком длинное слово (макс. 64 символа).");
        else if (text.includes("empty")) setError("Пустое слово.");
        else setError("Не получилось добавить. Попробуй ещё раз.");
        return;
      }
      setDraftWord("");
      setDraftTranslation("");
      void reload();
    } catch {
      setError("Ошибка сети. Попробуй ещё раз.");
    } finally {
      setBusy(false);
    }
  }, [draftWord, draftTranslation, busy, words.length, limit, apiBase, initData, reload]);

  const removeWord = useCallback(
    async (word: string) => {
      if (busy) return;
      setBusy(true);
      setError(null);
      setWords((prev) => prev.filter((w) => w.word !== word));
      try {
        const r = await fetch(
          `${apiBase}/api/user-words/${encodeURIComponent(word)}?init_data=${encodeURIComponent(initData)}`,
          { method: "DELETE" },
        );
        if (!r.ok) { await reload(); setError("Не удалось удалить. Список обновлён."); }
      } catch {
        await reload();
        setError("Ошибка сети.");
      } finally {
        setBusy(false);
      }
    },
    [busy, apiBase, initData, reload],
  );

  const onSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    void addWord();
  }, [addWord]);

  const total = words.length;
  const atLimit = total >= limit;

  return (
    <div className="wrd-v2">
      <header className="wrd-v2__top">
        <button type="button" className="wrd-v2__back" onClick={onClose}>
          <Icon name="arrow-left" size={16} /> <span>Назад</span>
        </button>
        <span className="wrd-v2__brand">
          <span className="wrd-v2__brand-icon"><Icon name="book-marked" size={18} /></span>
          <SerifH as="h1" size={24}>Мои слова</SerifH>
        </span>
      </header>

      <div className="wrd-v2__counter">
        <span className="wrd-v2__counter-val">{total}</span>
        <span className="wrd-v2__counter-sep">/</span>
        <span className="wrd-v2__counter-lim">{limit}</span>
      </div>

      <p className="wrd-v2__hint">
        Добавь слова, которые сейчас учишь — тьютор будет подкидывать их в
        разговоре, а в режиме «Слова» они станут карточками для повторения.
      </p>

      <NoteCard padding={16}>
        <form className="wrd-v2__form" onSubmit={onSubmit}>
          <input
            type="text"
            className="wrd-v2__input wrd-v2__input--full"
            placeholder="Новое слово…"
            value={draftWord}
            onChange={(e) => setDraftWord(e.target.value)}
            disabled={busy || atLimit}
            maxLength={64}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
          />
          <input
            type="text"
            className="wrd-v2__input wrd-v2__input--full"
            placeholder="перевод (опционально)"
            value={draftTranslation}
            onChange={(e) => setDraftTranslation(e.target.value)}
            disabled={busy || atLimit}
            maxLength={255}
          />
          <Button
            type="submit"
            variant="primary"
            fullWidth
            disabled={busy || atLimit || !draftWord.trim()}
          >
            Добавить
          </Button>
        </form>
      </NoteCard>

      {error && <div className="wrd-v2__error">{error}</div>}

      <div className="wrd-v2__list">
        {loading && <div className="wrd-v2__empty">Загрузка…</div>}
        {!loading && words.length === 0 && (
          <div className="wrd-v2__empty">Пока пусто. Добавь первое слово сверху.</div>
        )}
        {!loading &&
          words.map((w) => (
            <div key={w.word} className="wrd-v2__item">
              <div className="wrd-v2__item-text">
                <span className="wrd-v2__item-word">{w.word}</span>
                {w.translation && (
                  <span className="wrd-v2__item-tr"> — {w.translation}</span>
                )}
              </div>
              <button
                type="button"
                className="wrd-v2__speak"
                onClick={() => playWord(w.word)}
                aria-label={`Послушать ${w.word}`}
                title="Послушать"
              >
                <SpeakSvg />
              </button>
              <button
                type="button"
                className="wrd-v2__rm"
                onClick={() => removeWord(w.word)}
                disabled={busy}
                aria-label={`Удалить ${w.word}`}
                title="Удалить"
              >
                <XSvg />
              </button>
            </div>
          ))}
      </div>

      <Button variant="ghost" fullWidth onClick={onClose}>Готово</Button>
    </div>
  );
}
