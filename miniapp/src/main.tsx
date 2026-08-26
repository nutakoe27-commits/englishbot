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
// Настоящая высота видимой области Telegram → CSS-переменная --app-vh.
// Без этого все экраны сидят на 100dvh, который в вебвью больше видимой
// области: низ уходит под обрез, а внутренний скролл не переполняется.
import { initViewport } from "./viewport";
initViewport();
// Service worker: установка на домашний экран, офлайн-заглушка и web push.
// Регистрируем после первой отрисовки — на старте он ничего не ускоряет, а
// конкурировать за сеть с бандлом ему незачем.
import { registerServiceWorker } from "./pwa";
import { syncPush } from "./push";
if (typeof window !== "undefined") {
  window.addEventListener("load", () => { void registerServiceWorker(); });
}
import App from "./App";
import { BattleScreen } from "./BattleScreen";
import { ModeSelector, type Mode } from "./ModeSelector";
import { ListeningScreen } from "./ListeningScreen";
import { GrammarScreen } from "./GrammarScreen";
import { LevelTestScreen } from "./LevelTestScreen";
import { SrsScreen } from "./SrsScreen";
import { LoginScreen } from "./LoginScreen";
import { LandingScreen } from "./LandingScreen";
import { SchoolsLanding } from "./SchoolsLanding";
import { LevelLanding, LEVEL_INTENT_KEY } from "./LevelLanding";
import { BottomNav } from "./BottomNav";
import { AccountSheet } from "./AccountSheet";
import { LeaderboardScreen } from "./LeaderboardScreen";
import { SubscribeScreen } from "./SubscribeScreen";
import { OnboardingModal } from "./OnboardingModal";
import type { TabKey } from "./tabs";
import {
  extractYandexCallback,
  installFetchAuth,
  getToken,
  joinOrg,
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

// B2B: код школы из ?school= переживает OAuth-redirect через localStorage
// (конвенция ключей et_* — как et_theme и токен в auth.ts).
const SCHOOL_CODE_KEY = "et_school_code";
// Намерение «подключить школу»: вход через Яндекс делает полную перезагрузку
// и возвращает на корень сайта, поэтому контекст /schools надо пережить.
const SCHOOLS_INTENT_KEY = "et_schools_intent";

/**
 * Намерение «я пришёл платить»: кнопка «Оплатить сразу» на лендинге ведёт
 * на тот же экран входа, но после регистрации человек должен попасть не на
 * главный экран, а сразу на тарифы. Вход через Яндекс ID перезагружает
 * страницу и возвращает на корень, поэтому намерение живёт в localStorage,
 * а не в state.
 */
const PAY_INTENT_KEY = "et_pay_intent";

/** Прочитать и погасить намерение — оно одноразовое. */
function consumePayIntent(): boolean {
  try {
    if (localStorage.getItem(PAY_INTENT_KEY) !== "1") return false;
    localStorage.removeItem(PAY_INTENT_KEY);
    return true;
  } catch {
    return false;
  }
}

/** Путь без хвостовых слэшей — по нему выбираем публичную страницу. */
function currentPath(): string {
  if (typeof window === "undefined") return "";
  return window.location.pathname.replace(/\/+$/, "");
}

function Root() {
  // Хуки всегда первыми, до условных return — иначе React 18 в проде может
  // выкинуть «rendered fewer hooks than expected».
  // Стартовый экран определяется один раз по deep-link (battle / srs / mode).
  // TabShell дальше владеет state'ом таба и mode'а.
  const [screen] = useState<Mode | "selector">("selector");
  const [startParam] = useState<string>(() => readStartParam());
  const [auth, setAuth] = useState<AuthState>("loading");
  // Deep-link скидки: бот открывает мини-апп с ?promo=CODE — сразу
  // показываем тарифы с авто-применённой скидкой. Читаем один раз и чистим URL.
  const [promoParam] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    try {
      const p = new URLSearchParams(window.location.search);
      const v = (p.get("promo") || "").trim();
      if (v) {
        const url = new URL(window.location.href);
        url.searchParams.delete("promo");
        window.history.replaceState(null, "", url.pathname + (url.search || "") + url.hash);
      }
      return v;
    } catch { return ""; }
  });
  // Бот шлёт ?subscribe=1 в напоминаниях про оплату — открываем тарифы
  // сразу, а не главный экран, с которого их ещё надо найти.
  const [subscribeParam] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      const p = new URLSearchParams(window.location.search);
      if (p.get("subscribe") !== "1") return false;
      const url = new URL(window.location.href);
      url.searchParams.delete("subscribe");
      window.history.replaceState(null, "", url.pathname + (url.search || "") + url.hash);
      return true;
    } catch { return false; }
  });
  // B2B deep-link: ?school=CODE — после входа подключаем юзера к школе.
  // Код дублируем в localStorage: вход через Яндекс OAuth делает полный
  // redirect с сайта и обратно — query и React-state теряются, а код
  // должен пережить логин. Потребляется (и чистится) в TabShell.
  const [schoolParam] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    try {
      const p = new URLSearchParams(window.location.search);
      const v = (p.get("school") || "").trim();
      if (v) {
        try { localStorage.setItem(SCHOOL_CODE_KEY, v); } catch { /* private mode */ }
        const url = new URL(window.location.href);
        url.searchParams.delete("school");
        window.history.replaceState(null, "", url.pathname + (url.search || "") + url.hash);
      }
      return v;
    } catch { return ""; }
  });
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

  // Возврат с оплаты школы мог прийти на корень (например, по старой
  // ссылке ЮKassa) — уводим на /schools, где есть экран заказа.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const p = new URLSearchParams(window.location.search);
      if (p.get("org") === "1" && p.get("payment_id")
          && window.location.pathname.replace(/\/+$/, "") !== "/schools") {
        window.location.replace(`/schools${window.location.search}`);
      }
    } catch { /* noop */ }
  }, []);

  // Вход состоялся, а юзер пришёл подключать школу — возвращаем его на
  // лендинг школ с флагом автооткрытия формы заказа.
  useEffect(() => {
    if (auth !== "authed" || typeof window === "undefined") return;
    try {
      if (localStorage.getItem(SCHOOLS_INTENT_KEY) !== "1") return;
      if (currentPath() === "/schools") return;
      localStorage.removeItem(SCHOOLS_INTENT_KEY);
      window.location.replace("/schools?connect=1");
    } catch { /* private mode */ }
  }, [auth]);

  // Вошёл — привязываем к аккаунту подписку на уведомления, если она уже
  // была. Подписаться можно и анонимно (например, с лендинга теста), и без
  // этого шага пуш некому было бы адресовать лично. Разрешений не просим:
  // syncPush работает только с уже выданными.
  useEffect(() => {
    if (auth !== "authed") return;
    void syncPush();
  }, [auth]);

  // То же для теста уровня: вход через Яндекс возвращает на корень сайта, а
  // человек шёл открывать разбор своего результата. Возвращаем на /level —
  // там test_id лежит в localStorage, и разбор подтянется сам.
  useEffect(() => {
    if (auth !== "authed" || typeof window === "undefined") return;
    try {
      if (localStorage.getItem(LEVEL_INTENT_KEY) !== "1") return;
      if (currentPath() === "/level") return;
      localStorage.removeItem(LEVEL_INTENT_KEY);
      window.location.replace("/level?claim=1");
    } catch { /* private mode */ }
  }, [auth]);

  const battle = parseBattle(startParam);
  if (battle) {
    return <BattleScreen battleId={battle.id} side={battle.side} />;
  }

  // Лендинг теста уровня /level — публичная страница, тест проходится
  // анонимно. Залогиненным тоже показываем: они могли прийти по прямой
  // ссылке или вернуться сюда после входа за разбором.
  if (currentPath() === "/level" && auth !== "loading") {
    if (auth === "authed" || !showLogin) {
      return (
        <LevelLanding
          authed={auth === "authed"}
          onLogin={() => setShowLogin(true)}
          onOpenApp={() => { window.location.href = "/"; }}
        />
      );
    }
  }

  // B2B-лендинг /schools — публичная страница подключения школы.
  // Показываем и незалогиненным, и залогиненным (там свой флоу оплаты).
  const isSchoolsPath = currentPath() === "/schools";
  if (isSchoolsPath && auth !== "loading") {
    // Залогиненный видит лендинг школ ВСЕГДА — по прямой ссылке тоже.
    // Экран входа показываем, только если гость сам нажал «Войти».
    if (auth === "authed" || !showLogin) {
      return (
        <SchoolsLanding
          authed={auth === "authed"}
          onLogin={() => setShowLogin(true)}
          onOpenApp={() => { window.location.href = "/"; }}
        />
      );
    }
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
      return <LoginScreen onAuthed={() => { setShowLogin(false); setAuth("authed"); }} />;
    }
    return (
      <LandingScreen
        onStartTrial={() => setShowLogin(true)}
        onLogin={() => setShowLogin(true)}
        onBuyNow={() => {
          try { localStorage.setItem(PAY_INTENT_KEY, "1"); } catch { /* private mode */ }
          setShowLogin(true);
        }}
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
      initialPromo={promoParam}
      initialSchool={schoolParam}
      initialSubscribe={subscribeParam}
      onLoggedOut={() => { setShowLogin(false); setAuth("login"); }}
    />
  );
}

function TabShell({
  initialTab,
  initialMode,
  initialPromo,
  initialSchool,
  initialSubscribe,
  onLoggedOut,
}: {
  initialTab?: TabKey;
  initialMode?: Mode | null;
  initialPromo?: string;
  initialSchool?: string;
  initialSubscribe?: boolean;
  onLoggedOut: () => void;
}) {
  // mode != null → юзер в тренировочном экране (Speaking/Listening/Grammar).
  // В этом случае ни один tab не подсвечен в BottomNav.
  // Клик по любому tab → выходим из mode и переключаемся.
  const [tab, setTab] = useState<TabKey>(initialTab ?? "home");
  const [mode, setMode] = useState<Mode | null>(initialMode ?? null);
  // Deep-link скидки: если бот открыл мини-апп с ?promo=CODE — сразу
  // показываем экран тарифов с авто-применённой скидкой.
  // Тарифы открываются сразу в трёх случаях: пришёл промокод из бота,
  // бот открыл мини-апп с ?subscribe=1, или человек нажал на лендинге
  // «Оплатить сразу» и только что зарегистрировался. Намерение гасим
  // здесь же — оно одноразовое.
  const [subscribeOpen, setSubscribeOpen] = useState<boolean>(
    () => !!(initialPromo && initialPromo.trim())
      || !!initialSubscribe
      || consumePayIntent(),
  );
  const [onboardingManual, setOnboardingManual] = useState<boolean>(false);
  // B2B: ?school=CODE — подключаем к школе и показываем результат тостом.
  const [orgToast, setOrgToast] = useState<string>("");

  useEffect(() => {
    // Код школы: из пропа (обычный заход) или из localStorage (веб-юзер
    // прошёл через Яндекс OAuth redirect — query потерян, код сохранён).
    let code = (initialSchool || "").trim();
    if (!code) {
      try { code = (localStorage.getItem(SCHOOL_CODE_KEY) || "").trim(); } catch { /* private mode */ }
    }
    if (!code) return;
    let alive = true;
    void (async () => {
      const r = await joinOrg(code);
      // Один заход — одна попытка: чистим сразу после ответа, чтобы не
      // переподключать юзера на каждом старте приложения.
      try { localStorage.removeItem(SCHOOL_CODE_KEY); } catch { /* private mode */ }
      if (!alive) return;
      const name = r?.org_name || "школа";
      if (!r) {
        setOrgToast("⚠️ Не получилось подключиться к школе. Попробуй позже.");
      } else if (r.status === "ok") {
        setOrgToast(`🎓 Ты подключён к школе «${name}» — полный доступ открыт!`);
      } else if (r.status === "already") {
        setOrgToast(`🎓 Ты уже ученик школы «${name}».`);
      } else if (r.status === "no_seats") {
        setOrgToast(`😔 В школе «${name}» нет свободных мест — сообщи администратору.`);
      } else {
        setOrgToast("⚠️ Код приглашения недействителен или срок доступа истёк.");
      }
      setTimeout(() => { if (alive) setOrgToast(""); }, 8000);
    })();
    return () => { alive = false; };
  }, [initialSchool]);

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
  } else if (mode === "level") {
    body = <LevelTestScreen onExit={exitMode} />;
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
  } else if (tab === "leaderboard") {
    body = <LeaderboardScreen />;
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
      {orgToast && (
        <div
          role="status"
          style={{
            position: "fixed", top: 12, left: 12, right: 12, zIndex: 60,
            padding: "12px 16px", borderRadius: 14,
            background: "var(--bg-2, #fff)", color: "var(--text, #222)",
            border: "1px solid var(--border, rgba(0,0,0,0.12))",
            boxShadow: "0 8px 24px rgba(0,0,0,0.18)",
            fontSize: 14, lineHeight: 1.4, textAlign: "center",
          }}
        >
          {orgToast}
        </div>
      )}
      <div className="app-shell__body">{body}</div>
      <BottomNav active={activeForNav as TabKey} onChange={switchTab} />
      {subscribeOpen && (
        <SubscribeScreen
          initialPromoCode={initialPromo}
          onClose={() => setSubscribeOpen(false)}
        />
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
