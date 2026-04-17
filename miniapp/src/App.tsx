/**
 * App.tsx — голосовой AI-репетитор английского языка.
 *
 * Архитектура:
 *   1. Push-to-talk: удерживай кнопку → запись идёт, отпусти → ждём ответ
 *   2. Аудио захватывается через getUserMedia → AudioWorklet (PCM 16kHz 16bit)
 *   3. PCM-фреймы отправляются по WebSocket на backend → Gemini Live API
 *   4. Ответный PCM 24kHz воспроизводится через AudioContext / AudioBuffer
 *   5. Транскрипции отображаются в лог диалога
 */

import { useCallback, useEffect, useRef, useState } from "react";
import WebApp from "@twa-dev/sdk";

// ─── Типы ─────────────────────────────────────────────────────────────────────

type AppState =
  | "idle"
  | "connecting"
  | "connected"
  | "recording"
  | "speaking"
  | "error";

interface DialogEntry {
  id: number;
  role: "user" | "tutor";
  text: string;
}

// ─── Константы ────────────────────────────────────────────────────────────────

const WS_URL = "wss://api-english.krichigindocs.ru/ws/voice";
const OUTPUT_SAMPLE_RATE = 24000; // Gemini отдаёт 24 kHz PCM
const MAX_LOG_ENTRIES = 6;

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
  const [appState, setAppState] = useState<AppState>("idle");
  const [dialogLog, setDialogLog] = useState<DialogEntry[]>([]);
  const [statusText, setStatusText] = useState<string>("Ready to talk");
  const [errorMsg, setErrorMsg] = useState<string>("");

  // Рефы для аудио/WS объектов (не вызывают ре-рендер)
  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const sourceNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
  // Очередь воспроизведения: следующий момент времени в AudioContext
  const playbackTimeRef = useRef<number>(0);
  const logIdRef = useRef<number>(0);
  // Флаг — ws уже закрывается
  const wsClosingRef = useRef<boolean>(false);
  // Флаг — идёт ли сейчас запись (ref для доступа из замыканий без stale closure)
  const isRecordingRef = useRef<boolean>(false);

  // ── Инициализация Telegram WebApp ─────────────────────────────────────────
  useEffect(() => {
    WebApp.ready();
    WebApp.expand();

    const user = WebApp.initDataUnsafe?.user;
    if (user?.first_name) {
      setUserName(user.first_name);
    }
  }, []);

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
    if (!ctx) return;

    const float32 = pcm16ToFloat32(data);
    const numSamples = float32.length;

    const audioBuffer = ctx.createBuffer(1, numSamples, OUTPUT_SAMPLE_RATE);
    audioBuffer.copyToChannel(float32, 0);

    const source = ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(ctx.destination);

    // Ставим в очередь: играем сразу после предыдущего фрагмента
    const startTime = Math.max(ctx.currentTime, playbackTimeRef.current);
    source.start(startTime);
    playbackTimeRef.current = startTime + audioBuffer.duration;
  }, []);

  // ── Открытие WebSocket + AudioContext ────────────────────────────────────
  const openConnection = useCallback(async () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      return; // уже открыт
    }

    setAppState("connecting");
    setStatusText("Connecting…");
    setErrorMsg("");
    wsClosingRef.current = false;

    // Создаём AudioContext для воспроизведения
    if (!audioCtxRef.current || audioCtxRef.current.state === "closed") {
      audioCtxRef.current = new AudioContext({ sampleRate: OUTPUT_SAMPLE_RATE });
    } else if (audioCtxRef.current.state === "suspended") {
      await audioCtxRef.current.resume();
    }
    // Сброс очереди воспроизведения
    playbackTimeRef.current = 0;

    // Формируем URL с initData
    const initData = WebApp.initData;
    const wsUrl = initData
      ? `${WS_URL}?init_data=${encodeURIComponent(initData)}`
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
        // Бинарные данные → PCM 24 kHz аудио от Gemini
        setAppState("speaking");
        setStatusText("Speaking…");
        enqueueAudio(event.data);

        // После ожидаемого завершения воспроизведения — возвращаемся в connected
        const ctx = audioCtxRef.current;
        if (ctx) {
          const remaining = Math.max(0, playbackTimeRef.current - ctx.currentTime);
          setTimeout(() => {
            setAppState((prev) => (prev === "speaking" ? "connected" : prev));
            setStatusText((prev) => (prev === "Speaking…" ? "Ready to talk" : prev));
          }, remaining * 1000 + 200);
        }
      } else if (typeof event.data === "string") {
        // JSON-сообщение с транскрипцией
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

    ws.onclose = (event) => {
      if (!wsClosingRef.current) {
        setAppState("idle");
        setStatusText("Ready to talk");
      }
      wsRef.current = null;
    };
  }, [addLogEntry, enqueueAudio]);

  // ── Начало записи: getUserMedia + AudioWorklet ────────────────────────────
  const startRecording = useCallback(async () => {
    if (appState === "idle") {
      await openConnection();
    }

    // Ждём соединения (если только что открыли)
    let attempts = 0;
    while (
      wsRef.current?.readyState !== WebSocket.OPEN &&
      attempts < 30
    ) {
      await new Promise((r) => setTimeout(r, 100));
      attempts++;
    }

    if (wsRef.current?.readyState !== WebSocket.OPEN) {
      setAppState("error");
      setErrorMsg("Could not connect. Check your network.");
      return;
    }

    setAppState("recording");
    isRecordingRef.current = true;
    setStatusText("Listening…");

    try {
      // Захватываем микрофон
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

      // Создаём AudioContext для захвата (если нужен отдельный)
      let captureCtx = audioCtxRef.current;
      if (!captureCtx || captureCtx.state === "closed") {
        captureCtx = new AudioContext();
        audioCtxRef.current = captureCtx;
      }
      if (captureCtx.state === "suspended") {
        await captureCtx.resume();
      }

      // Загружаем AudioWorklet
      try {
        await captureCtx.audioWorklet.addModule("/pcm-recorder-worklet.js");
      } catch {
        // Если уже загружен — игнорируем ошибку
      }

      const workletNode = new AudioWorkletNode(
        captureCtx,
        "pcm-recorder-processor"
      );
      workletNodeRef.current = workletNode;

      // Получаем PCM-фреймы из воркплета и отправляем по WebSocket
      workletNode.port.onmessage = (e: MessageEvent<Int16Array>) => {
        if (
          wsRef.current?.readyState === WebSocket.OPEN &&
          isRecordingRef.current
        ) {
          wsRef.current.send(e.data.buffer);
        }
      };

      // Подключаем источник → воркплет
      const source = captureCtx.createMediaStreamSource(stream);
      sourceNodeRef.current = source;
      source.connect(workletNode);
      // Не подключаем к destination — чтобы не было эха
    } catch (err) {
      console.error("Ошибка захвата микрофона:", err);
      setAppState("error");
      setErrorMsg("Microphone access denied or unavailable.");
      setStatusText("Error");
    }
  }, [appState, openConnection]);

  // ── Остановка записи ──────────────────────────────────────────────────────
  const stopRecording = useCallback(() => {
    if (!isRecordingRef.current) return;
    isRecordingRef.current = false;

    // Останавливаем воркплет и медиапоток
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

    setAppState("connected");
    setStatusText("Thinking…");
  }, [appState]);

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
  }, [stopRecording]);

  // ── Обработчики кнопки (pointer events — работают и на mobile, и на desktop)
  const handlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
      if (appState === "idle" || appState === "connected") {
        startRecording();
      }
    },
    [appState, startRecording]
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      if (appState === "recording") {
        stopRecording();
      }
    },
    [appState, stopRecording]
  );

  // ── Определение свойств кнопки по состоянию ──────────────────────────────
  const isRecording = appState === "recording";
  const isSpeaking = appState === "speaking";
  const isConnecting = appState === "connecting";
  const isError = appState === "error";
  const buttonDisabled = isConnecting || isError || isSpeaking;

  const getButtonLabel = () => {
    if (isRecording) return "Release to Send";
    if (isSpeaking) return "Speaking…";
    if (isConnecting) return "Connecting…";
    if (isError) return "Error";
    return "Hold to Talk";
  };

  // ─── Render ───────────────────────────────────────────────────────────────
  return (
    <div style={styles.container}>
      {/* Шапка */}
      <div style={styles.header}>
        <h1 style={styles.title}>AI English Tutor</h1>
        <p style={styles.greeting}>Hi, {userName}!</p>
      </div>

      {/* Лог диалога */}
      <div style={styles.logContainer}>
        {dialogLog.length === 0 ? (
          <p style={styles.logPlaceholder}>
            Press and hold the button below to start talking.
          </p>
        ) : (
          dialogLog.map((entry) => (
            <div
              key={entry.id}
              style={{
                ...styles.logEntry,
                ...(entry.role === "user"
                  ? styles.logEntryUser
                  : styles.logEntryTutor),
              }}
            >
              <span style={styles.logRole}>
                {entry.role === "user" ? "You" : "Tutor"}:
              </span>{" "}
              {entry.text}
            </div>
          ))
        )}
      </div>

      {/* Кнопка Push-to-Talk */}
      <div style={styles.buttonArea}>
        <button
          style={{
            ...styles.talkButton,
            ...(isRecording ? styles.talkButtonRecording : {}),
            ...(isSpeaking ? styles.talkButtonSpeaking : {}),
            ...(buttonDisabled && !isRecording && !isSpeaking
              ? styles.talkButtonDisabled
              : {}),
          }}
          disabled={buttonDisabled}
          onPointerDown={handlePointerDown}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          // Предотвращаем долгое нажатие на мобильных (контекстное меню)
          onContextMenu={(e) => e.preventDefault()}
        >
          <span style={styles.micIcon}>
            {isRecording ? "🔴" : isSpeaking ? "🔊" : "🎤"}
          </span>
          <span style={styles.buttonLabel}>{getButtonLabel()}</span>
        </button>

        {/* Статус */}
        <p style={styles.statusText}>{statusText}</p>

        {/* Ошибка */}
        {isError && (
          <div style={styles.errorBox}>
            <p style={styles.errorText}>{errorMsg}</p>
            <button
              style={styles.retryButton}
              onClick={() => {
                setAppState("idle");
                setErrorMsg("");
                setStatusText("Ready to talk");
              }}
            >
              Try Again
            </button>
          </div>
        )}
      </div>

      {/* Кнопка сброса (показываем если уже подключены) */}
      {(appState === "connected" ||
        appState === "speaking" ||
        appState === "recording") && (
        <button style={styles.disconnectButton} onClick={closeConnection}>
          End Session
        </button>
      )}

      {/* Подвал */}
      <div style={styles.footer}>
        <p style={styles.footerText}>Powered by Gemini Live · A2–B1 English</p>
      </div>

      {/* Pulse-анимация для состояния записи */}
      <style>{pulseKeyframes}</style>
    </div>
  );
}

// ─── Стили ────────────────────────────────────────────────────────────────────

const pulseKeyframes = `
  @keyframes pulse {
    0%   { box-shadow: 0 0 0 0 rgba(244, 67, 54, 0.5); }
    70%  { box-shadow: 0 0 0 20px rgba(244, 67, 54, 0); }
    100% { box-shadow: 0 0 0 0 rgba(244, 67, 54, 0); }
  }
  @keyframes speakPulse {
    0%   { box-shadow: 0 0 0 0 rgba(76, 175, 80, 0.5); }
    70%  { box-shadow: 0 0 0 20px rgba(76, 175, 80, 0); }
    100% { box-shadow: 0 0 0 0 rgba(76, 175, 80, 0); }
  }
`;

const styles: Record<string, React.CSSProperties> = {
  container: {
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    padding: "20px 16px 100px",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    backgroundColor: "#1a1a1a",
    color: "#e0e0e0",
    boxSizing: "border-box",
    userSelect: "none",
    WebkitUserSelect: "none",
  },
  header: {
    textAlign: "center",
    marginBottom: "20px",
    paddingTop: "8px",
  },
  title: {
    fontSize: "22px",
    fontWeight: 700,
    margin: "0 0 4px",
    color: "#ffffff",
    letterSpacing: "-0.3px",
  },
  greeting: {
    fontSize: "15px",
    margin: 0,
    color: "#9e9e9e",
  },
  // ── Лог диалога ───────────────────────────────────────────────────────
  logContainer: {
    width: "100%",
    maxWidth: "420px",
    flex: 1,
    minHeight: "200px",
    maxHeight: "340px",
    overflowY: "auto",
    marginBottom: "24px",
    display: "flex",
    flexDirection: "column",
    gap: "8px",
    padding: "12px",
    backgroundColor: "#242424",
    borderRadius: "16px",
    border: "1px solid #2e2e2e",
  },
  logPlaceholder: {
    margin: "auto",
    textAlign: "center",
    color: "#555",
    fontSize: "14px",
    lineHeight: 1.5,
    padding: "20px",
  },
  logEntry: {
    padding: "8px 12px",
    borderRadius: "12px",
    fontSize: "14px",
    lineHeight: 1.45,
    maxWidth: "85%",
    wordBreak: "break-word",
  },
  logEntryUser: {
    backgroundColor: "#2a3d2a",
    border: "1px solid #3a5a3a",
    alignSelf: "flex-end",
    color: "#c8e6c9",
  },
  logEntryTutor: {
    backgroundColor: "#1e2a3a",
    border: "1px solid #2a3d5a",
    alignSelf: "flex-start",
    color: "#bbdefb",
  },
  logRole: {
    fontWeight: 600,
    opacity: 0.7,
    fontSize: "12px",
    textTransform: "uppercase" as const,
    letterSpacing: "0.5px",
  },
  // ── Кнопка ────────────────────────────────────────────────────────────
  buttonArea: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "16px",
    width: "100%",
  },
  talkButton: {
    width: "160px",
    height: "160px",
    borderRadius: "50%",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: "6px",
    border: "3px solid #4CAF50",
    backgroundColor: "#2a2a2a",
    color: "#4CAF50",
    cursor: "pointer",
    transition: "all 0.15s ease",
    WebkitTapHighlightColor: "transparent",
    touchAction: "none",
    outline: "none",
    boxShadow: "0 4px 20px rgba(76, 175, 80, 0.2)",
  },
  talkButtonRecording: {
    backgroundColor: "#3d1a1a",
    borderColor: "#f44336",
    color: "#f44336",
    animation: "pulse 1.2s ease-in-out infinite",
    boxShadow: "0 4px 20px rgba(244, 67, 54, 0.3)",
  },
  talkButtonSpeaking: {
    backgroundColor: "#1a2d1a",
    borderColor: "#4CAF50",
    color: "#4CAF50",
    animation: "speakPulse 1.2s ease-in-out infinite",
  },
  talkButtonDisabled: {
    opacity: 0.4,
    cursor: "not-allowed",
    borderColor: "#555",
    color: "#555",
    boxShadow: "none",
  },
  micIcon: {
    fontSize: "36px",
    lineHeight: 1,
    pointerEvents: "none",
  },
  buttonLabel: {
    fontSize: "12px",
    fontWeight: 600,
    letterSpacing: "0.3px",
    textAlign: "center",
    pointerEvents: "none",
  },
  statusText: {
    fontSize: "14px",
    color: "#9e9e9e",
    margin: 0,
    height: "20px",
    textAlign: "center",
  },
  // ── Ошибка ────────────────────────────────────────────────────────────
  errorBox: {
    backgroundColor: "#2d1a1a",
    border: "1px solid #5a2a2a",
    borderRadius: "12px",
    padding: "12px 16px",
    textAlign: "center",
    maxWidth: "300px",
  },
  errorText: {
    color: "#ef9a9a",
    fontSize: "13px",
    margin: "0 0 8px",
  },
  retryButton: {
    backgroundColor: "#4CAF50",
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    padding: "6px 16px",
    fontSize: "13px",
    fontWeight: 600,
    cursor: "pointer",
  },
  // ── End Session ───────────────────────────────────────────────────────
  disconnectButton: {
    marginTop: "20px",
    backgroundColor: "transparent",
    color: "#666",
    border: "1px solid #333",
    borderRadius: "8px",
    padding: "8px 20px",
    fontSize: "13px",
    cursor: "pointer",
    transition: "all 0.15s ease",
  },
  // ── Подвал ────────────────────────────────────────────────────────────
  footer: {
    position: "fixed" as const,
    bottom: "16px",
    left: 0,
    right: 0,
    textAlign: "center",
  },
  footerText: {
    fontSize: "11px",
    color: "#3a3a3a",
    margin: 0,
  },
};
