/**
 * OnboardingModal.tsx — модалка-слайдер с 4 шагами для новых юзеров.
 *
 * Показывается, когда в /api/auth/me пришло `tutorial_done: false`. После
 * прохождения или клика «Пропустить» зовём /api/auth/tutorial/complete —
 * флаг не покажется снова на этом и других устройствах.
 *
 * Может открываться и повторно — через кнопку «Открыть гид» в Аккаунте.
 * В этом случае флаг в БД не меняется.
 *
 * UI v2: warm cream NoteCard, sage icon-плитка вместо эмодзи, Source Serif
 * заголовок.
 */

import { useCallback, useEffect, useState } from "react";
import { completeTutorial } from "./auth";
import { ymReachGoal } from "./metrika";
import { Button } from "./ds-react/Button";
import { IconButton } from "./ds-react/IconButton";
import { Icon } from "./ds-react/Icon";
import { SerifH } from "./ds-react/typography";
import { useLucide } from "./lucide";

interface Props {
  open: boolean;
  markDoneOnFinish: boolean;
  onClose: () => void;
}

interface Step {
  title: string;
  body: string | React.ReactNode;
  icon: string;        // lucide-имя
  tone: "sage" | "speak" | "accent" | "warn";
}

const STEPS: Step[] = [
  {
    icon: "hand",
    tone: "sage",
    title: "Добро пожаловать в English Tutor",
    body: (
      <>
        Я — твой AI-репетитор английского. За 30 секунд покажу, что внутри.
        Можно <b>пролистать стрелками</b> или сразу пропустить.
      </>
    ),
  },
  {
    icon: "mic",
    tone: "speak",
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
    icon: "user-round",
    tone: "accent",
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
    icon: "heart",
    tone: "warn",
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

  useLucide(`${open}-${step}`);

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
    <div className="ob-v2" onClick={() => void finish("skipped")}>
      <div className="ob-v2__card" onClick={(e) => e.stopPropagation()}>
        <div className="ob-v2__top">
          <IconButton icon="x" variant="surface" size="sm" label="Пропустить" onClick={() => void finish("skipped")} />
        </div>

        <div className={`ob-v2__icon ob-v2__icon--${current.tone}`} aria-hidden>
          <Icon name={current.icon} size={28} />
        </div>
        <SerifH as="h2" size={24} className="ob-v2__title">{current.title}</SerifH>
        <div className="ob-v2__body">{current.body}</div>

        <div className="ob-v2__dots" role="tablist" aria-label="Шаги">
          {STEPS.map((_, i) => (
            <button
              key={i}
              type="button"
              role="tab"
              aria-selected={i === step}
              aria-label={`Шаг ${i + 1}`}
              className={`ob-v2__dot ${i === step ? "is-active" : ""}`}
              onClick={() => setStep(i)}
            />
          ))}
        </div>

        <div className="ob-v2__nav">
          <Button
            variant="ghost"
            fullWidth
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
          >
            Назад
          </Button>
          {isLast ? (
            <Button variant="primary" fullWidth onClick={() => void finish("completed")}>
              Понятно, начнём
            </Button>
          ) : (
            <Button variant="primary" fullWidth onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}>
              Дальше
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
