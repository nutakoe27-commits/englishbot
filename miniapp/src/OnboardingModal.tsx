/**
 * OnboardingModal.tsx — модалка-слайдер с 4 шагами для новых юзеров.
 *
 * Показывается, когда в /api/auth/me пришло `tutorial_done: false`. После
 * прохождения или клика «Пропустить» зовём /api/auth/tutorial/complete —
 * флаг не покажется снова на этом и других устройствах.
 *
 * Может открываться и повторно — через кнопку «Открыть гид» в Аккаунте.
 * В этом случае флаг в БД не меняется.
 */

import { useCallback, useEffect, useState } from "react";
import { completeTutorial } from "./auth";
import { ymReachGoal } from "./metrika";

interface Props {
  /** true — показать (первый заход). Когда юзер закроет, родитель сам
   *  поставит false. */
  open: boolean;
  /** true — это автоматический показ при первом заходе (помечаем done в БД).
   *  false — юзер открыл сам из меню Аккаунт, флаг в БД не трогаем. */
  markDoneOnFinish: boolean;
  onClose: () => void;
}

interface Step {
  title: string;
  body: string | React.ReactNode;
  emoji: string;
}

const STEPS: Step[] = [
  {
    emoji: "👋",
    title: "Добро пожаловать в English Tutor",
    body: (
      <>
        Я — твой AI-репетитор английского. За 30 секунд покажу, что внутри.
        Можно <b>пролистать стрелками</b> или сразу пропустить.
      </>
    ),
  },
  {
    emoji: "🎙️",
    title: "Четыре режима — один прогресс",
    body: (
      <>
        <b>Разговор</b> — голосом или текстом, как с живым носителем.<br />
        <b>Слушание</b> — подкаст под твою тему и слова.<br />
        <b>Грамматика</b> — 50 уроков от A1 до C1.<br />
        <b>Слова</b> — карточки на повтор (1→3→7→14→30 дней).<br />
        Прогресс и словарь — общие между режимами.
      </>
    ),
  },
  {
    emoji: "👤",
    title: "Профиль и способы входа",
    body: (
      <>
        Иконка <b>👤</b> в правом верхнем углу — твой Аккаунт. Там можно
        привязать второй способ входа (<b>Telegram</b>, <b>Яндекс ID</b>{" "}
        или <b>email с паролем</b>), чтобы не потерять прогресс при смене
        устройства или блокировке одного из сервисов.
      </>
    ),
  },
  {
    emoji: "💛",
    title: "Подписка и бесплатный лимит",
    body: (
      <>
        На бесплатном тарифе — <b>20 минут разговора в день</b>. Подписка
        снимает все ограничения. Тарифы — в Аккаунте → «Оформить подписку»
        (от <b>99 ₽</b> на пробу).<br />
        Нет возможности оплатить? Напиши <b>«прошу доступ»</b> в комментариях
        под любым постом{" "}
        <a href="https://t.me/kmo_ai" target="_blank" rel="noreferrer">@kmo_ai</a>
        {" "}— я выдам подписку бесплатно.
      </>
    ),
  },
];

export function OnboardingModal({ open, markDoneOnFinish, onClose }: Props) {
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (!open) return;
    setStep(0);
    ymReachGoal("onboarding_started", { markDone: markDoneOnFinish });
  }, [open, markDoneOnFinish]);

  const finish = useCallback(async (reason: "completed" | "skipped") => {
    if (markDoneOnFinish) {
      await completeTutorial();
    }
    ymReachGoal(reason === "completed" ? "onboarding_completed" : "onboarding_skipped",
      { step });
    onClose();
  }, [markDoneOnFinish, onClose, step]);

  if (!open) return null;
  const isLast = step === STEPS.length - 1;
  const current = STEPS[step];

  return (
    <div className="ob-backdrop" onClick={() => void finish("skipped")}>
      <div className="ob-card" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          className="ob-skip"
          onClick={() => void finish("skipped")}
          aria-label="Пропустить"
        >
          ✕
        </button>

        <div className="ob-emoji" aria-hidden>{current.emoji}</div>
        <h2 className="ob-title">{current.title}</h2>
        <div className="ob-body">{current.body}</div>

        <div className="ob-dots" role="tablist" aria-label="Шаги">
          {STEPS.map((_, i) => (
            <button
              key={i}
              type="button"
              role="tab"
              aria-selected={i === step}
              aria-label={`Шаг ${i + 1}`}
              className={`ob-dot ${i === step ? "is-active" : ""}`}
              onClick={() => setStep(i)}
            />
          ))}
        </div>

        <div className="ob-nav">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
          >
            Назад
          </button>
          {isLast ? (
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => void finish("completed")}
            >
              Понятно, начнём
            </button>
          ) : (
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}
            >
              Дальше
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
