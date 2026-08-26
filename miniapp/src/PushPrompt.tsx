/**
 * PushPrompt — мягкий вопрос про уведомления.
 *
 * Показывается в трёх местах: в итоге урока, в конце онбординга и на экране
 * результата теста уровня. Компонент один намеренно — вопрос должен звучать
 * одинаково, а правило «один раз на устройство» работать поперёк всех
 * экранов: человек, отказавшийся в онбординге, не должен встретить тот же
 * вопрос после первого занятия.
 *
 * Системный диалог браузера вызывается ТОЛЬКО по кнопке «Да». Отказ в нём
 * необратим — включить уведомления обратно можно лишь вручную в настройках
 * сайта, куда никто не пойдёт. Поэтому сперва свой вопрос, и только по
 * согласию — системный.
 *
 * Если браузер уведомления не умеет, решение уже принято или вопрос уже
 * задавали — компонент не рендерит ничего.
 */

import { useState } from "react";
import { enablePush, markPushAsked, pushAsked, pushState } from "./push";
import { ymReachGoal } from "./metrika";

interface Props {
  /** Откуда спрашиваем — уходит в Метрику, чтобы сравнить места. */
  place: "session" | "onboarding" | "level";
  /** Текст под заголовком. У каждого места свой повод. */
  text: string;
}

export function PushPrompt({ place, text }: Props) {
  const [show, setShow] = useState<boolean>(
    () => pushState() === "default" && !pushAsked(),
  );
  const [busy, setBusy] = useState<boolean>(false);

  if (!show) return null;

  return (
    <div className="dsx-pushask">
      <div className="dsx-pushask__text">{text}</div>
      <div className="dsx-pushask__row">
        <button
          type="button"
          className="dsx-pushask__yes"
          disabled={busy}
          onClick={() => void (async () => {
            setBusy(true);
            ymReachGoal("push_ask_accept", { place });
            const next = await enablePush();
            ymReachGoal(
              next === "granted" ? "push_granted" : "push_denied", { place },
            );
            setShow(false);
          })()}
        >
          {busy ? "Секунду…" : "Да, напоминать"}
        </button>
        <button
          type="button"
          className="dsx-pushask__no"
          onClick={() => {
            // Отметка общая: спросили — больше не спрашиваем нигде.
            markPushAsked();
            ymReachGoal("push_ask_decline", { place });
            setShow(false);
          }}
        >
          Не надо
        </button>
      </div>
    </div>
  );
}
