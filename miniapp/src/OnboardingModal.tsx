/**
 * OnboardingModal.tsx — онбординг новых юзеров и ручной гид.
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
  /** true — это первый заход юзера (а не ручной гид из Аккаунта). */
  markDoneOnFinish: boolean;
  onClose: () => void;
  /** Увести сразу в разговор. Новичку нужно одно действие, а не тур. */
  onStart?: () => void;
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
        Вкладка <b>Профиль</b> в меню внизу экрана — там можно привязать
        второй способ входа (<b>Telegram</b>, <b>Яндекс ID</b> или{" "}
        <b>email с паролем</b>), чтобы не потерять прогресс при смене
        устройства или блокировке одного из сервисов. Там же —
        переключатель темы (светлая / тёмная) и подписка.
      </>
    ),
  },
  {
    icon: "heart",
    tone: "warn",
    title: "Доступ и тарифы",
    body: (
      <>
        Первые <b>3 дня</b> после регистрации всё открыто без ограничений.
        Дальше на бесплатном тарифе остаётся <b>5 минут разговора в день</b>,
        один подкаст и один урок грамматики.<br />
        Подписка снимает лимиты: пробная неделя — <b>199 ₽</b>, месяц —{" "}
        <b>999 ₽</b>. Тарифы во вкладке <b>Профиль</b> → «Оформить подписку».
      </>
    ),
  },
];

export function OnboardingModal({ open, markDoneOnFinish, onClose, onStart }: Props) {
  const [step, setStep] = useState(0);
  // Новичку показываем ОДИН экран с одним действием. Тур из четырёх
  // модалок отодвигал первую фразу по-английски на минуту-полторы и
  // спрашивал про привязку логина у человека, который ещё ничего не
  // получил. Полный тур остался — но только по кнопке «Открыть гид».
  const activation = markDoneOnFinish;

  useLucide(`${open}-${step}-${activation}`);

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

  const startTalking = useCallback(async () => {
    ymReachGoal("onboarding_start_talking");
    await finish("completed");
    onStart?.();
  }, [finish, onStart]);

  if (!open) return null;

  // ── Активационный экран: один шаг, одно действие ──────────────────
  if (activation) {
    return (
      <div className="ob-v2" onClick={() => void finish("skipped")}>
        <div className="ob-v2__card" onClick={(e) => e.stopPropagation()}>
          <div className="ob-v2__top">
            <IconButton
              icon="x" variant="surface" size="sm" label="Закрыть"
              onClick={() => void finish("skipped")}
            />
          </div>

          <div className="ob-v2__icon ob-v2__icon--sage" aria-hidden>
            <Icon name="mic" size={28} />
          </div>
          <SerifH as="h2" size={24} className="ob-v2__title">
            3 дня полного доступа уже открыты
          </SerifH>
          <div className="ob-v2__body">
            Я — твой AI-репетитор. Говори со мной вслух: пойму, отвечу и
            объясню ошибки <b>по-русски</b>.<br /><br />
            Ближайшие 3 дня — <b>без ограничений</b>: разговор, подкасты,
            грамматика, словарь. Настраивать ничего не нужно, просто скажи
            первую фразу — хоть «hello, how are you».
          </div>

          <div className="ob-v2__nav">
            <Button variant="primary" fullWidth onClick={() => void startTalking()}>
              🎤 Сказать первую фразу
            </Button>
          </div>
          <button
            type="button"
            className="ob-v2__secondary"
            onClick={() => void finish("skipped")}
          >
            Осмотреться самому
          </button>
        </div>
      </div>
    );
  }

  // ── Полный гид (Аккаунт → «Открыть гид») ──────────────────────────
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
