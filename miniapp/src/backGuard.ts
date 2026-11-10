/**
 * backGuard.ts — аппаратная кнопка «Назад» на Android.
 *
 * Внутри TWA «Назад» листает историю браузера. У одностраничного
 * приложения её почти нет: человек открывает разговор, жмёт «Назад» — и
 * вместо возврата на главный экран приложение закрывается. Для нативного
 * приложения это выглядит поломкой, и на модерации такое замечают.
 *
 * Приём стандартный: при открытии экрана кладём в историю пустую запись,
 * а «Назад» перехватываем и закрываем экран сами. При закрытии изнутри
 * (кнопкой в интерфейсе) свою запись убираем, иначе следующее нажатие
 * «Назад» уйдёт впустую.
 *
 * Работает только в установленном приложении. На сайте перехватывать
 * «Назад» нельзя: там это навигация браузера, и человек ждёт от неё
 * привычного поведения.
 */

import { useEffect, useRef } from "react";
import { isAppMode } from "./appMode";

interface Guard { id: string; onBack: () => void; pushed: boolean }
const guards: Guard[] = [];
const retired = new Set<string>();
let sequence = 0;
let pendingBack = false;
let listening = false;

function currentMarker(): string | undefined {
  return (window.history.state as { etGuard?: string } | null)?.etGuard;
}

function flushHistory(): void {
  if (pendingBack) return;
  const marker = currentMarker();
  if (marker && retired.has(marker)) {
    pendingBack = true;
    window.history.back();
    return;
  }
  // A close followed by opening another modal must finish its asynchronous
  // history.back() before the replacement guard pushes its own entry.
  for (const guard of guards) {
    if (!guard.pushed) {
      window.history.pushState({ etGuard: guard.id }, "");
      guard.pushed = true;
    }
  }
}

function onPop(): void {
  if (pendingBack) {
    pendingBack = false;
    flushHistory();
    return;
  }
  const top = guards[guards.length - 1];
  if (top?.pushed && currentMarker() !== top.id) {
    guards.pop();
    retired.add(top.id);
    top.onBack();
  }
  flushHistory();
}

export function useBackGuard(active: boolean, onBack: () => void): void {
  const callback = useRef(onBack);
  callback.current = onBack;
  useEffect(() => {
    if (!active || !isAppMode() || typeof window === "undefined") return;
    if (!listening) { window.addEventListener("popstate", onPop); listening = true; }
    const guard: Guard = { id: `${Date.now()}-${++sequence}`, onBack: () => callback.current(), pushed: false };
    guards.push(guard);
    flushHistory();

    return () => {
      const index = guards.indexOf(guard);
      if (index < 0) return; // Already consumed by the hardware Back button.
      guards.splice(index, 1);
      if (guard.pushed) retired.add(guard.id);
      flushHistory();
    };
  }, [active]);
}
