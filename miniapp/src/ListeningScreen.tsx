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
  const abortRef = useRef<AbortController | null>(null);

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

      <header className="tutor-header">
        <div className="tutor-brand">
          <button
            type="button"
            className="icon-button tutor-back"
            onClick={onExit}
            aria-label="Назад к выбору режима"
            title="Назад"
          >
            <span style={{ fontSize: 18, lineHeight: 1 }} aria-hidden>←</span>
          </button>
          <span className="tutor-brand__dot" aria-hidden />
          <span className="tutor-brand__name">Listening</span>
        </div>
        <div className="tutor-header__right">
          <p className="tutor-hello">Hi, {userName}</p>
          <button
            type="button"
            className="icon-button"
            onClick={() => setProgressOpen(true)}
            aria-label="Мой прогресс"
            title="Мой прогресс"
          >
            <span style={{ fontSize: 18, lineHeight: 1 }} aria-hidden>📊</span>
          </button>
          <button
            type="button"
            className="icon-button"
            onClick={() => setWordsOpen(true)}
            aria-label="Мои слова"
            title="Мои слова"
          >
            <span style={{ fontSize: 18, lineHeight: 1 }} aria-hidden>📖</span>
          </button>
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
        <WordsScreen
          apiBase={API_BASE}
          onClose={() => setWordsOpen(false)}
        />
      )}

      {progressOpen && (
        <ProgressScreen
          apiBase={API_BASE}
          initData={WebApp.initData || ""}
          onClose={() => setProgressOpen(false)}
        />
      )}
    </div>
  );
}
