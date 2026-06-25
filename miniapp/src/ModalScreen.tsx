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

  return createPortal(
    <div className="modal-screen">{children}</div>,
    document.body,
  );
}
