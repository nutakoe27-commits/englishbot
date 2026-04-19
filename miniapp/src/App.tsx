/**
 * App.tsx — голосовой AI-репетитор английского языка.
 *
 * Архитектура:
 *   1. При монтировании компонента сразу запрашиваем микрофон (getUserMedia).
 *      Это фиксирует permission-prompt в момент, когда пользователь только что
 *      осознанно открыл Mini App — лучше UX, чем ждать первого нажатия на кнопку.
 *   2. Push-to-talk: удерживай кнопку → запись идёт, отпусти → ждём ответ
 *   3. Аудио захватывается через AudioWorklet (PCM 16kHz 16bit)
 *   4. PCM-фреймы отправляются по WebSocket на backend (Whisper STT)
 *   5. Ответный PCM 24kHz воспроизводится через AudioContext / AudioBuffer
 *   6. Транскрипции отображаются в логе диалога
 */

import { useCallback, useEffect, useRef, useState } from "react";
import WebApp from "@twa-dev/sdk";
import "./App.css";
import { SettingsSheet } from "./SettingsSheet";
import {
  loadSettings,
  saveSettings,
  settingsToQuery,
  type TutorSettings,
} from "./tutorSettings";

// ─── Типы ─────────────────────────────────────────────────────────────────────

type AppState =
  | "initializing" // запрашиваем микрофон при старте
  | "idle"          // микрофон получен, WS ещё не открыт
  | "connecting"
  | "connected"
  | "recording"
  | "speaking"
  | "mic-denied"    // пользователь отказал в доступе к микрофону
  | "error";

interface DialogEntry {
  id: number;
  role: "user" | "tutor";
  text: string;
}

// ─── Константы ────────────────────────────────────────────────────────────────

const WS_URL = "wss://api-english.krichigindocs.ru/ws/voice";
const OUTPUT_SAMPLE_RATE = 24000; // TTS отдаёт 24 kHz PCM
const MAX_LOG_ENTRIES = 20;

// ─── Утилиты ──────────────────────────────────────────────────────────────────

/** Декодирует PCM 16-bit little-endian в Float32Array */
function pcm16ToFloat32(buffer: ArrayBuffer): Float32Array {
  const int16 = new Int16Array(buffer);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / (int16[i] < 0 ? 0x8000 : 0x7fff);
  }
  return float32;
}

// ─── Основной компонент ───────────────────────────────────────────────────────

export default function App() {
  const [userName, setUserName] = useState<string>("there");
  const [appState, setAppState] = useState<AppState>("initializing");
  const [dialogLog, setDialogLog] = useState<DialogEntry[]>([]);
  const [statusText, setStatusText] = useState<string>("Getting ready…");
  const [errorMsg, setErrorMsg] = useState<string>("");
  // Настройки тьютора (уровень, роль, длина, исправления)
  const [settings, setSettings] = useState<TutorSettings>(() => loadSettings());
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  // Реф с актуальными настройками — openConnection читает его без ре-рендера
  const settingsRef = useRef<TutorSettings>(settings);
  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);

  // Рефы для аудио/WS объектов (не вызывают ре-рендер)
  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const sourceNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
  // Очередь воспроизведения: следующий момент времени в AudioContext
  const playbackTimeRef = useRef<number>(0);
  // Таймер перехода speaking → connected (один, перезаводим на каждый чанк)
  const speakingEndTimerRef = useRef<number | null>(null);
  const logIdRef = useRef<number>(0);
  // Флаг — ws уже закрывается
  const wsClosingRef = useRef<boolean>(false);
  // Флаг — идёт ли сейчас запись (ref для доступа из замыканий без stale closure)
  const isRecordingRef = useRef<boolean>(false);
  // Флаг — палец сейчас зажат на кнопке (критично для синхронизации с async WS)
  const isPressedRef = useRef<boolean>(false);
  // Время начала записи — для отсечения случайных коротких тапов
  const recordingStartedAtRef = useRef<number>(0);
  // ID конкретного pointer'а, который начал запись (чтобы игнорировать другие касания)
  const activePointerIdRef = useRef<number | null>(null);
  // Контейнер лога — для auto-scroll вниз при новой реплике
  const logRef = useRef<HTMLDivElement | null>(null);

  // ── Инициализация Telegram WebApp ─────────────────────────────────────────
  useEffect(() => {
    WebApp.ready();
    WebApp.expand();
    // Отключаем вертикальные свайпы, чтобы не закрывать Mini App случайно
    // во время push-to-talk.
    try {
      WebApp.disableVerticalSwipes?.();
    } catch {
      // старые версии Telegram — не критично
    }

    const user = WebApp.initDataUnsafe?.user;
    if (user?.first_name) {
      setUserName(user.first_name);
    }
  }, []);

  // ── Авто-скролл лога вниз при новой реплике ──────────────────────────────
  useEffect(() => {
    const el = logRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [dialogLog]);

  // ── Добавление реплики в лог ──────────────────────────────────────────────
  const addLogEntry = useCallback((role: "user" | "tutor", text: string) => {
    setDialogLog((prev) => {
      const newEntry: DialogEntry = {
        id: ++logIdRef.current,
        role,
        text,
      };
      const updated = [...prev, newEntry];
      return updated.slice(-MAX_LOG_ENTRIES);
    });
  }, []);

  // ── Воспроизведение PCM-аудио из бинарного WebSocket-фрейма ──────────────
  const enqueueAudio = useCallback((data: ArrayBuffer) => {
    const ctx = audioCtxRef.current;
    if (!ctx) {
      console.warn("[audio] enqueueAudio: AudioContext ещё не создан");
      return;
    }

    if (ctx.state === "suspended") {
      ctx.resume().catch((err) => console.warn("[audio] resume failed:", err));
    }

    const float32 = pcm16ToFloat32(data);
    const numSamples = float32.length;

    const audioBuffer = ctx.createBuffer(1, numSamples, OUTPUT_SAMPLE_RATE);
    audioBuffer.copyToChannel(float32, 0);

    const source = ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(ctx.destination);

    const startTime = Math.max(ctx.currentTime, playbackTimeRef.current);
    source.start(startTime);
    playbackTimeRef.current = startTime + audioBuffer.duration;
  }, []);

  // ── Запрос микрофона при старте приложения ────────────────────────────────
  // Делаем это в useEffect сразу после mount — пользователь ещё ничего не нажимал,
  // но уже понимает, что открыл голосовое приложение.
  const initMicrophone = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      mediaStreamRef.current = stream;
      // Сразу мьютим — поток получен, но пока пользователь не нажал — не шлём
      stream.getAudioTracks().forEach((t) => (t.enabled = false));

      // Создаём AudioContext для записи и воспроизведения (shared)
      const ctx = new AudioContext({ sampleRate: OUTPUT_SAMPLE_RATE });
      audioCtxRef.current = ctx;
      if (ctx.state === "suspended") {
        try {
          await ctx.resume();
        } catch {
          // на iOS без user-gesture не запустится — дорезюмим при первом клике
        }
      }

      try {
        await ctx.audioWorklet.addModule("/pcm-recorder-worklet.js");
      } catch {
        // уже загружен
      }

      const workletNode = new AudioWorkletNode(ctx, "pcm-recorder-processor");
      workletNodeRef.current = workletNode;

      workletNode.port.onmessage = (e: MessageEvent<Int16Array>) => {
        if (
          wsRef.current?.readyState === WebSocket.OPEN &&
          isRecordingRef.current
        ) {
          wsRef.current.send(e.data.buffer);
        }
      };

      const source = ctx.createMediaStreamSource(stream);
      sourceNodeRef.current = source;
      source.connect(workletNode);
      // Не подключаем к destination — чтобы не было эха

      return true;
    } catch (err) {
      console.error("Ошибка захвата микрофона:", err);
      return false;
    }
  }, []);

  // При монтировании компонента: запрашиваем микрофон, затем сразу открываем WS.
  // Так к моменту первого нажатия кнопки WS уже в OPEN — нет гонки pointerdown/pointerup.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await initMicrophone();
      if (cancelled) return;
      if (!ok) {
        setAppState("mic-denied");
        setStatusText("Microphone is needed");
        return;
      }
      setAppState("idle");
      setStatusText("Ready to talk");
      // Сразу открываем WS — к первому нажатию он будет готов
      try {
        await openConnection();
      } catch (err) {
        console.warn("Initial WS open failed:", err);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Открытие WebSocket ────────────────────────────────────────────────────
  const openConnection = useCallback(async () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      return;
    }

    setAppState("connecting");
    setStatusText("Connecting…");
    setErrorMsg("");
    wsClosingRef.current = false;

    // Резюмим AudioContext в user-gesture, если вдруг остался suspended
    const ctx = audioCtxRef.current;
    if (ctx && ctx.state === "suspended") {
      try {
        await ctx.resume();
      } catch {
        // ничего страшного
      }
    }
    playbackTimeRef.current = 0;
    if (speakingEndTimerRef.current !== null) {
      clearTimeout(speakingEndTimerRef.current);
      speakingEndTimerRef.current = null;
    }

    const initData = WebApp.initData;
    // Собираем query-строку: init_data (для валидации Telegram) + настройки тьютора.
    const queryParts: string[] = [];
    if (initData) {
      queryParts.push(`init_data=${encodeURIComponent(initData)}`);
    }
    queryParts.push(settingsToQuery(settingsRef.current));
    const wsUrl = queryParts.length
      ? `${WS_URL}?${queryParts.join("&")}`
      : WS_URL;

    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setAppState("connected");
      setStatusText("Ready to talk");
    };

    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        // Бинарные данные → PCM 24 kHz аудио
        setAppState("speaking");
        setStatusText("Speaking…");
        enqueueAudio(event.data);

        // Отменяем предыдущий таймер и заводим новый на актуальный remaining.
        // Без этого на каждый чанк стартовал свой setTimeout и самый первый срабатывал
        // посреди воспроизведения — кнопка мигала speaking ↔ connected.
        if (speakingEndTimerRef.current !== null) {
          clearTimeout(speakingEndTimerRef.current);
          speakingEndTimerRef.current = null;
        }
        const ctx = audioCtxRef.current;
        if (ctx) {
          const remaining = Math.max(0, playbackTimeRef.current - ctx.currentTime);
          speakingEndTimerRef.current = window.setTimeout(() => {
            speakingEndTimerRef.current = null;
            setAppState((prev) => (prev === "speaking" ? "connected" : prev));
            setStatusText((prev) => (prev === "Speaking…" ? "Your turn" : prev));
          }, remaining * 1000 + 200);
        }
      } else if (typeof event.data === "string") {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "text" && msg.text) {
            addLogEntry(msg.role as "user" | "tutor", msg.text);
          }
        } catch {
          // игнорируем нераспознанный JSON
        }
      }
    };

    ws.onerror = (err) => {
      console.error("WebSocket error:", err);
      if (!wsClosingRef.current) {
        setAppState("error");
        setErrorMsg("Connection error. Please try again.");
        setStatusText("Error");
      }
    };

    ws.onclose = () => {
      if (!wsClosingRef.current) {
        setAppState("idle");
        setStatusText("Ready to talk");
      }
      wsRef.current = null;
    };
  }, [addLogEntry, enqueueAudio]);

  // ── Начало записи (unmute уже открытого микрофона) ────────────────────────
  // Минимальная длительность записи: короче — считаем случайным тапом, EOU не шлём
  const MIN_RECORDING_MS = 250;

  const startRecording = useCallback(() => {
    // Строго синхронный: никаких await. WS должен быть открыт заранее
    // (через useEffect после initMicrophone). Если вдруг не открыт — откроем
    // и возвращаем false, пользователь повторит нажатие когда WS готов.
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      void openConnection();
      return false;
    }
    if (!mediaStreamRef.current) {
      setAppState("mic-denied");
      setStatusText("Microphone is needed");
      return false;
    }

    isRecordingRef.current = true;
    recordingStartedAtRef.current = Date.now();
    setAppState("recording");
    setStatusText("Listening…");

    mediaStreamRef.current
      .getAudioTracks()
      .forEach((t) => (t.enabled = true));
    return true;
  }, [openConnection]);

  // ── Остановка записи (mute, но микрофон не релизим) ───────────────────────
  const stopRecording = useCallback(() => {
    if (!isRecordingRef.current) return;
    const durationMs = Date.now() - recordingStartedAtRef.current;
    isRecordingRef.current = false;

    // Мьютим трек — браузер перестаёт давать семплы, но разрешение остаётся
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getAudioTracks().forEach((t) => (t.enabled = false));
    }

    const ws = wsRef.current;
    // Если палец был зажат меньше MIN_RECORDING_MS — считаем случайным тапом,
    // EOU не шлём (чтобы бэк не получал пустые фразы).
    if (durationMs >= MIN_RECORDING_MS && ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "eou" }));
      } catch (err) {
        console.warn("Failed to send eou marker:", err);
      }
      setAppState("connected");
      setStatusText("Thinking…");
    } else {
      setAppState("connected");
      setStatusText("Hold to talk");
    }
  }, []);

  // ── Полный релиз микрофона и аудио — только при End Session ──────────────
  const releaseMicrophone = useCallback(() => {
    if (sourceNodeRef.current) {
      sourceNodeRef.current.disconnect();
      sourceNodeRef.current = null;
    }
    if (workletNodeRef.current) {
      workletNodeRef.current.disconnect();
      workletNodeRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
    }
  }, []);

  // ── Разрыв соединения ─────────────────────────────────────────────────────
  const closeConnection = useCallback(() => {
    stopRecording();
    wsClosingRef.current = true;
    if (wsRef.current) {
      wsRef.current.close(1000);
      wsRef.current = null;
    }
    setAppState("idle");
    setStatusText("Ready to talk");
    setDialogLog([]);
  }, [stopRecording]);

  // ── Применение новых настроек: сохраняем, закрываем текущую сессию и
  // переоткрываем WS с обновлёнными query-параметрами. Тьютор пришлёт
  // новое приветствие под выбранную роль.
  const handleSettingsSave = useCallback(
    (next: TutorSettings) => {
      setSettings(next);
      saveSettings(next);
      settingsRef.current = next;
      setSettingsOpen(false);

      // Если сессия была активна — перезапускаем WS с новыми настройками.
      const ws = wsRef.current;
      if (ws && ws.readyState !== WebSocket.CLOSED) {
        wsClosingRef.current = true;
        try {
          ws.close(1000);
        } catch {
          // ignore
        }
        wsRef.current = null;
      }
      setDialogLog([]);
      setAppState("idle");
      setStatusText("Ready to talk");
      // Новая сессия с свежими настройками
      void openConnection();
    },
    [openConnection]
  );

  // Cleanup при размонтировании: полностью освобождаем микрофон и WS
  useEffect(() => {
    return () => {
      releaseMicrophone();
      if (wsRef.current) {
        wsClosingRef.current = true;
        try {
          wsRef.current.close(1000);
        } catch {
          // ignore
        }
      }
    };
  }, [releaseMicrophone]);

  // ── Обработчики кнопки (pointer events — работают и на mobile, и на desktop) ──────────
  // Логика: ключевой стайт истины — isPressedRef (палец зажат), а не appState.
  // appState меняется асинхронно (через setState), поэтому проверять его в обработчиках
  // ненадёжно — как раз отсюда идёт баг «кнопка мигает».
  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      e.preventDefault();
      // Игнорируем второй палец / правую кнопку мыши
      if (e.button !== undefined && e.button !== 0) return;
      if (isPressedRef.current) return;
      if (activePointerIdRef.current !== null) return;
      // Не начинаем запись пока идёт TTS / подключение
      if (
        appState === "speaking" ||
        appState === "connecting" ||
        appState === "initializing" ||
        appState === "mic-denied" ||
        appState === "error"
      ) {
        return;
      }

      isPressedRef.current = true;
      activePointerIdRef.current = e.pointerId;

      // Capture на самой кнопке (currentTarget), а не на вложенном svg
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        // в некоторых браузерах capture может бросать — игнорируем
      }

      startRecording();
    },
    [appState, startRecording]
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      e.preventDefault();
      // Принимаем up только от того же pointer'а, что начал запись
      if (
        activePointerIdRef.current !== null &&
        e.pointerId !== activePointerIdRef.current
      ) {
        return;
      }
      if (!isPressedRef.current) return;

      isPressedRef.current = false;
      activePointerIdRef.current = null;

      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        // ignore
      }

      if (isRecordingRef.current) {
        stopRecording();
      }
    },
    [stopRecording]
  );

  const handleRetryMicrophone = useCallback(async () => {
    setAppState("initializing");
    setStatusText("Getting ready…");
    const ok = await initMicrophone();
    if (!ok) {
      setAppState("mic-denied");
      setStatusText("Microphone is needed");
      return;
    }
    setAppState("idle");
    setStatusText("Ready to talk");
    try {
      await openConnection();
    } catch (err) {
      console.warn("WS open after retry failed:", err);
    }
  }, [initMicrophone, openConnection]);

  // ── Определение свойств кнопки по состоянию ──────────────────────────────
  const isInitializing = appState === "initializing";
  const isRecording = appState === "recording";
  const isSpeaking = appState === "speaking";
  const isConnecting = appState === "connecting";
  const isMicDenied = appState === "mic-denied";
  const isError = appState === "error";
  const buttonDisabled =
    isInitializing || isConnecting || isError || isSpeaking || isMicDenied;

  // Дата-атрибут для CSS — одно состояние управляет всем видом
  const buttonVariant = isRecording
    ? "recording"
    : isSpeaking
    ? "speaking"
    : isConnecting
    ? "connecting"
    : isInitializing
    ? "initializing"
    : isMicDenied
    ? "denied"
    : isError
    ? "error"
    : "idle";

  const buttonLabel = (() => {
    if (isRecording) return "Release to send";
    if (isSpeaking) return "Speaking";
    if (isConnecting) return "Connecting";
    if (isInitializing) return "Getting ready";
    if (isMicDenied) return "Microphone off";
    if (isError) return "Error";
    return "Hold to talk";
  })();

  // Показываем End Session всегда — но делаем невидимым, когда не нужен,
  // чтобы не было layout-shift. visibility:hidden сохраняет место.
  const endSessionVisible =
    appState === "connected" ||
    appState === "speaking" ||
    appState === "recording";

  // ─── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="tutor-app">
      {/* Декоративный фон — статичные градиентные пятна */}
      <div className="bg-orb bg-orb--one" aria-hidden />
      <div className="bg-orb bg-orb--two" aria-hidden />

      {/* Шапка */}
      <header className="tutor-header">
        <div className="tutor-brand">
          <span className="tutor-brand__dot" aria-hidden />
          <span className="tutor-brand__name">English Tutor</span>
        </div>
        <div className="tutor-header__right">
          <p className="tutor-hello">Hi, {userName}</p>
          <button
            type="button"
            className="icon-button"
            onClick={() => setSettingsOpen(true)}
            aria-label="Settings"
          >
            <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden>
              <path
                d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"
                stroke="currentColor"
                strokeWidth="1.6"
                fill="none"
              />
              <path
                d="M19.4 13a7.7 7.7 0 0 0 0-2l2-1.5-2-3.4-2.3.9a7.6 7.6 0 0 0-1.7-1L15 3.5h-4l-.4 2.4a7.6 7.6 0 0 0-1.7 1l-2.3-.9-2 3.4L6.6 11a7.7 7.7 0 0 0 0 2l-2 1.5 2 3.4 2.3-.9a7.6 7.6 0 0 0 1.7 1l.4 2.4h4l.4-2.4a7.6 7.6 0 0 0 1.7-1l2.3.9 2-3.4-2-1.5Z"
                stroke="currentColor"
                strokeWidth="1.6"
                fill="none"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>
      </header>

      {/* Лог диалога — всегда одинакового размера */}
      <section className="tutor-log" ref={logRef} aria-live="polite">
        {dialogLog.length === 0 ? (
          <div className="tutor-log__empty">
            <div className="tutor-log__empty-icon" aria-hidden>
              💬
            </div>
            <p className="tutor-log__empty-title">Let's chat in English</p>
            <p className="tutor-log__empty-hint">
              Hold the button below, speak a sentence, then release to hear me reply.
            </p>
          </div>
        ) : (
          dialogLog.map((entry) => (
            <div
              key={entry.id}
              className={`msg msg--${entry.role}`}
            >
              <span className="msg__role">
                {entry.role === "user" ? "You" : "Tutor"}
              </span>
              <span className="msg__text">{entry.text}</span>
            </div>
          ))
        )}
      </section>

      {/* Стабильный футер с кнопкой, статусом и End Session */}
      <footer className="tutor-controls">
        {/* Статус — фиксированной высоты, меняется только opacity/текст */}
        <div className="tutor-status" data-variant={buttonVariant}>
          <span className="tutor-status__dot" aria-hidden />
          <span className="tutor-status__text">{statusText}</span>
        </div>

        {/* Кнопка */}
        <button
          className="talk-button"
          data-variant={buttonVariant}
          disabled={buttonDisabled}
          onPointerDown={handlePointerDown}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onContextMenu={(e) => e.preventDefault()}
          aria-label={buttonLabel}
        >
          <span className="talk-button__ring" aria-hidden />
          <span className="talk-button__icon" aria-hidden>
            {isRecording ? (
              // Красный квадрат — "stop"
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
              </svg>
            ) : isSpeaking ? (
              // Динамик
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                <path
                  d="M4 9v6h4l5 4V5L8 9H4zm11.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02z"
                  fill="currentColor"
                />
              </svg>
            ) : (
              // Микрофон
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                <path
                  d="M12 14a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v5a3 3 0 0 0 3 3z"
                  fill="currentColor"
                />
                <path
                  d="M19 11a1 1 0 0 0-2 0 5 5 0 0 1-10 0 1 1 0 0 0-2 0 7 7 0 0 0 6 6.92V20H8a1 1 0 0 0 0 2h8a1 1 0 0 0 0-2h-3v-2.08A7 7 0 0 0 19 11z"
                  fill="currentColor"
                />
              </svg>
            )}
          </span>
        </button>

        {/* Подпись под кнопкой — фиксированной высоты */}
        <div className="talk-button__label">{buttonLabel}</div>

        {/* Нижняя строка — всегда занимает место, содержимое меняется */}
        <div className="tutor-bottom-slot">
          {isMicDenied ? (
            <button className="link-button" onClick={handleRetryMicrophone}>
              Allow microphone access
            </button>
          ) : isError ? (
            <button
              className="link-button"
              onClick={() => {
                setAppState("idle");
                setErrorMsg("");
                setStatusText("Ready to talk");
              }}
            >
              {errorMsg || "Error"} — try again
            </button>
          ) : endSessionVisible ? (
            <button className="link-button" onClick={closeConnection}>
              End session
            </button>
          ) : (
            // Невидимый плейсхолдер, чтобы не было layout-shift
            <span className="link-button" aria-hidden style={{ visibility: "hidden" }}>
              placeholder
            </span>
          )}
        </div>
      </footer>

      {settingsOpen && (
        <SettingsSheet
          initial={settings}
          onCancel={() => setSettingsOpen(false)}
          onSave={handleSettingsSave}
        />
      )}
    </div>
  );
}
