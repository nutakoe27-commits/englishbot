import { useEffect, useRef, useState } from "react";

interface TranslatePopoverProps {
  apiBase: string;
  initData: string;
  word: string;
  context: string;
  x: number;
  y: number;
  onClose: () => void;
}

type State =
  | { kind: "loading" }
  | { kind: "loaded"; translations: string[] }
  | { kind: "error"; message: string };

export function TranslatePopover({
  apiBase,
  initData,
  word,
  context,
  x,
  y,
  onClose,
}: TranslatePopoverProps) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const popoverRef = useRef<HTMLDivElement | null>(null);

  // Fetch перевода. AbortController на cleanup, чтобы при быстром переключении
  // на другое слово старый запрос не дописал стейт уже размонтированного компонента.
  useEffect(() => {
    const controller = new AbortController();

    (async () => {
      try {
        const res = await fetch(`${apiBase}/api/translate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ init_data: initData, word, context }),
          signal: controller.signal,
        });
        if (!res.ok) {
          if (res.status === 429) {
            setState({ kind: "error", message: "Слишком часто — попробуй через минуту" });
          } else {
            setState({ kind: "error", message: "Не удалось перевести" });
          }
          return;
        }
        const data = (await res.json()) as { translations?: string[] };
        const translations = data.translations ?? [];
        if (translations.length === 0) {
          setState({ kind: "error", message: "Перевод не найден" });
        } else {
          setState({ kind: "loaded", translations });
        }
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        setState({ kind: "error", message: "Сеть недоступна" });
      }
    })();

    return () => controller.abort();
  }, [apiBase, initData, word, context]);

  // Закрытие по клику вне popover'а.
  useEffect(() => {
    function handler(evt: MouseEvent | TouchEvent) {
      const target = evt.target as Node | null;
      if (popoverRef.current && target && !popoverRef.current.contains(target)) {
        onClose();
      }
    }
    // setTimeout чтобы открывающий клик не закрыл нас сразу.
    const id = window.setTimeout(() => {
      document.addEventListener("mousedown", handler);
      document.addEventListener("touchstart", handler);
    }, 0);
    return () => {
      window.clearTimeout(id);
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("touchstart", handler);
    };
  }, [onClose]);

  // Клампим popover в окно: не даём вылезти за правый край.
  const POPOVER_W = 220;
  const left = Math.max(8, Math.min(x, window.innerWidth - POPOVER_W - 8));
  const top = Math.max(8, y);

  return (
    <div
      ref={popoverRef}
      className="translate-popover"
      style={{ left, top, maxWidth: POPOVER_W }}
      role="dialog"
      aria-label={`Перевод слова ${word}`}
    >
      <div className="translate-popover__word">{word}</div>
      {state.kind === "loading" && (
        <div className="translate-popover__loading">переводим…</div>
      )}
      {state.kind === "error" && (
        <div className="translate-popover__error">{state.message}</div>
      )}
      {state.kind === "loaded" && (
        <>
          <div className="translate-popover__primary">{state.translations[0]}</div>
          {state.translations.length > 1 && (
            <div className="translate-popover__alt">
              {state.translations.slice(1).join(", ")}
            </div>
          )}
        </>
      )}
    </div>
  );
}
