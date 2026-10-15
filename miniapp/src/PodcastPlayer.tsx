// PodcastPlayer.tsx — обёртка над нативным <audio> с собственной кнопкой Replay.
// WAV отдаётся бэкендом готовым (PCM 24kHz s16le mono + RIFF-заголовок),
// браузер декодирует и отдаёт scrubber/play/pause бесплатно.

import { useRef } from "react";

interface Props {
  audioUrl: string;
  onRegenerate?: () => void;
  onEnded?: () => void;
  onError?: () => void;
}

export function PodcastPlayer({ audioUrl, onRegenerate, onEnded, onError }: Props) {
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const handleReplay = () => {
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = 0;
    void a.play().catch(() => {});
  };

  return (
    <div className="lst-player">
      <audio ref={audioRef} src={audioUrl} controls preload="auto" onEnded={onEnded} onError={onError} aria-label="Подкаст" />
      <div className="lst-player__actions">
        <button type="button" className="lst-secondary-btn" onClick={handleReplay}>
          ↻ С начала
        </button>
        {onRegenerate && <button type="button" className="lst-secondary-btn" onClick={onRegenerate}>
          ✦ Новый подкаст
        </button>}
      </div>
    </div>
  );
}
