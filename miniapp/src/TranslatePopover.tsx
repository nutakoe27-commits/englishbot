import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface TranslatePopoverProps {
  apiBase: string;
  initData: string;
  word: string;
  context: string;
  /** Левый край слова. */
  x: number;
  /** Низ слова — попап по умолчанию раскрывается отсюда вниз. */
  y: number;
  /** Верх слова. Нужен, чтобы у нижних строк раскрыться ВВЕРХ. */
  anchorTop?: number;
  onClose: () => void;
}

type State =
  | { kind: "loading" }
  | { kind: "loaded"; translations: string[] }
  | { kind: "error"; message: string };

type AddState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "ok" }
  | { kind: "duplicate" }
  | { kind: "limit" }
  | { kind: "error" };

export function TranslatePopover({
  apiBase,
  initData,
  word,
  context,
  x,
  y,
  anchorTop,
  onClose,
}: TranslatePopoverProps) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [addState, setAddState] = useState<AddState>({ kind: "idle" });
  const popoverRef = useRef<HTMLDivElement | null>(null);

  // Сохранить слово в личный словарь юзера (как «+ В словарь» в SRS-флоу).
  // Перевод берём из первого варианта popover'а.
  const handleAddToVocab = async () => {
    if (state.kind !== "loaded") return;
    if (addState.kind === "saving" || addState.kind === "ok") return;
    setAddState({ kind: "saving" });
    try {
      const res = await fetch(`${apiBase}/api/user-words`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          init_data: initData,
          word,
          translation: state.translations[0] ?? null,
        }),
      });
      if (res.ok) {
        const data = (await res.json().catch(() => ({}))) as { duplicate?: boolean };
        setAddState({ kind: data.duplicate ? "duplicate" : "ok" });
        // Закрываем попап через 1.2с, чтобы юзер увидел подтверждение.
        window.setTimeout(onClose, 1200);
      } else {
        const text = await res.text();
        if (text.includes("limit_reached")) {
          setAddState({ kind: "limit" });
        } else {
          setAddState({ kind: "error" });
        }
      }
    } catch {
      setAddState({ kind: "error" });
    }
  };

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

  // Позиция: по горизонтали клампим в окно, по вертикали — раскрываемся вниз,
  // а если снизу не помещаемся (последние строки транскрипта, над нижней
  // навигацией) — переворачиваемся вверх. Без этого кнопка «+ В словарь»
  // уезжала за экран и слово нельзя было добавить.
  const POPOVER_W = 220;
  const GAP = 6;
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    const el = popoverRef.current;
    if (!el) return;
    const h = el.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    // Нижняя навигация перекрывает попап — резервируем её высоту.
    const nav = document.querySelector(".bnav");
    const bottomReserve = (nav ? nav.getBoundingClientRect().height : 0) + 8;

    const left = Math.max(8, Math.min(x, vw - POPOVER_W - 8));
    const spaceBelow = vh - bottomReserve - y;
    let top: number;
    if (spaceBelow >= h) {
      top = y;                                   // помещается снизу
    } else if (anchorTop !== undefined && anchorTop - h - GAP >= 8) {
      top = anchorTop - h - GAP;                 // переворачиваемся вверх
    } else {
      // Не помещается ни там, ни там — прижимаем к низу видимой области.
      top = Math.max(8, vh - bottomReserve - h);
    }
    setPos({ left, top });
    // Пересчитываем и когда пришёл перевод: высота попапа меняется.
  }, [x, y, anchorTop, state.kind]);

  return createPortal(
    <div
      ref={popoverRef}
      className="translate-popover"
      style={{
        left: pos?.left ?? x,
        top: pos?.top ?? y,
        maxWidth: POPOVER_W,
        // До замера прячем, чтобы не мигнуть в неправильной позиции.
        visibility: pos ? "visible" : "hidden",
      }}
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
          <button
            type="button"
            className="translate-popover__add"
            onClick={handleAddToVocab}
            disabled={addState.kind === "saving" || addState.kind === "ok"}
          >
            {addState.kind === "idle" && "+ В словарь"}
            {addState.kind === "saving" && "Сохраняю…"}
            {addState.kind === "ok" && "Добавлено ✓"}
            {addState.kind === "duplicate" && "Уже в словаре"}
            {addState.kind === "limit" && "Лимит словаря"}
            {addState.kind === "error" && "Ошибка — повторить"}
          </button>
        </>
      )}
    </div>,
    document.body,
  );
}
