/**
 * tts.ts — озвучка слова через backend Kokoro (/api/tts/word).
 *
 * playWord('hello') → проигрывает WAV. Браузер кеширует по URL
 * (Cache-Control на бэке), повтор — мгновенно. Ошибки (сеть / 503 /
 * autoplay-policy) гасятся тихо — UI не ломается.
 */

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ||
  "https://api-english.krichigindocs.ru";

let current: HTMLAudioElement | null = null;

export function playWord(word: string): void {
  const w = (word || "").trim();
  if (!w) return;
  try {
    current?.pause();
  } catch { /* ignore */ }
  const audio = new Audio(`${API_BASE}/api/tts/word?text=${encodeURIComponent(w)}`);
  current = audio;
  // play() возвращает Promise — гасим reject (autoplay / сеть / 503).
  void audio.play().catch(() => { /* silent */ });
}
