// tutorSettings.ts — типы и утилиты для настроек AI-тьютора.
// Значения совпадают с ключами в backend/app/tutor_prompt.py.

export type Level = "A2" | "B1" | "B2" | "C1";
export type Length = "short" | "long";
// Режим ввода: voice — микрофон + TTS-ответ; chat — текстовый двухсторонний чат.
export type Mode = "voice" | "chat";

export type RoleKey =
  | "language_partner"
  | "friend"
  | "barista"
  | "interviewer"
  | "travel_agent"
  | "doctor"
  | "shopkeeper"
  | "custom";

export interface TutorSettings {
  level: Level;
  role: RoleKey;
  roleCustom: string;
  length: Length;
  corrections: boolean;
  mode: Mode;
}

export const DEFAULT_SETTINGS: TutorSettings = {
  level: "B1",
  role: "language_partner",
  roleCustom: "",
  length: "short",
  corrections: true,
  mode: "voice",
};

export const LEVEL_OPTIONS: { value: Level; label: string; hint: string }[] = [
  { value: "A2", label: "A2", hint: "Elementary" },
  { value: "B1", label: "B1", hint: "Intermediate" },
  { value: "B2", label: "B2", hint: "Upper-Int." },
  { value: "C1", label: "C1", hint: "Advanced" },
];

export const LENGTH_OPTIONS: { value: Length; label: string; hint: string }[] = [
  { value: "short", label: "Short", hint: "1-2 sentences" },
  { value: "long", label: "Detailed", hint: "3-5 sentences" },
];

export const MODE_OPTIONS: { value: Mode; label: string; hint: string }[] = [
  { value: "voice", label: "Voice", hint: "speak & listen" },
  { value: "chat", label: "Chat", hint: "text only" },
];

export const ROLE_PRESETS: { value: RoleKey; label: string; emoji: string }[] = [
  { value: "language_partner", label: "Language partner", emoji: "🗣️" },
  { value: "friend", label: "Friend", emoji: "🤝" },
  { value: "barista", label: "Barista", emoji: "☕" },
  { value: "interviewer", label: "Job interviewer", emoji: "💼" },
  { value: "travel_agent", label: "Travel agent", emoji: "✈️" },
  { value: "doctor", label: "Doctor", emoji: "🩺" },
  { value: "shopkeeper", label: "Shop assistant", emoji: "🛍️" },
  { value: "custom", label: "Custom…", emoji: "✏️" },
];

const STORAGE_KEY = "englishbot.tutorSettings.v1";

export function loadSettings(): TutorSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const parsed = JSON.parse(raw) as Partial<TutorSettings>;
    return {
      level: isLevel(parsed.level) ? parsed.level : DEFAULT_SETTINGS.level,
      role: isRole(parsed.role) ? parsed.role : DEFAULT_SETTINGS.role,
      roleCustom:
        typeof parsed.roleCustom === "string"
          ? parsed.roleCustom.slice(0, 200)
          : DEFAULT_SETTINGS.roleCustom,
      length: isLength(parsed.length) ? parsed.length : DEFAULT_SETTINGS.length,
      corrections:
        typeof parsed.corrections === "boolean"
          ? parsed.corrections
          : DEFAULT_SETTINGS.corrections,
      mode: isMode(parsed.mode) ? parsed.mode : DEFAULT_SETTINGS.mode,
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(settings: TutorSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // игнорируем — приватный режим может блокировать localStorage
  }
}

export function settingsToQuery(settings: TutorSettings): string {
  const params = new URLSearchParams({
    level: settings.level,
    role: settings.role,
    length: settings.length,
    corrections: settings.corrections ? "on" : "off",
    mode: settings.mode,
  });
  if (settings.role === "custom" && settings.roleCustom.trim()) {
    params.set("role_custom", settings.roleCustom.trim().slice(0, 200));
  }
  return params.toString();
}

// ─── Валидаторы ──────────────────────────────────────────────────────────────

function isLevel(v: unknown): v is Level {
  return v === "A2" || v === "B1" || v === "B2" || v === "C1";
}

function isLength(v: unknown): v is Length {
  return v === "short" || v === "long";
}

function isMode(v: unknown): v is Mode {
  return v === "voice" || v === "chat";
}

function isRole(v: unknown): v is RoleKey {
  return (
    v === "language_partner" ||
    v === "friend" ||
    v === "barista" ||
    v === "interviewer" ||
    v === "travel_agent" ||
    v === "doctor" ||
    v === "shopkeeper" ||
    v === "custom"
  );
}
