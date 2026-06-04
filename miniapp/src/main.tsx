import React, { useState } from "react";
import ReactDOM from "react-dom/client";
import WebApp from "@twa-dev/sdk";
import App from "./App";
import { BattleScreen } from "./BattleScreen";
import { ModeSelector, type Mode } from "./ModeSelector";
import { ListeningScreen } from "./ListeningScreen";

// Error boundary — без него любая runtime-ошибка в React 18 размонтирует
// всё дерево и оставляет пустой #root (чёрный экран в Telegram). Здесь мы
// хотя бы выведем текст ошибки, чтобы её было видно на устройстве.
interface BoundaryProps { children: React.ReactNode }
interface BoundaryState { error: Error | null }
class ErrorBoundary extends React.Component<BoundaryProps, BoundaryState> {
  constructor(props: BoundaryProps) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("[mini-app crashed]", error, info);
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{
          padding: 20, color: "#fff", background: "#0b0d12",
          minHeight: "100vh", fontFamily: "monospace", fontSize: 13,
          whiteSpace: "pre-wrap", wordBreak: "break-word",
        }}>
          <strong style={{ color: "#ff8a8a" }}>Mini App crashed</strong>{"\n\n"}
          {String(this.state.error?.stack || this.state.error?.message || this.state.error)}
        </div>
      );
    }
    return this.props.children;
  }
}

function safeReadStartParam(): string {
  // На iOS WebKit обращение к WebApp.* может бросать в момент module-evaluation,
  // если Telegram SDK ещё не готов / страница открыта вне Telegram. Защищаемся.
  try {
    const raw = (WebApp.initDataUnsafe?.start_param as string | undefined) || "";
    if (raw) return raw;
  } catch {
    /* fallthrough */
  }
  try {
    if (typeof window !== "undefined") {
      const m = window.location.hash.match(/tgWebAppStartParam=([^&]+)/);
      if (m) return decodeURIComponent(m[1]);
    }
  } catch {
    /* malformed payload */
  }
  return "";
}

function parseBattle(param: string): { id: number; side: "a" | "b" } | null {
  if (!param.startsWith("battle_")) return null;
  const parts = param.split("_");
  const id = parseInt(parts[1] || "0", 10);
  if (id <= 0) return null;
  const side = (parts[2] === "b" ? "b" : "a") as "a" | "b";
  return { id, side };
}

function Root() {
  // ВАЖНО: хук всегда первый, до любых условных return — иначе React 18
  // в production может выкинуть «rendered fewer hooks than expected» при
  // повторных рендерах.
  const [screen, setScreen] = useState<Mode | "selector">("selector");
  const [startParam] = useState<string>(() => safeReadStartParam());

  const battle = parseBattle(startParam);
  if (battle) {
    return <BattleScreen battleId={battle.id} side={battle.side} />;
  }

  const backToSelector = () => setScreen("selector");
  if (screen === "speaking") return <App onExit={backToSelector} />;
  if (screen === "listening") return <ListeningScreen onExit={backToSelector} />;
  return <ModeSelector onPick={setScreen} />;
}

// Всё monting'ование в try/catch — если падает createRoot или первый рендер,
// выведем причину в #root напрямую (без зависимости от ErrorBoundary, который
// сам по себе живёт внутри React-дерева).
try {
  const rootEl = document.getElementById("root");
  if (!rootEl) {
    throw new Error("#root element not found in index.html");
  }
  ReactDOM.createRoot(rootEl).render(
    // StrictMode временно отключён — на iOS WebKit двойной рендер иногда
    // ловит несовместимости со сторонним SDK; в дев-режиме включим обратно.
    <ErrorBoundary>
      <Root />
    </ErrorBoundary>
  );
} catch (e) {
  const err = e as Error;
  const root = document.getElementById("root");
  if (root) {
    root.textContent = "MOUNT ERROR\n" + (err?.stack || err?.message || String(err));
    root.setAttribute(
      "style",
      "padding:20px;font-family:monospace;font-size:13px;color:#ff8a8a;white-space:pre-wrap;word-break:break-word;",
    );
  }
  throw e;
}
