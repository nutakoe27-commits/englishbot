"""STT для аудиофайлов (не-стриминговый случай).

Backend-серверный STT — только WebSocket (см. stt_providers.py), рассчитан на
push-to-talk из Mini App. Но для Battle Mode приходит готовый файл от
MediaRecorder (audio/webm;opus), который нужно целиком транскрибировать.

Решение: ffmpeg декодирует webm → PCM s16le 16kHz mono, мы пушим чанки в ту же
очередь, что обычный оркестратор, и один раз шлём EOU. WhisperSTTProvider
возвращает final текст.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Optional

from .config import settings
from .stt_providers import get_stt_provider, INPUT_SAMPLE_RATE


log = logging.getLogger(__name__)


async def transcribe_file(audio_path: str, *, language: str = "en") -> str:
    """Декодирует аудиофайл в PCM и гонит через WhisperSTTProvider.

    Возвращает распознанный текст (пустая строка если ничего не распознано).
    Кидает RuntimeError при сбое ffmpeg или STT-сервиса.
    """
    # 1. Декодируем через ffmpeg в PCM s16le 16kHz mono.
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-loglevel", "error",
        "-i", audio_path,
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ar", str(INPUT_SAMPLE_RATE),
        "-ac", "1",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    pcm_data, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode(errors='ignore')[:500]}")
    if not pcm_data:
        log.warning("[stt_file] ffmpeg produced empty PCM for %s", audio_path)
        return ""

    log.info(
        "[stt_file] decoded %s: %d bytes PCM (~%.1f sec)",
        audio_path, len(pcm_data), len(pcm_data) / (INPUT_SAMPLE_RATE * 2),
    )

    # 2. Гоним через STT-провайдер.
    provider = get_stt_provider()
    stt_queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()

    # Пушим аудио чанками по ~200мс (6400 байт = 3200 сэмплов).
    CHUNK = 6400
    for i in range(0, len(pcm_data), CHUNK):
        await stt_queue.put({"kind": "audio", "data": pcm_data[i : i + CHUNK]})
    await stt_queue.put({"kind": "eou"})
    await stt_queue.put(None)

    # Собираем результат. Ждём первый final.
    final_text = ""
    try:
        async for event_type, text in provider.stream(stt_queue):
            if event_type == "final":
                final_text = text or ""
                break
    except Exception as exc:
        log.error("[stt_file] STT provider failed: %s", exc)
        raise RuntimeError(f"STT failed: {exc}")

    log.info("[stt_file] transcribed: %r", final_text[:200])
    return final_text
