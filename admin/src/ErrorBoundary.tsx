/**
 * ErrorBoundary — без него любая runtime-ошибка в React 18 размонтирует всё
 * дерево, и админка превращается в белый экран без единой подсказки.
 * Показываем текст ошибки и стек прямо на странице.
 */

import React from "react";

interface Props { children: React.ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("[admin crashed]", error, info);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div style={{
        maxWidth: 820, margin: "40px auto", padding: 24,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        color: "#1a1a1a",
      }}>
        <h1 style={{ fontSize: 20, color: "#b3261e", marginBottom: 8 }}>
          Админка упала
        </h1>
        <p style={{ fontSize: 14, color: "#555", lineHeight: 1.6 }}>
          Ошибка ниже. Частая причина — админка собрана без{" "}
          <code>VITE_API_BASE</code> и запросы уходят на её собственный домен
          вместо backend.
        </p>
        <pre style={{
          background: "#f6f6f6", border: "1px solid #ddd", borderRadius: 8,
          padding: 14, fontSize: 12.5, lineHeight: 1.5,
          whiteSpace: "pre-wrap", wordBreak: "break-word", marginTop: 14,
        }}>
          {String(error.stack || error.message || error)}
        </pre>
        <button
          type="button"
          onClick={() => { localStorage.removeItem("englishbot_admin_token"); location.reload(); }}
          style={{
            marginTop: 14, padding: "10px 16px", borderRadius: 8,
            border: "1px solid #ccc", background: "#fff", cursor: "pointer",
            fontSize: 14,
          }}
        >
          Сбросить токен и перезагрузить
        </button>
      </div>
    );
  }
}
