export default function App() {
  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <h1 style={styles.title}>🛠 Admin Panel</h1>
        <p style={styles.subtitle}>AI English Tutor</p>
        <div style={styles.badge}>Coming soon</div>
        <p style={styles.description}>
          Административная панель для управления пользователями, статистикой и
          настройками AI-репетитора. Появится в Phase 1.
        </p>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#f5f5f5",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    padding: "24px",
    boxSizing: "border-box",
  },
  card: {
    backgroundColor: "#ffffff",
    borderRadius: "12px",
    padding: "40px 48px",
    textAlign: "center",
    boxShadow: "0 2px 16px rgba(0,0,0,0.08)",
    maxWidth: "400px",
    width: "100%",
  },
  title: {
    fontSize: "32px",
    margin: "0 0 8px",
    color: "#1a1a1a",
  },
  subtitle: {
    fontSize: "16px",
    color: "#666",
    margin: "0 0 24px",
  },
  badge: {
    display: "inline-block",
    backgroundColor: "#fff3cd",
    color: "#856404",
    padding: "4px 12px",
    borderRadius: "20px",
    fontSize: "13px",
    fontWeight: 600,
    marginBottom: "20px",
  },
  description: {
    fontSize: "14px",
    color: "#888",
    lineHeight: 1.6,
    margin: 0,
  },
};
