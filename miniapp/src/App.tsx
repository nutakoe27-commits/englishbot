import { useEffect, useState } from "react";
import WebApp from "@twa-dev/sdk";

export default function App() {
  const [userName, setUserName] = useState<string>("Пользователь");

  useEffect(() => {
    // Инициализация Telegram Web App SDK
    WebApp.ready();
    WebApp.expand();

    // Получаем имя пользователя из initDataUnsafe
    const user = WebApp.initDataUnsafe?.user;
    if (user?.first_name) {
      setUserName(user.first_name);
    }
  }, []);

  return (
    <div style={styles.container}>
      <h1 style={styles.title}>AI English Tutor</h1>
      <p style={styles.greeting}>Привет, {userName}! 👋</p>
      <p style={styles.subtitle}>
        Твой персональный AI-репетитор разговорного английского
      </p>

      <div style={styles.card}>
        {/* Кнопка разговора — задизейблена до Phase 2 (Gemini Live API) */}
        <button style={styles.talkButton} disabled>
          🎤 Удерживай, чтобы говорить
        </button>
        <p style={styles.comingSoon}>⏳ Голосовой режим появится скоро</p>
      </div>

      <div style={styles.footer}>
        <p style={styles.footerText}>Phase 0 — каркас проекта</p>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: "24px 16px",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    backgroundColor: "var(--tg-theme-bg-color, #ffffff)",
    color: "var(--tg-theme-text-color, #000000)",
    boxSizing: "border-box",
  },
  title: {
    fontSize: "28px",
    fontWeight: 700,
    margin: "0 0 8px",
    textAlign: "center",
  },
  greeting: {
    fontSize: "18px",
    margin: "0 0 4px",
    textAlign: "center",
  },
  subtitle: {
    fontSize: "14px",
    opacity: 0.6,
    textAlign: "center",
    margin: "0 0 40px",
    maxWidth: "280px",
  },
  card: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "12px",
  },
  talkButton: {
    width: "180px",
    height: "180px",
    borderRadius: "50%",
    fontSize: "16px",
    fontWeight: 600,
    border: "none",
    backgroundColor: "var(--tg-theme-button-color, #3390ec)",
    color: "var(--tg-theme-button-text-color, #ffffff)",
    cursor: "not-allowed",
    opacity: 0.5,
    transition: "opacity 0.2s",
  },
  comingSoon: {
    fontSize: "13px",
    opacity: 0.5,
    margin: 0,
  },
  footer: {
    position: "fixed" as const,
    bottom: "16px",
  },
  footerText: {
    fontSize: "11px",
    opacity: 0.3,
    margin: 0,
  },
};
