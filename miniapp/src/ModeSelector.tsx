// ModeSelector.tsx — стартовый экран mini-app: выбор режима тренировки.
// Speaking — существующий голосовой тьютор; Listening — генерация подкаста.
// v2-стиль: warm cream surfaces, RichModeCard с illustrations, StatTile из
// ds-react, lucide-иконки, Source Serif заголовки.

import WebApp from "@twa-dev/sdk";
import { useEffect, useState } from "react";
import { ProgressScreen } from "./ProgressScreen";
import { ModalScreen } from "./ModalScreen";
import { SubscribeScreen } from "./SubscribeScreen";
import { OnboardingModal } from "./OnboardingModal";
import { LevelProgressBar } from "./LevelProgressBar";
import { fetchMe } from "./auth";
import { RichModeCard } from "./ds-react/RichModeCard";
import { StatTile } from "./ds-react/StatTile";
import { TopBar } from "./ds-react/TopBar";
import { LogoBox } from "./ds-react/LogoBox";
import { SerifH } from "./ds-react/typography";

export type Mode = "speaking" | "listening" | "grammar" | "srs";

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  "https://api-english.krichigindocs.ru";

interface Props {
  onPick: (mode: Mode) => void;
  onLoggedOut?: () => void;
}

interface MeStats {
  streak: { current: number; best: number };
  total_minutes: number;
  total_words: number;
  achievements_earned: number;
  achievements_total: number;
}

export function ModeSelector({ onPick, onLoggedOut }: Props) {
  const [userName, setUserName] = useState<string>("there");
  const [stats, setStats] = useState<MeStats | null>(null);
  const [progressOpen, setProgressOpen] = useState<boolean>(false);
  const [subscribeOpen, setSubscribeOpen] = useState<boolean>(false);
  // Онбординг. autoOpen — при первом заходе (помечаем done в БД).
  // manualOpen — юзер сам открыл через «Открыть гид» в Аккаунте (не помечаем).
  const [onboardingAuto, setOnboardingAuto] = useState<boolean>(false);
  const [onboardingManual, setOnboardingManual] = useState<boolean>(false);
  // Если юзер вернулся с ЮKassa — в URL ?payment_id=N. Открываем SubscribeScreen
  // в режиме «return» с поллингом статуса.
  const [returnPaymentId, setReturnPaymentId] = useState<number | null>(() => {
    if (typeof window === "undefined") return null;
    const p = new URLSearchParams(window.location.search);
    const v = p.get("payment_id");
    return v && !isNaN(Number(v)) ? Number(v) : null;
  });
  useEffect(() => {
    if (returnPaymentId == null) return;
    // Открываем SubscribeScreen и чистим query, чтобы при reload не открывалось снова.
    setSubscribeOpen(true);
    try {
      const url = new URL(window.location.href);
      url.searchParams.delete("payment_id");
      window.history.replaceState(null, "", url.pathname + (url.search ? url.search : "") + url.hash);
    } catch { /* ignore */ }
  }, [returnPaymentId]);

  useEffect(() => {
    try { WebApp.ready(); } catch { /* старые клиенты */ }
    try { WebApp.expand(); } catch { /* старые клиенты */ }
    // Без этого свайп вниз при скролле карточек сворачивает Mini App.
    try { WebApp.disableVerticalSwipes?.(); } catch { /* старые клиенты */ }
    const user = WebApp.initDataUnsafe?.user;
    if (user?.first_name) {
      setUserName(user.first_name);
    }
    // Подтянем профиль: для веба — first_name; для всех — флаг tutorial_done.
    // Если онбординг ещё не пройден (новый юзер) — открываем модалку.
    void fetchMe().then((me) => {
      if (!user?.first_name && me?.first_name) setUserName(me.first_name);
      if (me && me.tutorial_done === false) {
        setOnboardingAuto(true);
      }
    });

    // Статистика на главном экране — чтобы прогресс был виден сразу.
    // На вебе JWT подставляется автоматически (installFetchAuth), в Telegram
    // дополнительно передаём init_data как fallback. Тихо игнорируем ошибки.
    const initData = WebApp.initData || "";
    const url = initData
      ? `${API_BASE}/api/me/progress?init_data=${encodeURIComponent(initData)}`
      : `${API_BASE}/api/me/progress`;
    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setStats(d as MeStats); })
      .catch(() => { /* нет статистики — не критично */ });
  }, []);

  return (
    <div className="mode-selector">
      <TopBar
        title="English Tutor"
        logo={false}
        right={
          <>
            <span className="ms-hello">Hi, {userName}</span>
            <LogoBox size={32} />
          </>
        }
      />

      <main className="mode-selector__main">
        <LevelProgressBar />
        <SerifH as="h1" size={32}>Что тренируем сегодня?</SerifH>
        <p className="mode-selector__subtitle">
          Выбери режим — слова и прогресс общие.
        </p>

        <div className="mode-cards">
          <RichModeCard
            icon="mic"
            title="Разговор"
            subtitle="Push-to-talk диалог с AI-тьютором."
            illustration="/illustrations/speaking-cafe.png"
            tone="accent"
            onClick={() => onPick("speaking")}
          />
          <RichModeCard
            icon="headphones"
            title="Слушание"
            subtitle="Подкаст на твою тему и слова."
            illustration="/illustrations/listening-podcast.png"
            tone="speak"
            onClick={() => onPick("listening")}
          />
          <RichModeCard
            icon="book-open"
            title="Грамматика"
            subtitle="Уроки A1–C1 и разбор твоих ошибок."
            illustration="/illustrations/grammar-notebook.png"
            tone="warn"
            onClick={() => onPick("grammar")}
          />
          <RichModeCard
            icon="layers"
            title="Слова"
            subtitle="Карточки на повтор — интервальное запоминание."
            illustration="/illustrations/vocabulary-cards.png"
            tone="accent"
            onClick={() => onPick("srs")}
          />
        </div>

        {stats && (
          <button
            type="button"
            className="ms-stats"
            aria-label="Моя статистика — открыть полный прогресс"
            onClick={() => setProgressOpen(true)}
          >
            <StatTile icon="flame" tone="streak" value={stats.streak.current} label="дней подряд" />
            <StatTile icon="timer" tone="default" value={stats.total_minutes} label="минут практики" />
            <StatTile icon="layers" tone="default" value={stats.total_words} label="слов в словаре" />
            <StatTile icon="award" tone="accent" value={`${stats.achievements_earned}/${stats.achievements_total}`} label="медалей" />
          </button>
        )}
      </main>

      {progressOpen && (
        <ModalScreen>
          <ProgressScreen
            apiBase={API_BASE}
            initData={WebApp.initData || ""}
            onClose={() => setProgressOpen(false)}
          />
        </ModalScreen>
      )}

      {/* AccountSheet popover убран — профиль теперь живёт в bottom-tab. */}

      {subscribeOpen && (
        <SubscribeScreen
          onClose={() => { setSubscribeOpen(false); setReturnPaymentId(null); }}
          onPaid={() => {
            // Подписка активировалась — обновим stats/me в фоне.
            void fetchMe();
          }}
          initialReturnPaymentId={returnPaymentId ?? undefined}
        />
      )}

      <OnboardingModal
        open={onboardingAuto || onboardingManual}
        markDoneOnFinish={onboardingAuto && !onboardingManual}
        onClose={() => { setOnboardingAuto(false); setOnboardingManual(false); }}
      />
    </div>
  );
}
