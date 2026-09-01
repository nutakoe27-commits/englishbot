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

import { useEffect } from "react";
import { isAppMode } from "./appMode";

interface GuardState {
  etGuard: number;
}

export function useBackGuard(active: boolean, onBack: () => void): void {
  useEffect(() => {
    if (!active || !isAppMode() || typeof window === "undefined") return;

    const marker: GuardState = { etGuard: Date.now() + Math.random() };
    window.history.pushState(marker, "");

    const onPop = () => { onBack(); };
    window.addEventListener("popstate", onPop);

    return () => {
      window.removeEventListener("popstate", onPop);
      // Экран закрыли не «Назад», а изнутри — снимаем свою запись, чтобы
      // история не копила пустышки. Слушателя уже нет, так что этот
      // history.back() ничего не запустит по второму кругу.
      const cur = window.history.state as GuardState | null;
      if (cur && cur.etGuard === marker.etGuard) window.history.back();
    };
  }, [active, onBack]);
}
