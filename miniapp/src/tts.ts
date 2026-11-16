/** Authenticated word audio, including the current free lesson context. */
import WebApp from '@twa-dev/sdk';
import { API_BASE } from './auth';
let context: AudioContext | null = null;
let current: AudioBufferSourceNode | null = null;
let generation = 0;
export function playWord(word: string, lessonId?: string): void {
  if (!word.trim()) return;
  const id = ++generation;
  current?.stop();
  current?.disconnect();
  current = null;
  // Resume during the tap itself: mobile browsers can reject play() after
  // waiting for an authenticated network request to finish.
  let audio: AudioContext;
  try { audio = context ??= new AudioContext(); } catch { return; }
  const ready = audio.resume().catch(() => {});
  const query = new URLSearchParams({ text: word.trim(), init_data: WebApp.initData || '' });
  if (lessonId) query.set('learning_lesson_id', lessonId);
  void fetch(`${API_BASE}/api/tts/word?${query}`).then(async response => {
    if (!response.ok) return;
    const buffer = await audio.decodeAudioData(await response.arrayBuffer());
    await ready;
    if (id !== generation) return;
    const source = audio.createBufferSource();
    source.buffer = buffer;
    source.connect(audio.destination);
    source.onended = () => { source.disconnect(); if (current === source) current = null; };
    current = source;
    source.start();
  }).catch(() => { /* A failed pronunciation must not discard the card. */ });
}
