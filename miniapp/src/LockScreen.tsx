import { useEffect, useState } from "react";

type LockKind = "limit_reached" | "maintenance" | "blocked";

interface Props {
  kind: LockKind;
  message?: string; // для maintenance / blocked
  /** Приветственные дни полного доступа закончились недавно — показываем
   *  текст про потерю вместо обычного «лимит на сегодня». */
  trialEnded?: boolean;
  botUsername?: string; // например "kmo_ai_english_bot" — без @
  onDismiss?: () => void; // например, кнопка «Закрыть» в режиме админа/dev
  /** Открыть SubscribeScreen внутри приложения (для веб-флоу через ЮKassa).
   *  Если не задан — fallback на открытие бота (Telegram Mini App: оплата
   *  через Telegram Payments + provider_token). */
  onOpenSubscribe?: () => void;
}

const TITLES: Record<LockKind, string> = {
  limit_reached: "Дневной лимит исчерпан",
  maintenance: "Технические работы",
  blocked: "Доступ ограничен",
};

// Тексты пейволла. Раньше первой строкой шло «возвращайся завтра» — мы сами
// предлагали бесплатный путь раньше, чем показывали цену. Теперь фокус на
// том, что теряется, и на том, сколько это стоит на самом деле.
const BODIES: Record<LockKind, string> = {
  limit_reached:
    "Бесплатные 5 минут разговора на сегодня закончились. Это 2,5 часа практики в год — примерно столько же выходит за одну неделю без лимита.",
  maintenance:
    "Бот временно недоступен. Возвращайся через 10–15 минут.",
  blocked:
    "Твой аккаунт заблокирован. Свяжись с поддержкой через бота.",
};

/** Отдельный текст для тех, у кого только что закончились приветственные
 *  дни полного доступа: это не «мне не дают», а «у меня отняли». */
const TRIAL_ENDED_BODY =
  "Твои дни полного доступа закончились. Дальше — 5 минут разговора в сутки: этого хватает, чтобы не забыть язык, но не хватает, чтобы заговорить.";

const CTA_LABEL: Record<LockKind, string> = {
  limit_reached: "Снять лимит",
  maintenance: "Открыть бота",
  blocked: "Открыть бота",
};

export function LockScreen({ kind, message, trialEnded, botUsername, onDismiss, onOpenSubscribe }: Props) {
  // Простой fade-in при монтировании
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setVisible(true), 10);
    return () => window.clearTimeout(id);
  }, []);

  const handleCta = () => {
    // Главная кнопка. Для limit_reached на вебе родитель пробросит
    // onOpenSubscribe — открываем SubscribeScreen (оплата через ЮKassa).
    // В Mini App внутри Telegram callback не пробрасывается — fallback на
    // открытие бота для оплаты через Telegram Payments.
    if (kind === "limit_reached" && onOpenSubscribe) {
      onOpenSubscribe();
      return;
    }
    const cmd = kind === "limit_reached" ? "subscribe" : "start";
    if (botUsername) {
      const url = `https://t.me/${botUsername}?start=${cmd}`;
      const tg = (window as any).Telegram?.WebApp;
      if (tg?.openTelegramLink) {
        tg.openTelegramLink(url);
      } else {
        window.open(url, "_blank");
      }
    }
    // Закрываем mini app — пользователь увидит чат с ботом
    const tg = (window as any).Telegram?.WebApp;
    if (tg?.close) tg.close();
  };

  return (
    <div className={`lock-screen ${visible ? "lock-screen--visible" : ""}`}>
      <div className="lock-screen__card">
        <div className="lock-screen__icon" aria-hidden>
          {kind === "maintenance" ? "🔧" : kind === "blocked" ? "🚫" : "⏳"}
        </div>
        <h2 className="lock-screen__title">
          {kind === "limit_reached" && trialEnded
            ? "Полный доступ закончился"
            : TITLES[kind]}
        </h2>
        <p className="lock-screen__body">
          {message
            || (kind === "limit_reached" && trialEnded
              ? TRIAL_ENDED_BODY
              : BODIES[kind])}
        </p>
        {kind === "limit_reached" && (
          <div className="lock-screen__pricing">
            <div className="lock-screen__price">
              <span className="lock-screen__price-amount">199 ₽</span>
              <span className="lock-screen__price-period">/ пробная неделя</span>
            </div>
            <div className="lock-screen__price">
              <span className="lock-screen__price-amount">999 ₽</span>
              <span className="lock-screen__price-period">/ месяц</span>
            </div>
            <div className="lock-screen__price">
              <span className="lock-screen__price-amount">5 999 ₽</span>
              <span className="lock-screen__price-period">/ год</span>
            </div>
          </div>
        )}
        <button className="lock-screen__cta" onClick={handleCta}>
          {CTA_LABEL[kind]}
        </button>
        {onDismiss && (
          <button className="lock-screen__dismiss" onClick={onDismiss}>
            Закрыть
          </button>
        )}
      </div>
    </div>
  );
}
