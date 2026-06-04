// PodcastPlayer.tsx — обёртка над нативным <audio> с собственной кнопкой Replay.
// WAV отдаётся бэкендом готовым (PCM 24kHz s16le mono + RIFF-заголовок),
// браузер декодирует и отдаёт scrubber/play/pause бесплатно.

import { useRef } from "react";

interface Props {
  audioUrl: string;
  onRegenerate: () => void;
}

export function PodcastPlayer({ audioUrl, onRegenerate }: Props) {
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const handleReplay = () => {
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = 0;
    void a.play();
  };

  return (
    <div className="lst-player">
      <audio ref={audioRef} src={audioUrl} controls preload="auto" />
      <div className="lst-player__actions">
        <button type="button" className="lst-secondary-btn" onClick={handleReplay}>
          ↻ С начала
        </button>
        <button type="button" className="lst-secondary-btn" onClick={onRegenerate}>
          ✦ Новый подкаст
        </button>
      </div>
    </div>
  );
}
