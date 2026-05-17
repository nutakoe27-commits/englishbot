/**
 * WordsScreen.tsx — экран «Мои слова».
 *
 * Юзер добавляет слова, которые сейчас учит. Бэк хранит их в
 * user_vocabulary с source='user'. Они подмешиваются в system_prompt
 * с пометкой «ACTIVELY WANTS to practice» — тьютор будет вкручивать
 * их в разговор. Лимит 100 слов.
 *
 * REST:
 *   GET    /api/user-words?init_data=…           → {words, total, limit}
 *   POST   /api/user-words     body {init_data, word}
 *   DELETE /api/user-words/{word}?init_data=…
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import WebApp from "@twa-dev/sdk";

interface WordItem {
  word: string;
  note: string | null;
  last_seen_at: string | null;
}

interface Props {
  apiBase: string;
  onClose: () => void;
}

export function WordsScreen({ apiBase, onClose }: Props) {
  const [words, setWords] = useState<WordItem[]>([]);
  const [limit, setLimit] = useState<number>(100);
  const [draft, setDraft] = useState<string>("");
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
    const word = draft.trim().toLowerCase();
    if (!word) return;
    if (busy) return;
    if (words.length >= limit) {
      setError(`Достиг лимита ${limit} слов. Удали что-то перед добавлением.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await fetch(`${apiBase}/api/user-words`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init_data: initData, word }),
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
      setDraft("");
      // Оптимистично добавляем в локальный список — потом перетащим из БД.
      void reload();
    } catch {
      setError("Ошибка сети. Попробуй ещё раз.");
    } finally {
      setBusy(false);
    }
  }, [draft, busy, words.length, limit, apiBase, initData, reload]);

  const removeWord = useCallback(
    async (word: string) => {
      if (busy) return;
      setBusy(true);
      setError(null);
      // Оптимистично убираем.
      setWords((prev) => prev.filter((w) => w.word !== word));
      try {
        const r = await fetch(
          `${apiBase}/api/user-words/${encodeURIComponent(word)}?init_data=${encodeURIComponent(initData)}`,
          { method: "DELETE" },
        );
        if (!r.ok) {
          // Если не удалось — перетянуть с сервера.
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
          их в разговоре.
        </p>

        <form className="words-input-row" onSubmit={onSubmit}>
          <input
            type="text"
            className="words-input"
            placeholder="Новое слово…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={busy || atLimit}
            maxLength={64}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
          />
          <button
            type="submit"
            className="words-add-btn"
            disabled={busy || atLimit || !draft.trim()}
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
                <span className="words-chip__word">{w.word}</span>
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
