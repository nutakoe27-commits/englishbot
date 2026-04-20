import { useEffect, useState } from "react";

type LockKind = "limit_reached" | "maintenance" | "blocked";

interface Props {
  kind: LockKind;
  message?: string; // для maintenance / blocked
  botUsername?: string; // например "EnglishTutorBot" — без @
  onDismiss?: () => void; // например, кнопка «Закрыть» в режиме админа/dev
}

const TITLES: Record<LockKind, string> = {
  limit_reached: "Дневной лимит исчерпан",
  maintenance: "Технические работы",
  blocked: "Доступ ограничен",
};

const BODIES: Record<LockKind, string> = {
  limit_reached:
    "Сегодня ты позанимался 10 минут на бесплатном тарифе. Возвращайся завтра — лимит сбросится в полночь по МСК.",
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

export function LockScreen({ kind, message, botUsername, onDismiss }: Props) {
  // Простой fade-in при монтировании
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setVisible(true), 10);
    return () => window.clearTimeout(id);
  }, []);

  const handleOpenBot = () => {
    // /subscribe — для limit_reached, /start — для остальных
    const cmd = kind === "limit_reached" ? "subscribe" : "start";
    if (botUsername) {
      // Глубокая ссылка прямо на команду в боте
      const url = `https://t.me/${botUsername}?start=${cmd}`;
      // Telegram Mini App — используем openTelegramLink, чтобы остаться в TG
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
        <h2 className="lock-screen__title">{TITLES[kind]}</h2>
        <p className="lock-screen__body">{message || BODIES[kind]}</p>
        {kind === "limit_reached" && (
          <div className="lock-screen__pricing">
            <div className="lock-screen__price">
              <span className="lock-screen__price-amount">699 ₽</span>
              <span className="lock-screen__price-period">/ месяц</span>
            </div>
            <div className="lock-screen__price">
              <span className="lock-screen__price-amount">4 990 ₽</span>
              <span className="lock-screen__price-period">/ год</span>
            </div>
          </div>
        )}
        <button className="lock-screen__cta" onClick={handleOpenBot}>
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
