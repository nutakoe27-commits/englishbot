import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import WebApp from "@twa-dev/sdk";
import App from "./App";
import { BattleScreen } from "./BattleScreen";
import { ModeSelector, type Mode } from "./ModeSelector";
import { ListeningScreen } from "./ListeningScreen";
import { GrammarScreen } from "./GrammarScreen";
import { SrsScreen } from "./SrsScreen";
import { LoginScreen } from "./LoginScreen";
import {
  installFetchAuth,
  getToken,
  loginTelegramInitData,
  verifySession,
} from "./auth";

// Авторизация в fetch — внедряем Authorization: Bearer ко всем API-запросам.
installFetchAuth();

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

type AuthState = "loading" | "authed" | "login";

function Root() {
  // Хуки всегда первыми, до условных return — иначе React 18 в проде может
  // выкинуть «rendered fewer hooks than expected».
  const [screen, setScreen] = useState<Mode | "selector">("selector");
  const [startParam] = useState<string>(() => readStartParam());
  const [auth, setAuth] = useState<AuthState>("loading");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // 1. Внутри Telegram — авто-логин по initData (получаем JWT).
      const initData = (() => {
        try { return WebApp.initData || ""; } catch { return ""; }
      })();
      if (initData) {
        await loginTelegramInitData(initData);
        if (!cancelled) setAuth("authed");
        return;
      }
      // 2. Браузер: есть сохранённый токен → проверим; иначе экран входа.
      if (getToken()) {
        const ok = await verifySession();
        if (!cancelled) setAuth(ok ? "authed" : "login");
        return;
      }
      if (!cancelled) setAuth("login");
    })();
    return () => { cancelled = true; };
  }, []);

  const battle = parseBattle(startParam);
  if (battle) {
    return <BattleScreen battleId={battle.id} side={battle.side} />;
  }

  if (auth === "loading") {
    return <div className="boot-splash" aria-label="Загрузка" />;
  }
  if (auth === "login") {
    return <LoginScreen onAuthed={() => setAuth("authed")} />;
  }

  const backToSelector = () => setScreen("selector");
  if (screen === "speaking") return <App onExit={backToSelector} />;
  if (screen === "listening") return <ListeningScreen onExit={backToSelector} />;
  if (screen === "grammar") return <GrammarScreen onExit={backToSelector} />;
  if (screen === "srs") return <SrsScreen onExit={backToSelector} />;
  return <ModeSelector onPick={setScreen} onLoggedOut={() => setAuth("login")} />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <Root />
    </ErrorBoundary>
  </React.StrictMode>
);
