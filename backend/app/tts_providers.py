"""
tts_providers.py — TTS-провайдер (синтез речи).

Единственный бэкенд — локальный Kokoro-82M на V100, пробрасываемый
на VPS через SSH-reverse-tunnel. WebSocket JSON-протокол, PCM 24kHz.

Контракт:
    async def synthesize(text: str) -> AsyncIterator[bytes]
        yield PCM s16le 24kHz mono chunks
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import AsyncIterator, Protocol

import websockets
from websockets.exceptions import ConnectionClosed

from .config import settings

logger = logging.getLogger(__name__)

OUTPUT_SAMPLE_RATE = 24000  # фиксировано по всему пайплайну


# ─── Контракт ────────────────────────────────────────────────────────────────

class TTSProvider(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...


# ─── Kokoro-82M (локально на V100) ───────────────────────────────────────────

class KokoroTTSProvider:
    """
    WebSocket-клиент к локальному Kokoro-серверу на V100.

    Протокол (см. v100/kokoro_tts_server.py):
      клиент → сервер:
        {"type":"config","voice":"af_heart","speed":1.0}
        {"type":"text","text":"..."}
      сервер → клиент:
        {"type":"ready"}
        {"type":"audio","data":"<base64 s16le PCM 24kHz>"}
        {"type":"done"}
        {"type":"error","message":"..."}

    Под каждый синтез открываем отдельное WS-соединение.
    """

    def __init__(self, url: str, voice: str, speed: float = 1.0):
        self.url = url
        self.voice = voice
        self.speed = speed

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        try:
            ws = await websockets.connect(
                self.url,
                open_timeout=5.0,
                ping_interval=20,
                ping_timeout=20,
                max_size=8 * 1024 * 1024,  # большие text-фреймы с base64
            )
        except Exception as exc:
            logger.error("[Kokoro TTS] не смог подключиться к %s: %s", self.url, exc)
            return

        logger.warning("[Kokoro TTS] WS открыт: %s, voice=%s", self.url, self.voice)
        chunks_sent = 0
        bytes_sent = 0

        try:
            # 1) ждём первый ready от сервера (шлётся сразу при accept)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                first = json.loads(raw)
                if first.get("type") != "ready":
                    logger.error("[Kokoro TTS] ожидал ready, получил: %s", first)
                    return
            except asyncio.TimeoutError:
                logger.error("[Kokoro TTS] таймаут ожидания первого ready")
                return

            # 2) шлём конфиг (голос, скорость) — сервер ответит вторым ready
            await ws.send(json.dumps({
                "type": "config",
                "voice": self.voice,
                "speed": self.speed,
            }))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                second = json.loads(raw)
                if second.get("type") != "ready":
                    logger.error("[Kokoro TTS] ожидал ready после config: %s", second)
                    return
            except asyncio.TimeoutError:
                logger.error("[Kokoro TTS] таймаут config-ready")
                return

            # 3) шлём текст
            await ws.send(json.dumps({"type": "text", "text": text}))

            # 4) читаем audio-чанки до done / error / таймаута на первый чанк
            # Первый чанк от Kokoro приходит после синтеза (Kokoro не стримит
            # синтез в реальном времени — сначала считает, потом режет на куски).
            # Для фразы из 1-2 предложений это обычно 0.5-1.5 сек.
            first_chunk = True
            while True:
                timeout = 15.0 if first_chunk else 30.0
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.error(
                        "[Kokoro TTS] таймаут (%s чанк, %.0fс)",
                        "первый" if first_chunk else "очередной", timeout,
                    )
                    return
                except ConnectionClosed as exc:
                    logger.warning("[Kokoro TTS] WS закрыт во время recv: %s", exc)
                    return

                if isinstance(raw, bytes):
                    # Бинарные фреймы не ожидаем — игнорируем.
                    continue

                try:
                    msg = json.loads(raw)
                except Exception:
                    logger.warning("[Kokoro TTS] непарсимое: %r", raw[:200])
                    continue

                mtype = msg.get("type")
                if mtype == "audio":
                    try:
                        pcm = base64.b64decode(msg["data"])
                    except Exception as exc:
                        logger.error("[Kokoro TTS] битый base64: %s", exc)
                        continue
                    chunks_sent += 1
                    bytes_sent += len(pcm)
                    first_chunk = False
                    yield pcm
                elif mtype == "done":
                    logger.warning(
                        "[Kokoro TTS] готово: %d чанков, %d байт (%.2f сек аудио)",
                        chunks_sent, bytes_sent, bytes_sent / 2 / OUTPUT_SAMPLE_RATE,
                    )
                    return
                elif mtype == "error":
                    logger.error("[Kokoro TTS] ошибка сервера: %s", msg.get("message"))
                    return
                else:
                    logger.warning("[Kokoro TTS] неизвестное: %r", msg)
        finally:
            try:
                await ws.close()
            except Exception:
                pass


# ─── Фабрика ─────────────────────────────────────────────────────────────────

def get_tts_provider() -> TTSProvider:
    """Создаёт KokoroTTSProvider из настроек. Требует KOKORO_TTS_URL."""
    if not settings.KOKORO_TTS_URL:
        raise RuntimeError(
            "TTS не сконфигурирован: задайте KOKORO_TTS_URL в .env"
        )
    voice = settings.KOKORO_TTS_VOICE or "af_heart"
    speed = settings.KOKORO_TTS_SPEED or 1.0
    logger.warning(
        "[TTS] url=%s voice=%s speed=%s",
        settings.KOKORO_TTS_URL, voice, speed,
    )
    return KokoroTTSProvider(
        url=settings.KOKORO_TTS_URL,
        voice=voice,
        speed=float(speed),
    )
