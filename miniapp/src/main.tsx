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
          <strong>Mini App crashed</strong>{"\n\n"}
          {String(this.state.error?.stack || this.state.error?.message || this.state.error)}
        </div>
      );
    }
    return this.props.children;
  }
}

// Battle deep-link читается один раз при старте приложения — он не меняется
// в течение mount-сессии, можно безопасно вычислить в модульной области.
function readStartParam(): string {
  const raw = (WebApp.initDataUnsafe?.start_param as string | undefined) || "";
  if (raw) return raw;
  if (typeof window !== "undefined") {
    const m = window.location.hash.match(/tgWebAppStartParam=([^&]+)/);
    if (m) return decodeURIComponent(m[1]);
  }
  return "";
}

const START_PARAM = readStartParam();

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
  // повторных рендерах и положить всё mini-app в чёрный экран.
  const [screen, setScreen] = useState<Mode | "selector">("selector");

  const battle = parseBattle(START_PARAM);
  if (battle) {
    return <BattleScreen battleId={battle.id} side={battle.side} />;
  }

  const backToSelector = () => setScreen("selector");
  if (screen === "speaking") return <App onExit={backToSelector} />;
  if (screen === "listening") return <ListeningScreen onExit={backToSelector} />;
  return <ModeSelector onPick={setScreen} />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <Root />
    </ErrorBoundary>
  </React.StrictMode>
);
