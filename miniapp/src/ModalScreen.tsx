/**
 * ModalScreen — обёртка для полноэкранных оверлеев (ProgressScreen,
 * WordsScreen и т.п.), открываемых ПОВЕРХ Speaking/Listening/Grammar.
 *
 * Рендерится через React Portal в document.body — таким образом гарантированно
 * выходит из любого stacking context / containing block родителя
 * (.tutor-app + .bg-orb с filter могли бы трапить position:fixed внутри).
 */

import { useEffect } from "react";
import { createPortal } from "react-dom";

export function ModalScreen({ children }: { children: React.ReactNode }) {
  // Блокируем фоновый скролл body, пока модалка открыта.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  // Inline-style вместо классов: hardcoded #D4C49F (wheat) + grid SVG.
  // Никаких var() / специфичности / каскада — самая бронебойная защита от
  // того что прозрачность всё ещё «протекает».
  return createPortal(
    <div
      className="modal-screen"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 500,
        backgroundColor: "#D4C49F",
        backgroundImage:
          "linear-gradient(to right, rgba(42,38,34,0.10) 1px, transparent 1px), " +
          "linear-gradient(to bottom, rgba(42,38,34,0.10) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
        overflowY: "auto",
        WebkitOverflowScrolling: "touch",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
      }}
    >
      {children}
    </div>,
    document.body,
  );
}
