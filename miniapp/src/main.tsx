import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import WebApp from "@twa-dev/sdk";
// Design System: токены (palette, typography, spacing) + .et-* компоненты.
// Импортируется ПЕРВЫМ, чтобы App.css и Landing.css могли использовать
// var(--bg/--text/--accent/--space-*/--radius-*).
import "./ds/styles.css";
// Стили dsx-* компонентов (Button/Card/Chip/Switch/TopBar/etc), используются в
// новых экранах фаз 1+ ребрендинга на v2 дизайн-систему.
import "./ds-react/components.css";
import { initTheme } from "./theme";
initTheme();
import App from "./App";
import { BattleScreen } from "./BattleScreen";
import { ModeSelector, type Mode } from "./ModeSelector";
import { ListeningScreen } from "./ListeningScreen";
import { GrammarScreen } from "./GrammarScreen";
import { SrsScreen } from "./SrsScreen";
import { LoginScreen } from "./LoginScreen";
import { LandingScreen } from "./LandingScreen";
import { BottomNav } from "./BottomNav";
import { AccountSheet } from "./AccountSheet";
import { ProgressScreen } from "./ProgressScreen";
import { SubscribeScreen } from "./SubscribeScreen";
import { OnboardingModal } from "./OnboardingModal";
import type { TabKey } from "./tabs";
import {
  extractYandexCallback,
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
  // Стартовый экран определяется один раз по deep-link (battle / srs / mode).
  // TabShell дальше владеет state'ом таба и mode'а.
  const [screen] = useState<Mode | "selector">("selector");
  const [startParam] = useState<string>(() => readStartParam());
  const [auth, setAuth] = useState<AuthState>("loading");
  // На вебе для не-залогиненных показываем сначала Landing, по клику
  // CTA — LoginScreen. Внутри Telegram Mini App initData аутентифицирует
  // юзера автоматически (см. ниже), Landing не появляется.
  const [showLogin, setShowLogin] = useState<boolean>(false);

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
      // 1.5. Возврат с Yandex OAuth: в URL fragment #yandex_jwt=… —
      // достаём JWT и кладём в localStorage до проверки getToken().
      // Иначе main.tsx покажет лендинг (showLogin локальный = false после
      // полной перезагрузки), и юзеру придётся ещё раз кликнуть «Войти».
      extractYandexCallback();
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
    // Если юзер уже кликнул CTA на лендинге — показываем форму. Иначе — лендинг.
    // Особый случай: в URL есть ?payment_id= (возврат с ЮKassa) — это
    // залогиненный юзер, который вернулся после оплаты; не показываем лендинг,
    // сразу логин.
    const hasPaymentReturn = typeof window !== "undefined"
      && new URLSearchParams(window.location.search).has("payment_id");
    if (showLogin || hasPaymentReturn) {
      return <LoginScreen onAuthed={() => setAuth("authed")} />;
    }
    return (
      <LandingScreen
        onStartTrial={() => setShowLogin(true)}
        onLogin={() => setShowLogin(true)}
      />
    );
  }

  return (
    <TabShell
      initialTab={
        screen === "srs"
          ? "words"
          : screen === "selector"
            ? "home"
            : undefined
      }
      initialMode={
        screen === "speaking" || screen === "listening" || screen === "grammar"
          ? screen
          : null
      }
      onLoggedOut={() => { setShowLogin(false); setAuth("login"); }}
    />
  );
}

function TabShell({
  initialTab,
  initialMode,
  onLoggedOut,
}: {
  initialTab?: TabKey;
  initialMode?: Mode | null;
  onLoggedOut: () => void;
}) {
  // mode != null → юзер в тренировочном экране (Speaking/Listening/Grammar).
  // В этом случае ни один tab не подсвечен в BottomNav.
  // Клик по любому tab → выходим из mode и переключаемся.
  const [tab, setTab] = useState<TabKey>(initialTab ?? "home");
  const [mode, setMode] = useState<Mode | null>(initialMode ?? null);
  const [subscribeOpen, setSubscribeOpen] = useState<boolean>(false);
  const [onboardingManual, setOnboardingManual] = useState<boolean>(false);

  const switchTab = (next: TabKey) => {
    setMode(null);
    setTab(next);
  };
  const exitMode = () => setMode(null);

  let body: React.ReactNode;
  if (mode === "speaking") {
    body = <App onExit={exitMode} />;
  } else if (mode === "listening") {
    body = <ListeningScreen onExit={exitMode} />;
  } else if (mode === "grammar") {
    body = <GrammarScreen onExit={exitMode} />;
  } else if (tab === "home") {
    body = (
      <ModeSelector
        onPick={(m) => {
          if (m === "srs") { setTab("words"); return; }
          setMode(m);
        }}
        onLoggedOut={onLoggedOut}
      />
    );
  } else if (tab === "progress") {
    body = (
      <ProgressScreen
        apiBase={(import.meta.env.VITE_API_BASE as string) || "https://api-english.krichigindocs.ru"}
        initData={WebApp.initData || ""}
        onClose={() => setTab("home")}
      />
    );
  } else if (tab === "words") {
    body = <SrsScreen onExit={() => setTab("home")} />;
  } else {
    body = (
      <AccountSheet
        embedded
        onLoggedOut={onLoggedOut}
        onOpenSubscribe={() => setSubscribeOpen(true)}
        onOpenTutorial={() => setOnboardingManual(true)}
      />
    );
  }

  // В режиме тренировки ни один таб не активен — но нав видна и кликабельна.
  // При клике exit'имся из mode и переключаемся.
  const activeForNav: TabKey | undefined = mode ? undefined : tab;

  return (
    <div className="app-shell">
      <div className="app-shell__body">{body}</div>
      <BottomNav active={activeForNav as TabKey} onChange={switchTab} />
      {subscribeOpen && (
        <SubscribeScreen onClose={() => setSubscribeOpen(false)} />
      )}
      {onboardingManual && (
        <OnboardingModal
          open
          markDoneOnFinish={false}
          onClose={() => setOnboardingManual(false)}
        />
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <Root />
    </ErrorBoundary>
  </React.StrictMode>
);
