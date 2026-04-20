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
import { LockScreen } from "./LockScreen";
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
// Username бота для deep-link на /subscribe и /start.
// Пробрасывается на этапе Docker build через VITE_BOT_USERNAME (см. docker-compose.yml).
// Указывать БЕЗ символа '@'.
const BOT_USERNAME =
  (import.meta.env.VITE_BOT_USERNAME as string | undefined) || "kmo_ai_english_bot";

// ─── Лимиты ───────────────────────────────────────────────────────────────────

type LockKind = "limit_reached" | "maintenance" | "blocked";

interface LimitsInfo {
  remaining_seconds: number; // -1 = unlimited (подписчик)
  has_subscription: boolean;
  free_seconds_per_day: number;
  used_seconds_today: number;
}

/** mm:ss из количества секунд */
function formatMmSs(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const mm = Math.floor(s / 60).toString().padStart(2, "0");
  const ss = (s % 60).toString().padStart(2, "0");
  return `${mm}:${ss}`;
}

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
  // Черновик текстового сообщения в chat-режиме
  const [chatDraft, setChatDraft] = useState<string>("");
  // Тьютор сейчас «думает» (индикация в chat-режиме пока LLM формирует ответ)
  const [chatThinking, setChatThinking] = useState<boolean>(false);
  // Лимиты бесплатного тарифа (приходят от сервера сразу после accept)
  const [limits, setLimits] = useState<LimitsInfo | null>(null);
  // Состояние lock-screen: null = обычный UI; иначе — показываем overlay
  const [lockState, setLockState] = useState<LockKind | null>(null);
  const [lockMessage, setLockMessage] = useState<string>("");
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
  // Защита от параллельных/повторных вызовов openConnection: пока идёт
  // хэндшейк нового WS или ожидание закрытия старого — дубликаты игнорируем.
  const openingRef = useRef<boolean>(false);
  // Флаг — идёт ли сейчас запись (ref для доступа из замыканий без stale closure)
  const isRecordingRef = useRef<boolean>(false);
  // Флаг — палец сейчас зажат на кнопке (критично для синхронизации с async WS)
  const isPressedRef = useRef<boolean>(false);
  // Реф для актуальных лимитов — читается из setInterval-тика без stale closure
  const limitsRef = useRef<LimitsInfo | null>(null);
  useEffect(() => {
    limitsRef.current = limits;
  }, [limits]);

  // Локальный countdown: пока WS открыт и юзер на бесплатном тарифе —
  // раз в секунду уменьшаем remaining_seconds, чтобы таймер в хедере был
  // живым. Сервер всё равно периодически шлёт «limits» для синка.
  useEffect(() => {
    const id = window.setInterval(() => {
      const cur = limitsRef.current;
      if (!cur || cur.has_subscription) return;
      // Тикаем только при живом WS
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (cur.remaining_seconds <= 0) return;
      setLimits({
        ...cur,
        remaining_seconds: Math.max(0, cur.remaining_seconds - 1),
        used_seconds_today: cur.used_seconds_today + 1,
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, []);
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

  // При монтировании компонента: в voice-режиме запрашиваем микрофон, в chat — пропускаем.
  // Затем сразу открываем WS — к первому вводу он уже в OPEN.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (settingsRef.current.mode === "chat") {
        // Чистый чат — микрофон не нужен. Сразу idle и открываем WS.
        setAppState("idle");
        setStatusText("Ready to chat");
      } else {
        const ok = await initMicrophone();
        if (cancelled) return;
        if (!ok) {
          setAppState("mic-denied");
          setStatusText("Microphone is needed");
          return;
        }
        setAppState("idle");
        setStatusText("Ready to talk");
      }
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

  // ── Открытие WebSocket ──────────────────────────────────────────────────
  // Идемпотентная, устойчивая к параллельным вызовам. Гарантии:
  //   1) Если уже есть OPEN WS — сразу выход.
  //   2) Если параллельно идёт открытие — дубликат игнорируем.
  //   3) Если старый WS ещё не закрыт — дождёмся его onclose, потом новый.
  //   4) Обработчики старого WS не трогают wsRef, если там уже другой сокет.
  const openConnection = useCallback(async () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      return;
    }
    if (openingRef.current) {
      return;
    }
    openingRef.current = true;
    try {
      // Дожидаемся закрытия старого WS (если есть), чтобы не было двух
      // параллельных соединений к бэкенду.
      const stale = wsRef.current;
      if (stale && stale.readyState !== WebSocket.CLOSED) {
        wsClosingRef.current = true;
        if (
          stale.readyState === WebSocket.OPEN ||
          stale.readyState === WebSocket.CONNECTING
        ) {
          try {
            stale.close(1000);
          } catch {
            // ignore
          }
        }
        await new Promise<void>((resolve) => {
          const prev = stale.onclose;
          stale.onclose = (ev) => {
            try {
              prev?.call(stale, ev);
            } finally {
              resolve();
            }
          };
          // Страховка: если close не придёт — пойдём дальше через секунду.
          setTimeout(resolve, 1000);
        });
      }
      wsRef.current = null;

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
      // Собираем query-строку: init_data + настройки тьютора.
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
        if (wsRef.current !== ws) return;
        setAppState("connected");
        setStatusText("Ready to talk");
      };

      ws.onmessage = (event) => {
        if (wsRef.current !== ws) return;
        if (event.data instanceof ArrayBuffer) {
          // Бинарные данные → PCM 24 kHz аудио
          setAppState("speaking");
          setStatusText("Speaking…");
          enqueueAudio(event.data);

          if (speakingEndTimerRef.current !== null) {
            clearTimeout(speakingEndTimerRef.current);
            speakingEndTimerRef.current = null;
          }
          const audioCtx = audioCtxRef.current;
          if (audioCtx) {
            const remaining = Math.max(
              0,
              playbackTimeRef.current - audioCtx.currentTime,
            );
            speakingEndTimerRef.current = window.setTimeout(() => {
              speakingEndTimerRef.current = null;
              setAppState((prev) => (prev === "speaking" ? "connected" : prev));
              setStatusText((prev) =>
                prev === "Speaking…" ? "Your turn" : prev,
              );
            }, remaining * 1000 + 200);
          }
        } else if (typeof event.data === "string") {
          try {
            const msg = JSON.parse(event.data);
            if (msg.type === "text" && msg.text) {
              addLogEntry(msg.role as "user" | "tutor", msg.text);
              if (msg.role === "tutor") setChatThinking(false);
            } else if (msg.type === "thinking") {
              setChatThinking(true);
            } else if (msg.type === "thinking_done") {
              setChatThinking(false);
            } else if (msg.type === "limits") {
              // Сервер прислал текущее состояние лимитов — сохраняем
              setLimits({
                remaining_seconds:
                  typeof msg.remaining_seconds === "number"
                    ? msg.remaining_seconds
                    : 0,
                has_subscription: !!msg.has_subscription,
                free_seconds_per_day:
                  typeof msg.free_seconds_per_day === "number"
                    ? msg.free_seconds_per_day
                    : 600,
                used_seconds_today:
                  typeof msg.used_seconds_today === "number"
                    ? msg.used_seconds_today
                    : 0,
              });
            } else if (msg.type === "limit_reached") {
              // Расходован весь дневной лимит — сервер сейчас закроет сокет (4004)
              setLimits({
                remaining_seconds: 0,
                has_subscription: false,
                free_seconds_per_day:
                  typeof msg.free_seconds_per_day === "number"
                    ? msg.free_seconds_per_day
                    : 600,
                used_seconds_today:
                  typeof msg.used_seconds_today === "number"
                    ? msg.used_seconds_today
                    : 600,
              });
              setLockMessage("");
              setLockState("limit_reached");
              wsClosingRef.current = true;
            } else if (msg.type === "maintenance") {
              // Режим техработ — сервер сейчас закроет сокет (4002)
              setLockMessage(typeof msg.message === "string" ? msg.message : "");
              setLockState("maintenance");
              wsClosingRef.current = true;
            } else if (msg.type === "blocked") {
              // Аккаунт заблокирован — сервер сейчас закроет сокет (4003)
              setLockMessage(typeof msg.message === "string" ? msg.message : "");
              setLockState("blocked");
              wsClosingRef.current = true;
            }
          } catch {
            // игнорируем нераспознанный JSON
          }
        }
      };

      ws.onerror = (err) => {
        console.error("WebSocket error:", err);
        // Ошибка старого WS не должна портить UI, если уже активен другой.
        if (wsRef.current !== ws) return;
        if (!wsClosingRef.current) {
          setAppState("error");
          setErrorMsg("Connection error. Please try again.");
          setStatusText("Error");
        }
      };

      ws.onclose = (ev) => {
        // Резервный путь: если JSON-фрейм не успел дойти до онмессажи,
        // определяем тип блокировки по close-коду.
        const code = ev?.code;
        if (wsRef.current === ws) {
          if (code === 4004) {
            setLockState((cur) => cur ?? "limit_reached");
          } else if (code === 4002) {
            setLockState((cur) => cur ?? "maintenance");
          } else if (code === 4003) {
            setLockState((cur) => cur ?? "blocked");
          }
          wsRef.current = null;
          if (!wsClosingRef.current) {
            setAppState("idle");
            setStatusText("Ready to talk");
          }
        }
      };
    } finally {
      openingRef.current = false;
    }
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
    setLimits(null);
    setLockState(null);
    setLockMessage("");
  }, [stopRecording]);

  // ── Применение новых настроек: сохраняем, закрываем текущую сессию и
  // переоткрываем WS с обновлёнными query-параметрами. Тьютор пришлёт
  // новое приветствие под выбранную роль.
  const handleSettingsSave = useCallback(
    (next: TutorSettings) => {
      const prevMode = settingsRef.current.mode;
      setSettings(next);
      saveSettings(next);
      settingsRef.current = next;
      setSettingsOpen(false);
      setDialogLog([]);
      // Сбрасываем UI-состояния от предыдущей сессии — иначе chat-thinking /
      // недопечатанный текст могут зависнуть при переключении режимов.
      setChatThinking(false);
      setChatDraft("");
      setErrorMsg("");
      if (isRecordingRef.current) {
        stopRecording();
      }
      if (speakingEndTimerRef.current !== null) {
        clearTimeout(speakingEndTimerRef.current);
        speakingEndTimerRef.current = null;
      }
      // Принудительно закрываем текущий WS — новые query-параметры
      // (в т.ч. mode) применяются только при следующем connect. Без этого
      // при переключении voice↔chat сервер остаётся в старом режиме и чат ломается.
      if (wsRef.current) {
        wsClosingRef.current = true;
        try {
          wsRef.current.close(1000);
        } catch {
          // ignore
        }
        wsRef.current = null;
      }
      void (async () => {
        // Если переключились с chat на voice и микрофон ещё не был инициализирован —
        // запрашиваем его сейчас (это реакция на жест «Apply» — valid user-gesture).
        if (
          next.mode === "voice" &&
          prevMode === "chat" &&
          !mediaStreamRef.current
        ) {
          const ok = await initMicrophone();
          if (!ok) {
            setAppState("mic-denied");
            setStatusText("Microphone is needed");
            return;
          }
        }
        if (next.mode === "voice") {
          setStatusText("Ready to talk");
        } else {
          setStatusText("Ready to chat");
        }
        // openConnection сам корректно дождётся закрытия старого WS и
        // откроет новый с обновлёнными query-параметрами.
        await openConnection();
      })();
    },
    [openConnection, initMicrophone, stopRecording],
  );

  // ── Отправка текстового сообщения в chat-режиме ────────────────────────
  const sendChatMessage = useCallback(async () => {
    const text = chatDraft.trim();
    if (!text) return;
    if (chatThinking) return; // не пускаем второй запрос пока ответ ещё не пришёл
    // Обеспечиваем, что WS открыт (на случай если бэкенд ронял сессию)
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      try {
        await openConnection();
      } catch (err) {
        console.warn("sendChatMessage: openConnection failed", err);
        return;
      }
    }
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.warn("sendChatMessage: WS не в OPEN после openConnection");
      return;
    }
    // Локальный рендер своего сообщения (бэкенд эхо не присылает в chat-режиме)
    addLogEntry("user", text);
    setChatDraft("");
    setChatThinking(true);
    try {
      ws.send(JSON.stringify({ type: "user_text", text }));
    } catch (err) {
      console.error("sendChatMessage: ws.send failed", err);
      setChatThinking(false);
    }
  }, [chatDraft, chatThinking, openConnection, addLogEntry]);

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

  // Режим текстового чата
  const isChatMode = settings.mode === "chat";

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
          {limits && !limits.has_subscription && limits.remaining_seconds >= 0 && (
            <span
              className="timer-pill"
              data-warning={limits.remaining_seconds <= 60 ? "true" : "false"}
              title="Осталось на сегодня (бесплатный тариф)"
              aria-label="Осталось времени на сегодня"
            >
              ⏱ {formatMmSs(limits.remaining_seconds)}
            </span>
          )}
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
              {isChatMode
                ? "Type a message below and press Send."
                : "Hold the button below, speak a sentence, then release to hear me reply."}
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

      {/* Стабильный футер — разный для voice / chat режимов */}
      {isChatMode ? (
        <footer className="tutor-controls tutor-controls--chat">
          {/* Индикатор «tutor is typing…» фиксированной высоты */}
          <div className="chat-thinking-indicator" aria-live="polite">
            {chatThinking ? (
              <>
                <span className="chat-thinking-indicator__dots" aria-hidden>
                  <span />
                  <span />
                  <span />
                </span>
                <span>Tutor is typing…</span>
              </>
            ) : (
              <span aria-hidden style={{ visibility: "hidden" }}>placeholder</span>
            )}
          </div>

          {/* Композер */}
          <form
            className="chat-composer"
            onSubmit={(e) => {
              e.preventDefault();
              void sendChatMessage();
            }}
          >
            <textarea
              className="chat-composer__input"
              value={chatDraft}
              onChange={(e) => setChatDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void sendChatMessage();
                }
              }}
              placeholder="Type a message…"
              rows={1}
              aria-label="Message"
              disabled={isError}
            />
            <button
              type="submit"
              className="chat-composer__send"
              disabled={!chatDraft.trim() || chatThinking || isError}
              aria-label="Send"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path
                  d="M3 11.5 20 4l-7.5 17-2.5-7.5L3 11.5z"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinejoin="round"
                  fill="currentColor"
                />
              </svg>
            </button>
          </form>

          {/* Нижняя строка — End session / error */}
          <div className="tutor-bottom-slot">
            {isError ? (
              <button
                className="link-button"
                onClick={() => {
                  setAppState("idle");
                  setErrorMsg("");
                  setStatusText("Ready to chat");
                }}
              >
                {errorMsg || "Error"} — try again
              </button>
            ) : endSessionVisible ? (
              <button className="link-button" onClick={closeConnection}>
                End session
              </button>
            ) : (
              <span className="link-button" aria-hidden style={{ visibility: "hidden" }}>
                placeholder
              </span>
            )}
          </div>
        </footer>
      ) : (
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
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                  <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
                </svg>
              ) : isSpeaking ? (
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                  <path
                    d="M4 9v6h4l5 4V5L8 9H4zm11.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02z"
                    fill="currentColor"
                  />
                </svg>
              ) : (
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

          {/* Подпись под кнопкой */}
          <div className="talk-button__label">{buttonLabel}</div>

          {/* Нижняя строка */}
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
              <span className="link-button" aria-hidden style={{ visibility: "hidden" }}>
                placeholder
              </span>
            )}
          </div>
        </footer>
      )}

      {settingsOpen && (
        <SettingsSheet
          initial={settings}
          onCancel={() => setSettingsOpen(false)}
          onSave={handleSettingsSave}
        />
      )}

      {lockState !== null && (
        <LockScreen
          kind={lockState}
          message={lockMessage}
          botUsername={BOT_USERNAME}
        />
      )}
    </div>
  );
}
