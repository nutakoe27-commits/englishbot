// listeningSettings.ts — типы и localStorage helpers для listening-режима.
// Параллелен tutorSettings.ts.

import type { Level } from "./tutorSettings";

export type ListeningCategory =
  | "news"
  | "tech"
  | "psychology"
  | "history"
  | "science"
  | "travel"
  | "business"
  | "culture";

export type ListeningSpeed = 0.75 | 1.0 | 1.25;

// Режим выбора длительности: пресетные chips или произвольный ввод.
// Храним отдельно от durationMin, чтобы ввод «1» в Custom не схлопывал секцию
// обратно в preset-чип «1 мин».
export type DurationMode = "preset" | "custom";

export interface ListeningSettings {
  durationMin: number;       // 1..20
  durationMode: DurationMode;
  category: ListeningCategory;
  useVocab: boolean;
  speed: ListeningSpeed;
  level: Level;
}

export const DEFAULT_LISTENING: ListeningSettings = {
  durationMin: 3,
  durationMode: "preset",
  category: "news",
  useVocab: true,
  speed: 1.0,
  level: "B1",
};

export const DURATION_PRESETS: number[] = [1, 3, 5, 10, 15];
// Backend всё ещё валидирует diapason 1..20, но в UI Custom-ввода больше нет.
export const MAX_CUSTOM_DURATION = 20;

export const CATEGORY_OPTIONS: { value: ListeningCategory; label: string; emoji: string }[] = [
  { value: "news", label: "News", emoji: "🗞️" },
  { value: "tech", label: "Tech", emoji: "💻" },
  { value: "psychology", label: "Psychology", emoji: "🧠" },
  { value: "history", label: "History", emoji: "📜" },
  { value: "science", label: "Science", emoji: "🔬" },
  { value: "travel", label: "Travel", emoji: "✈️" },
  { value: "business", label: "Business", emoji: "💼" },
  { value: "culture", label: "Culture", emoji: "🎭" },
];

export const SPEED_OPTIONS: { value: ListeningSpeed; label: string }[] = [
  { value: 0.75, label: "0.75×" },
  { value: 1.0, label: "1.0×" },
  { value: 1.25, label: "1.25×" },
];

const STORAGE_KEY = "englishbot.listeningSettings.v1";

export function loadListeningSettings(): ListeningSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_LISTENING };
    const parsed = JSON.parse(raw) as Partial<ListeningSettings>;
    let durationMin = clampDuration(parsed.durationMin);
    // Migration: Custom-режим убран из UI, но в localStorage у старых юзеров
    // мог сохраниться durationMode='custom' с произвольным числом. Нормализуем:
    // если значение не из текущих пресетов — берём ближайший доступный.
    if (!DURATION_PRESETS.includes(durationMin)) {
      durationMin = DURATION_PRESETS.reduce((best, p) =>
        Math.abs(p - durationMin) < Math.abs(best - durationMin) ? p : best,
      );
    }
    return {
      durationMin,
      durationMode: "preset",
      category: isCategory(parsed.category) ? parsed.category : DEFAULT_LISTENING.category,
      useVocab:
        typeof parsed.useVocab === "boolean" ? parsed.useVocab : DEFAULT_LISTENING.useVocab,
      speed: isSpeed(parsed.speed) ? parsed.speed : DEFAULT_LISTENING.speed,
      level: isLevel(parsed.level) ? parsed.level : DEFAULT_LISTENING.level,
    };
  } catch {
    return { ...DEFAULT_LISTENING };
  }
}

export function saveListeningSettings(settings: ListeningSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // приватный режим — игнорируем
  }
}

function clampDuration(n: unknown): number {
  if (typeof n !== "number" || !Number.isFinite(n)) return DEFAULT_LISTENING.durationMin;
  return Math.min(MAX_CUSTOM_DURATION, Math.max(1, Math.round(n)));
}

function isCategory(v: unknown): v is ListeningCategory {
  return (
    v === "news" ||
    v === "tech" ||
    v === "psychology" ||
    v === "history" ||
    v === "science" ||
    v === "travel" ||
    v === "business" ||
    v === "culture"
  );
}

function isSpeed(v: unknown): v is ListeningSpeed {
  return v === 0.75 || v === 1.0 || v === 1.25;
}

function isLevel(v: unknown): v is Level {
  return v === "A2" || v === "B1" || v === "B2" || v === "C1";
}
