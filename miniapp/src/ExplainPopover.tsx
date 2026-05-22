import { useEffect, useRef, useState } from "react";

interface ExplainPopoverProps {
  apiBase: string;
  initData: string;
  original: string;
  corrected: string;
  x: number;
  y: number;
  onClose: () => void;
}

type State =
  | { kind: "loading" }
  | { kind: "loaded"; explanation: string }
  | { kind: "error"; message: string };

export function ExplainPopover({
  apiBase,
  initData,
  original,
  corrected,
  x,
  y,
  onClose,
}: ExplainPopoverProps) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const popoverRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const res = await fetch(`${apiBase}/api/explain-correction`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ init_data: initData, original, corrected }),
          signal: controller.signal,
        });
        if (!res.ok) {
          if (res.status === 429) {
            setState({
              kind: "error",
              message: "Слишком часто — попробуй через минуту",
            });
          } else {
            setState({ kind: "error", message: "Не удалось получить объяснение" });
          }
          return;
        }
        const data = (await res.json()) as { explanation?: string };
        const explanation = (data.explanation || "").trim();
        if (!explanation) {
          setState({ kind: "error", message: "Объяснение недоступно" });
        } else {
          setState({ kind: "loaded", explanation });
        }
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        setState({ kind: "error", message: "Сеть недоступна" });
      }
    })();
    return () => controller.abort();
  }, [apiBase, initData, original, corrected]);

  useEffect(() => {
    function handler(evt: MouseEvent | TouchEvent) {
      const target = evt.target as Node | null;
      if (popoverRef.current && target && !popoverRef.current.contains(target)) {
        onClose();
      }
    }
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

  const POPOVER_W = 280;
  const left = Math.max(8, Math.min(x, window.innerWidth - POPOVER_W - 8));
  const top = Math.max(8, y);

  return (
    <div
      ref={popoverRef}
      className="translate-popover explain-popover"
      style={{ left, top, maxWidth: POPOVER_W }}
      role="dialog"
      aria-label="Объяснение исправления"
    >
      <div className="translate-popover__word">Почему так?</div>
      {state.kind === "loading" && (
        <div className="translate-popover__loading">думаю…</div>
      )}
      {state.kind === "error" && (
        <div className="translate-popover__error">{state.message}</div>
      )}
      {state.kind === "loaded" && (
        <div className="explain-popover__text">{state.explanation}</div>
      )}
    </div>
  );
}
