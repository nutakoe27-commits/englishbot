// ListeningScreen.tsx — режим «слушание»: пользователь конфигурирует подкаст,
// бэкенд генерирует текст через LLM и аудио через Kokoro, фронт играет нативный <audio>.
//
// Фазы:
//   config  — ListeningSettingsPanel + кнопка «Сгенерировать»
//   loading — спиннер, AbortController на cancel
//   player  — PodcastPlayer + Transcript
//   error   — сообщение + кнопка retry

import { useEffect, useRef, useState } from "react";
import WebApp from "@twa-dev/sdk";
import "./App.css";
import { ListeningSettingsPanel } from "./ListeningSettingsPanel";
import { PodcastPlayer } from "./PodcastPlayer";
import { Transcript } from "./Transcript";
import { ProgressScreen } from "./ProgressScreen";
import { WordsScreen } from "./WordsScreen";
import { ModalScreen } from "./ModalScreen";
import { LockScreen } from "./LockScreen";
import { SubscribeScreen } from "./SubscribeScreen";
import { IconButton } from "./ds-react/IconButton";
import { SerifH } from "./ds-react/typography";
import { Icon } from "./ds-react/Icon";
import { useLucide } from "./lucide";
import {
  CATEGORY_OPTIONS,
  loadListeningSettings,
  saveListeningSettings,
  type ListeningSettings,
} from "./listeningSettings";

// Backend API URL. См. miniapp/Dockerfile (build-arg VITE_API_BASE).
const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  "https://api-english.krichigindocs.ru";

const BOT_USERNAME =
  (import.meta.env.VITE_BOT_USERNAME as string | undefined) || "kmo_ai_english_bot";

interface Props {
  onExit: () => void;
}

type Phase = "config" | "loading" | "player" | "error";

interface PodcastResult {
  session_id: number;
  transcript: string;
  audio_url: string; // абсолютный или относительный к API_BASE
  used_words: string[];
}

export function ListeningScreen({ onExit }: Props) {
  const [settings, setSettings] = useState<ListeningSettings>(() => loadListeningSettings());
  const [phase, setPhase] = useState<Phase>("config");
  const [result, setResult] = useState<PodcastResult | null>(null);
  const [error, setError] = useState<string>("");
  const [userName, setUserName] = useState<string>("there");
  const [progressOpen, setProgressOpen] = useState<boolean>(false);
  const [wordsOpen, setWordsOpen] = useState<boolean>(false);
  const [paywall, setPaywall] = useState<boolean>(false);
  const [subscribeOpen, setSubscribeOpen] = useState<boolean>(false);
  const inTelegram = !!WebApp.initData;
  const abortRef = useRef<AbortController | null>(null);
  useLucide(`${phase}-${progressOpen}-${wordsOpen}-${paywall}`);

  useEffect(() => {
    try { WebApp.ready(); } catch { /* старые клиенты */ }
    try { WebApp.expand(); } catch { /* старые клиенты */ }
    // Без этого свайп вниз при скролле конфига сворачивает Mini App.
    try { WebApp.disableVerticalSwipes?.(); } catch { /* старые клиенты */ }
    const user = WebApp.initDataUnsafe?.user;
    if (user?.first_name) setUserName(user.first_name);
  }, []);

  // Persist при каждом изменении настроек.
  useEffect(() => {
    saveListeningSettings(settings);
  }, [settings]);

  const startGeneration = async () => {
    setError("");
    setResult(null);
    setPhase("loading");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch(`${API_BASE}/api/listening/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          init_data: WebApp.initData || "",
          duration_min: settings.durationMin,
          category: settings.category,
          use_vocab: settings.useVocab,
          speed: settings.speed,
          level: settings.level,
        }),
        signal: controller.signal,
      });
      if (res.status === 402) {
        // Дневной лимит подкастов исчерпан — показываем пейволл.
        setPaywall(true);
        setPhase("config");
        return;
      }
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status}${body ? `: ${body.slice(0, 200)}` : ""}`);
      }
      const data: PodcastResult = await res.json();
      // Если бэк прислал относительный путь — собираем абсолютный.
      const audioUrl = data.audio_url.startsWith("http")
        ? data.audio_url
        : `${API_BASE}${data.audio_url}`;
      setResult({ ...data, audio_url: audioUrl });
      setPhase("player");
    } catch (e: unknown) {
      if (controller.signal.aborted) {
        setPhase("config");
        return;
      }
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  };

  const cancelGeneration = () => {
    abortRef.current?.abort();
  };

  const backToConfig = () => {
    setResult(null);
    setError("");
    setPhase("config");
  };

  return (
    <div className="tutor-shell lst-screen">
      <div className="bg-orb bg-orb--one" aria-hidden />
      <div className="bg-orb bg-orb--two" aria-hidden />

      <header className="mode-v2-top">
        <button type="button" className="mode-v2-back" onClick={onExit} aria-label="Назад">
          <Icon name="arrow-left" size={16} />
          <span>Назад</span>
        </button>
        <div className="mode-v2-title">
          <span className="mode-v2-title__icon mode-v2-title__icon--speak">
            <Icon name="headphones" size={16} />
          </span>
          <SerifH as="h1" size={22}>Слушание</SerifH>
        </div>
        <div className="mode-v2-actions">
          <span className="mode-v2-hello">Hi, {userName}</span>
          <IconButton icon="chart-no-axes-column" size="sm" label="Мой прогресс" onClick={() => setProgressOpen(true)} />
          <IconButton icon="book-marked" size="sm" label="Мои слова" onClick={() => setWordsOpen(true)} />
        </div>
      </header>

      <main className="lst-main">
        {phase === "config" && (
          <>
            <ListeningSettingsPanel value={settings} onChange={setSettings} />
            <button type="button" className="lst-primary-btn" onClick={startGeneration}>
              Сгенерировать подкаст
            </button>
          </>
        )}

        {phase === "loading" && (
          <div className="lst-loading">
            <div className="lst-spinner" aria-hidden />
            <p className="lst-loading__title">Готовлю подкаст…</p>
            <p className="lst-loading__hint">
              {settings.durationMin <= 3
                ? "Это займёт около 15 секунд."
                : "Это займёт до минуты — длинные подкасты дольше синтезируются."}
            </p>
            <button type="button" className="lst-secondary-btn" onClick={cancelGeneration}>
              Отмена
            </button>
          </div>
        )}

        {phase === "player" && result && (
          <div className="lst-player-wrap">
            <div className="lst-meta">
              <span className="lst-meta__pill">{settings.durationMin} мин</span>
              <span className="lst-meta__pill">{settings.level}</span>
              <span className="lst-meta__pill">
                {CATEGORY_OPTIONS.find((c) => c.value === settings.category)?.label
                  ?? settings.category}
              </span>
              <span className="lst-meta__pill">{settings.speed}×</span>
              {result.used_words.length > 0 && (
                <span
                  className="lst-meta__pill"
                  title={result.used_words.join(", ")}
                >
                  💡 слов из словаря: {result.used_words.length}
                </span>
              )}
            </div>
            <PodcastPlayer audioUrl={result.audio_url} onRegenerate={backToConfig} />
            <Transcript apiBase={API_BASE} text={result.transcript} />
          </div>
        )}

        {phase === "error" && (
          <div className="lst-error">
            <p className="lst-error__title">Не получилось сгенерировать</p>
            <p className="lst-error__hint">{error || "Попробуй ещё раз через минуту."}</p>
            <div className="lst-error__actions">
              <button type="button" className="lst-secondary-btn" onClick={backToConfig}>
                Назад
              </button>
              <button type="button" className="lst-primary-btn" onClick={startGeneration}>
                Повторить
              </button>
            </div>
          </div>
        )}
      </main>

      {wordsOpen && (
        <ModalScreen>
          <WordsScreen
            apiBase={API_BASE}
            onClose={() => setWordsOpen(false)}
          />
        </ModalScreen>
      )}

      {progressOpen && (
        <ModalScreen>
          <ProgressScreen
            apiBase={API_BASE}
            initData={WebApp.initData || ""}
            onClose={() => setProgressOpen(false)}
          />
        </ModalScreen>
      )}

      {paywall && (
        <LockScreen
          kind="limit_reached"
          botUsername={BOT_USERNAME}
          message="Бесплатные подкасты на сегодня закончились. С подпиской — без лимитов."
          onDismiss={() => setPaywall(false)}
          onOpenSubscribe={inTelegram ? undefined : () => setSubscribeOpen(true)}
        />
      )}

      {subscribeOpen && (
        <SubscribeScreen
          onClose={() => setSubscribeOpen(false)}
          onPaid={() => setPaywall(false)}
        />
      )}
    </div>
  );
}
