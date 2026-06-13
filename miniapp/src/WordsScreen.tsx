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
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import WebApp from "@twa-dev/sdk";

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

export function WordsScreen({ apiBase, onClose }: Props) {
  const [words, setWords] = useState<WordItem[]>([]);
  const [limit, setLimit] = useState<number>(3000);
  const [draftWord, setDraftWord] = useState<string>("");
  const [draftTranslation, setDraftTranslation] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const initData = useMemo(() => WebApp.initData || "", []);

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
    } catch (e) {
      setError("Не удалось загрузить словарь. Попробуй позже.");
    } finally {
      setLoading(false);
    }
  }, [apiBase, initData]);

  useEffect(() => {
    void reload();
  }, [reload]);

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
        if (text.includes("limit_reached")) {
          setError(`Достиг лимита ${limit} слов.`);
        } else if (text.includes("too_long")) {
          setError("Слишком длинное слово (макс. 64 символа).");
        } else if (text.includes("empty")) {
          setError("Пустое слово.");
        } else {
          setError("Не получилось добавить. Попробуй ещё раз.");
        }
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
        if (!r.ok) {
          await reload();
          setError("Не удалось удалить. Список обновлён.");
        }
      } catch {
        await reload();
        setError("Ошибка сети.");
      } finally {
        setBusy(false);
      }
    },
    [busy, apiBase, initData, reload],
  );

  const onSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      void addWord();
    },
    [addWord],
  );

  const total = words.length;
  const atLimit = total >= limit;

  return (
    <div className="words-overlay">
      <div className="words-card">
        <header className="words-header">
          <h2 className="words-title">📖 Мои слова</h2>
          <div className="words-counter">
            {total} / {limit}
          </div>
        </header>

        <p className="words-hint">
          Добавь слова, которые сейчас учишь — тьютор будет подкидывать
          их в разговоре, а в режиме «📚 Слова» они станут карточками для повторения.
        </p>

        <form className="words-input-row words-input-row--two" onSubmit={onSubmit}>
          <input
            type="text"
            className="words-input"
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
            className="words-input"
            placeholder="перевод (опционально)"
            value={draftTranslation}
            onChange={(e) => setDraftTranslation(e.target.value)}
            disabled={busy || atLimit}
            maxLength={255}
          />
          <button
            type="submit"
            className="words-add-btn"
            disabled={busy || atLimit || !draftWord.trim()}
          >
            Добавить
          </button>
        </form>

        {error && <div className="words-error">{error}</div>}

        <div className="words-list">
          {loading && <div className="words-empty">Загрузка…</div>}
          {!loading && words.length === 0 && (
            <div className="words-empty">
              Пока пусто. Добавь первое слово сверху.
            </div>
          )}
          {!loading &&
            words.map((w) => (
              <div key={w.word} className="words-chip">
                <div className="words-chip__text">
                  <span className="words-chip__word">{w.word}</span>
                  {w.translation && (
                    <span className="words-chip__translation">— {w.translation}</span>
                  )}
                </div>
                <button
                  className="words-chip__remove"
                  onClick={() => removeWord(w.word)}
                  disabled={busy}
                  aria-label={`Удалить ${w.word}`}
                  title="Удалить"
                >
                  ✕
                </button>
              </div>
            ))}
        </div>

        <button className="words-close" onClick={onClose}>
          Готово
        </button>
      </div>
    </div>
  );
}
