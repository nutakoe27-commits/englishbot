/**
 * ModalScreen — тот же визуальный паттерн что у SubscribeScreen
 * (.sub-screen backdrop + .sub-screen__inner solid card). Используется
 * для ProgressScreen / WordsScreen когда они открываются ПОВЕРХ
 * Speaking/Listening/Grammar.
 */

import { useEffect } from "react";
import { createPortal } from "react-dom";

export function ModalScreen({
  children,
  fullscreen = false,
}: {
  children: React.ReactNode;
  /** true — popover на весь viewport без card-ограничений (max-width / border / radius). */
  fullscreen?: boolean;
}) {
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  return createPortal(
    <div className={`sub-screen ${fullscreen ? "sub-screen--fullscreen" : ""}`}>
      <div className="sub-screen__inner" style={{ padding: 0 }}>{children}</div>
    </div>,
    document.body,
  );
}
