import { requestSubscription } from "./SubscriptionOffer";
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
  limit_reached: "Откройте полный доступ",
  maintenance: "Технические работы",
  blocked: "Доступ ограничен",
};

// Тексты пейволла. Раньше первой строкой шло «возвращайся завтра» — мы сами
// предлагали бесплатный путь раньше, чем показывали цену. Теперь фокус на
// том, что теряется, и на том, сколько это стоит на самом деле.
const BODIES: Record<LockKind, string> = {
  limit_reached:
    "Для уроков и самостоятельных тренажёров нужна подписка. При первом подключении — 7 дней за 1 ₽.",
  maintenance:
    "Бот временно недоступен. Возвращайся через 10–15 минут.",
  blocked:
    "Твой аккаунт заблокирован. Свяжись с поддержкой через бота.",
};

/** Отдельный текст для тех, у кого только что закончились приветственные
 *  дни полного доступа: это не «мне не дают», а «у меня отняли». */
const CTA_LABEL: Record<LockKind, string> = {
  limit_reached: "Открыть подписку",
  maintenance: "Открыть бота",
  blocked: "Открыть бота",
};

export function LockScreen({ kind, message, botUsername, onDismiss }: Props) {
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
    if (kind === "limit_reached") {
      requestSubscription();
      return;
    }
    const cmd = "start";
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
          {TITLES[kind]}
        </h2>
        <p className="lock-screen__body">{message || BODIES[kind]}</p>
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
