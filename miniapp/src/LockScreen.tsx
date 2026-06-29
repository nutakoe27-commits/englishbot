import { useEffect, useState } from "react";

type LockKind = "limit_reached" | "maintenance" | "blocked";

interface Props {
  kind: LockKind;
  message?: string; // для maintenance / blocked
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

const BODIES: Record<LockKind, string> = {
  limit_reached:
    "Сегодня ты использовал бесплатные 20 минут разговора. Возвращайся завтра — лимит сбросится в полночь по МСК.",
  maintenance:
    "Бот временно недоступен. Возвращайся через 10–15 минут.",
  blocked:
    "Твой аккаунт заблокирован. Свяжись с поддержкой через бота.",
};

const CTA_LABEL: Record<LockKind, string> = {
  limit_reached: "Перейти к подписке",
  maintenance: "Открыть бота",
  blocked: "Открыть бота",
};

export function LockScreen({ kind, message, botUsername, onDismiss, onOpenSubscribe }: Props) {
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

  const handleOpenChannel = () => {
    // Личка автора — там можно попросить доступ, если нет возможности оплатить.
    const url = "https://t.me/NuTak0e";
    const tg = (window as any).Telegram?.WebApp;
    if (tg?.openTelegramLink) {
      tg.openTelegramLink(url);
    } else {
      window.open(url, "_blank");
    }
  };

  return (
    <div className={`lock-screen ${visible ? "lock-screen--visible" : ""}`}>
      <div className="lock-screen__card">
        <div className="lock-screen__icon" aria-hidden>
          {kind === "maintenance" ? "🔧" : kind === "blocked" ? "🚫" : "⏳"}
        </div>
        <h2 className="lock-screen__title">{TITLES[kind]}</h2>
        <p className="lock-screen__body">{message || BODIES[kind]}</p>
        {kind === "limit_reached" && (
          <div className="lock-screen__pricing">
            <div className="lock-screen__price">
              <span className="lock-screen__price-amount">99 ₽</span>
              <span className="lock-screen__price-period">/ 3 дня</span>
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
        {kind === "limit_reached" && (
          <p className="lock-screen__charity">
            Нет возможности оплатить? Это не повод бросать английский — напиши
            мне лично:{" "}
            <button
              type="button"
              className="lock-screen__charity-link"
              onClick={handleOpenChannel}
            >
              @NuTak0e
            </button>
            {" "}— разберёмся и найдём вариант. 💛
          </p>
        )}
        {onDismiss && (
          <button className="lock-screen__dismiss" onClick={onDismiss}>
            Закрыть
          </button>
        )}
      </div>
    </div>
  );
}
