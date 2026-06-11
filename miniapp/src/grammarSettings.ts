// grammarSettings.ts — типы и localStorage helpers для grammar-режима.

import type { Level } from "./tutorSettings";

export type GrammarMode = "weak_points" | "topic";

export type MistakeCategory =
  | "article"
  | "tense"
  | "preposition"
  | "word_choice"
  | "phrasal"
  | "other";

export interface GrammarSettings {
  defaultMode: GrammarMode;
  level: Level;
  category: MistakeCategory;
}

export const DEFAULT_GRAMMAR: GrammarSettings = {
  defaultMode: "topic",
  level: "B1",
  category: "tense",
};

export const CATEGORY_OPTIONS: { value: MistakeCategory; label: string; emoji: string }[] = [
  { value: "tense", label: "Времена", emoji: "⏱️" },
  { value: "article", label: "Артикли", emoji: "🔤" },
  { value: "preposition", label: "Предлоги", emoji: "🧭" },
  { value: "word_choice", label: "Выбор слов", emoji: "🎯" },
  { value: "phrasal", label: "Фразовые глаголы", emoji: "🔁" },
  { value: "other", label: "Другое", emoji: "✏️" },
];

const STORAGE_KEY = "englishbot.grammarSettings.v1";

export function loadGrammarSettings(): GrammarSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_GRAMMAR };
    const parsed = JSON.parse(raw) as Partial<GrammarSettings>;
    return {
      defaultMode: parsed.defaultMode === "weak_points" ? "weak_points" : "topic",
      level: isLevel(parsed.level) ? parsed.level : DEFAULT_GRAMMAR.level,
      category: isCategory(parsed.category) ? parsed.category : DEFAULT_GRAMMAR.category,
    };
  } catch {
    return { ...DEFAULT_GRAMMAR };
  }
}

export function saveGrammarSettings(settings: GrammarSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    /* приватный режим */
  }
}

function isLevel(v: unknown): v is Level {
  return v === "A2" || v === "B1" || v === "B2" || v === "C1";
}

function isCategory(v: unknown): v is MistakeCategory {
  return (
    v === "article" ||
    v === "tense" ||
    v === "preposition" ||
    v === "word_choice" ||
    v === "phrasal" ||
    v === "other"
  );
}
