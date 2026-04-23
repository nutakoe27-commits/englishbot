/**
 * BattleScreen.tsx — экран записи Battle Mode.
 *
 * Открывается через deep-link Telegram Mini App: startapp=battle_<id>_<side>.
 * Читает start_param из WebApp.initDataUnsafe, показывает тему, запускает
 * 60-секундный таймер записи и отправляет аудио на backend.
 *
 * Поток:
 *   1. GET /api/battles/<id>/state-miniapp?init_data=... — получаем тему.
 *   2. Юзер жмёт «Start» → начинается запись (MediaRecorder).
 *   3. Через 60 сек (или по кнопке Stop) — аудио улетает POST-ом на
 *      /api/battles/<id>/record-miniapp multipart/form-data.
 *   4. Показываем экран «Ждём соперника» / «Победитель».
 */

import { useCallback, useEffect, useRef, useState } from "react";
import WebApp from "@twa-dev/sdk";

const API_BASE = "https://api-english.krichigindocs.ru";
const MAX_RECORDING_SECONDS = 60;

type BattleSide = "a" | "b";

interface BattleState {
  id: number;
  status: string; // pending | accepted | recorded | judged | expired
  topic_title_ru: string;
  prompt_en: string;
  side_a_ru: string;
  side_b_ru: string;
  my_side: BattleSide;
  my_recorded: boolean;
  other_recorded: boolean;
  winner: string | null; // "a" | "b" | "draw" | null
  judge_comment: string | null;
}

type Phase =
  | "loading"
  | "ready"
  | "recording"
  | "uploading"
  | "waiting-opponent"
  | "judged"
  | "error";

function fmt(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
}

interface Props {
  battleId: number;
  side: BattleSide;
}

export function BattleScreen({ battleId, side }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [state, setState] = useState<BattleState | null>(null);
  const [err, setErr] = useState<string>("");
  const [timeLeft, setTimeLeft] = useState<number>(MAX_RECORDING_SECONDS);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);

  const initData = WebApp.initData || "";

  const loadState = useCallback(async () => {
    try {
      const url = `${API_BASE}/api/battles/${battleId}/state-miniapp?side=${side}&init_data=${encodeURIComponent(
        initData
      )}`;
      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data: BattleState = await res.json();
      setState(data);
      if (data.status === "judged") {
        setPhase("judged");
      } else if (data.my_recorded) {
        setPhase("waiting-opponent");
      } else {
        setPhase("ready");
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  }, [battleId, side, initData]);

  useEffect(() => {
    loadState();
  }, [loadState]);

  // Опрос состояния раз в 3 сек в фазах «ждём судью/оппонента»
  useEffect(() => {
    if (phase !== "waiting-opponent") return;
    const h = window.setInterval(() => loadState(), 3000);
    return () => window.clearInterval(h);
  }, [phase, loadState]);

  // Отпустить ресурсы на unmount
  useEffect(() => {
    return () => {
      if (timerRef.current != null) window.clearInterval(timerRef.current);
      const r = recorderRef.current;
      if (r && r.state !== "inactive") {
        try {
          r.stop();
        } catch {}
      }
      const s = streamRef.current;
      if (s) s.getTracks().forEach((t) => t.stop());
    };
  }, []);

  const startRecording = useCallback(async () => {
    setErr("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      // Пытаемся использовать opus в webm — хорошо сжимается и поддерживается Whisper.
      const mimeCandidates = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4",
      ];
      const mimeType =
        mimeCandidates.find((m) => MediaRecorder.isTypeSupported(m)) || "";
      const rec = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = async () => {
        const blob = new Blob(chunksRef.current, {
          type: mimeType || "audio/webm",
        });
        await uploadRecording(blob);
      };
      rec.start();
      recorderRef.current = rec;
      setPhase("recording");
      setTimeLeft(MAX_RECORDING_SECONDS);

      const startedAt = Date.now();
      timerRef.current = window.setInterval(() => {
        const elapsed = Math.floor((Date.now() - startedAt) / 1000);
        const left = MAX_RECORDING_SECONDS - elapsed;
        setTimeLeft(Math.max(0, left));
        if (left <= 0) {
          stopRecording();
        }
      }, 250);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopRecording = useCallback(() => {
    if (timerRef.current != null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    const r = recorderRef.current;
    if (r && r.state !== "inactive") {
      try {
        r.stop();
      } catch {}
    }
    const s = streamRef.current;
    if (s) s.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setPhase("uploading");
  }, []);

  const uploadRecording = useCallback(
    async (blob: Blob) => {
      try {
        const form = new FormData();
        form.append("audio", blob, "battle.webm");
        // init_data и side — как Form-поля (сервер принимает init_data = Form(...))
        form.append("init_data", initData);
        form.append("side", side);
        const url = `${API_BASE}/api/battles/${battleId}/record-miniapp`;
        const res = await fetch(url, { method: "POST", body: form });
        if (!res.ok) {
          throw new Error(`upload failed: HTTP ${res.status}`);
        }
        await loadState();
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
        setPhase("error");
      }
    },
    [battleId, side, initData, loadState]
  );

  // ─── Render ────────────────────────────────────────────────────────

  if (phase === "loading") {
    return <Center>Загружаем битву…</Center>;
  }

  if (phase === "error") {
    return (
      <Center>
        <div style={{ textAlign: "center", maxWidth: 320 }}>
          <h2 style={{ color: "#e74c3c" }}>Ошибка</h2>
          <p style={{ color: "#bbb" }}>{err || "Что-то пошло не так."}</p>
          <button style={btnPrimary} onClick={loadState}>
            Попробовать снова
          </button>
        </div>
      </Center>
    );
  }

  if (!state) {
    return <Center>Нет данных о битве.</Center>;
  }

  const myPosition = state.my_side === "a" ? state.side_a_ru : state.side_b_ru;

  if (phase === "judged") {
    return <JudgedView state={state} />;
  }

  if (phase === "waiting-opponent") {
    return (
      <Center>
        <div style={{ textAlign: "center", maxWidth: 360 }}>
          <h2 style={{ color: "#fff" }}>Запись отправлена</h2>
          <p style={{ color: "#bbb", marginTop: 16 }}>
            {state.other_recorded
              ? "Оба аргумента записаны — ждём вердикта ИИ-судьи…"
              : "Ждём, пока соперник запишет свой ответ. Результат придёт в чат, где был брошен вызов."}
          </p>
          <div style={{ marginTop: 24, color: "#888", fontSize: 13 }}>
            Можно закрыть — бот напишет, когда всё готово.
          </div>
        </div>
      </Center>
    );
  }

  return (
    <div style={shellStyle}>
      <div style={cardStyle}>
        <div style={{ fontSize: 13, color: "#888", marginBottom: 4 }}>
          Battle #{state.id} · тема
        </div>
        <div style={{ fontSize: 20, fontWeight: 600, color: "#fff" }}>
          {state.topic_title_ru}
        </div>
        <div
          style={{
            marginTop: 14,
            fontSize: 15,
            color: "#ddd",
            fontStyle: "italic",
          }}
        >
          {state.prompt_en}
        </div>
        <div
          style={{
            marginTop: 18,
            padding: "12px 14px",
            background: "#1f2a44",
            borderRadius: 10,
          }}
        >
          <div style={{ fontSize: 12, color: "#89a", marginBottom: 2 }}>
            Твоя позиция
          </div>
          <div style={{ fontSize: 15, color: "#fff", fontWeight: 500 }}>
            {myPosition}
          </div>
        </div>
      </div>

      <div style={{ height: 20 }} />

      <div style={{ textAlign: "center" }}>
        {phase === "ready" && (
          <>
            <p style={{ color: "#bbb", fontSize: 14 }}>
              60 секунд на английском. Аргументируй свою позицию —
              ИИ-судья оценит грамматику, беглость и аргументацию.
            </p>
            <button style={btnPrimary} onClick={startRecording}>
              🎤 Начать запись
            </button>
          </>
        )}

        {phase === "recording" && (
          <>
            <div
              style={{
                fontSize: 64,
                fontWeight: 700,
                color: timeLeft <= 10 ? "#e74c3c" : "#fff",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {fmt(timeLeft)}
            </div>
            <p style={{ color: "#bbb", fontSize: 14 }}>
              Идёт запись. Говори на английском.
            </p>
            <button style={btnDanger} onClick={stopRecording}>
              ⏹ Остановить и отправить
            </button>
          </>
        )}

        {phase === "uploading" && (
          <p style={{ color: "#bbb" }}>Отправляем запись…</p>
        )}
      </div>
    </div>
  );
}

function JudgedView({ state }: { state: BattleState }) {
  const iWon = state.winner && state.winner === state.my_side;
  const draw = state.winner === "draw";
  const title = draw ? "Ничья" : iWon ? "Ты победил" : "Соперник победил";
  const color = draw ? "#f1c40f" : iWon ? "#2ecc71" : "#e74c3c";

  return (
    <Center>
      <div style={{ textAlign: "center", maxWidth: 380 }}>
        <div style={{ fontSize: 48 }}>
          {draw ? "🤝" : iWon ? "🏆" : "💔"}
        </div>
        <h2 style={{ color }}>{title}</h2>
        <div style={{ fontSize: 13, color: "#888", marginTop: 8 }}>
          {state.topic_title_ru}
        </div>
        {state.judge_comment && (
          <div
            style={{
              marginTop: 20,
              padding: "14px 16px",
              background: "#1f2a44",
              borderRadius: 10,
              color: "#ddd",
              fontSize: 14,
              textAlign: "left",
              whiteSpace: "pre-wrap",
            }}
          >
            {state.judge_comment}
          </div>
        )}
        <div style={{ marginTop: 24, color: "#888", fontSize: 13 }}>
          Результат также запощен в чат, где был брошен вызов.
        </div>
      </div>
    </Center>
  );
}

// ─── utility components ────────────────────────────────────────────

function Center({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
        background: "#11131c",
        color: "#eee",
      }}
    >
      {children}
    </div>
  );
}

const shellStyle: React.CSSProperties = {
  minHeight: "100vh",
  padding: "20px 18px 40px",
  background: "#11131c",
  color: "#eee",
};

const cardStyle: React.CSSProperties = {
  background: "#18202f",
  borderRadius: 14,
  padding: "16px 18px",
};

const btnPrimary: React.CSSProperties = {
  background: "#2b6cff",
  color: "#fff",
  border: "none",
  borderRadius: 12,
  padding: "14px 22px",
  fontSize: 16,
  fontWeight: 600,
  cursor: "pointer",
  marginTop: 16,
};

const btnDanger: React.CSSProperties = {
  background: "#e74c3c",
  color: "#fff",
  border: "none",
  borderRadius: 12,
  padding: "14px 22px",
  fontSize: 16,
  fontWeight: 600,
  cursor: "pointer",
  marginTop: 16,
};
