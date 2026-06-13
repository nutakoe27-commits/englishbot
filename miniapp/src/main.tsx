import React, { useState } from "react";
import ReactDOM from "react-dom/client";
import WebApp from "@twa-dev/sdk";
import App from "./App";
import { BattleScreen } from "./BattleScreen";
import { ModeSelector, type Mode } from "./ModeSelector";
import { ListeningScreen } from "./ListeningScreen";
import { GrammarScreen } from "./GrammarScreen";
import { SrsScreen } from "./SrsScreen";

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

function readStartParam(): string {
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
  // Хук всегда первый, до условных return — иначе React 18 в проде может
  // выкинуть «rendered fewer hooks than expected».
  const [screen, setScreen] = useState<Mode | "selector">("selector");
  const [startParam] = useState<string>(() => readStartParam());

  const battle = parseBattle(startParam);
  if (battle) {
    return <BattleScreen battleId={battle.id} side={battle.side} />;
  }

  const backToSelector = () => setScreen("selector");
  if (screen === "speaking") return <App onExit={backToSelector} />;
  if (screen === "listening") return <ListeningScreen onExit={backToSelector} />;
  if (screen === "grammar") return <GrammarScreen onExit={backToSelector} />;
  if (screen === "srs") return <SrsScreen onExit={backToSelector} />;
  return <ModeSelector onPick={setScreen} />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <Root />
    </ErrorBoundary>
  </React.StrictMode>
);
