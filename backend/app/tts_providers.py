"""
tts_providers.py — абстракция над TTS-провайдерами.

Позволяет переключать TTS-бэкенд через переменную окружения TTS_PROVIDER:
  - "yandex" — Yandex SpeechKit TTS v3 (gRPC, LINEAR16_PCM 24kHz)
  - "kokoro" — локальный Kokoro-82M на V100 через SSH-reverse-tunnel,
               WebSocket JSON-протокол, PCM 24kHz

Контракт:
    async def synthesize(text: str) -> AsyncIterator[bytes]
        yield PCM s16le 24kHz mono chunks

Оба провайдера отдают одинаковый формат — бэкенд шлёт чанки в WebSocket
браузера как есть.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import AsyncIterator, Optional, Protocol

import grpc
import websockets
from websockets.exceptions import ConnectionClosed

import yandex.cloud.ai.tts.v3.tts_pb2 as tts_pb2
import yandex.cloud.ai.tts.v3.tts_service_pb2_grpc as tts_service_pb2_grpc

from .config import settings

logger = logging.getLogger(__name__)

OUTPUT_SAMPLE_RATE = 24000  # фиксировано по всему пайплайну
TTS_ENDPOINT_YANDEX = "tts.api.cloud.yandex.net:443"


# ─── Контракт ────────────────────────────────────────────────────────────────

class TTSProvider(Protocol):
    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...


# ─── Yandex SpeechKit TTS ───────────────────────────────────────────────────

class YandexTTSProvider:
    """Yandex TTS v3 StreamSynthesis — PCM 24kHz 16-bit LE."""

    def __init__(self, api_key: str, voice: str, folder_id: str):
        self.api_key = api_key
        self.voice = voice
        self.folder_id = folder_id

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        # Конфигурация по оф. примеру Yandex; не задаём model / role /
        # loudness_normalization_type — у ряда голосов это вешает стрим.
        synthesis_options = tts_pb2.SynthesisOptions(
            voice=self.voice,
            output_audio_spec=tts_pb2.AudioFormatOptions(
                raw_audio=tts_pb2.RawAudio(
                    audio_encoding=tts_pb2.RawAudio.LINEAR16_PCM,
                    sample_rate_hertz=OUTPUT_SAMPLE_RATE,
                )
            ),
        )

        async def request_iterator():
            yield tts_pb2.StreamSynthesisRequest(options=synthesis_options)
            yield tts_pb2.StreamSynthesisRequest(
                synthesis_input=tts_pb2.SynthesisInput(text=text)
            )

        credentials = grpc.ssl_channel_credentials()
        async with grpc.aio.secure_channel(TTS_ENDPOINT_YANDEX, credentials) as channel:
            stub = tts_service_pb2_grpc.SynthesizerStub(channel)
            metadata = (("authorization", f"Api-Key {self.api_key}"),)
            stream = stub.StreamSynthesis(request_iterator(), metadata=metadata)

            logger.warning("[Yandex TTS] gRPC стрим открыт")
            chunks_sent = 0
            first_chunk = True
            try:
                while True:
                    try:
                        # Жёсткий таймаут на первый ответ — не висеть.
                        response = await asyncio.wait_for(
                            stream.read(), timeout=10.0 if first_chunk else 30.0
                        )
                    except asyncio.TimeoutError:
                        logger.error(
                            "[Yandex TTS] таймаут (%s чанк). Закрываем стрим.",
                            "первый" if first_chunk else "очередной",
                        )
                        stream.cancel()
                        return
                    if response == grpc.aio.EOF:
                        break
                    first_chunk = False
                    if response.audio_chunk.data:
                        chunks_sent += 1
                        yield response.audio_chunk.data
                logger.warning("[Yandex TTS] готово, чанков: %d", chunks_sent)
            except grpc.aio.AioRpcError as exc:
                logger.error("[Yandex TTS] gRPC ошибка: code=%s details=%s",
                             exc.code(), exc.details())
                raise
            except Exception as exc:
                logger.error("[Yandex TTS] неизвестная ошибка: %s", exc, exc_info=True)
                raise


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

    Под каждый синтез открываем отдельное WS-соединение. Kokoro-сервер
    поддерживает reuse, но держать pool WS-сессий в бэкенде сложнее, чем
    платить ~5-10мс на connect — у нас single-user бот.
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
            # синтез как Yandex — сначала считает, потом режет на куски).
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
    """
    Возвращает провайдера согласно settings.TTS_PROVIDER.
    Значения: "yandex" (по умолчанию) или "kokoro".
    """
    provider = (settings.TTS_PROVIDER or "yandex").lower()

    if provider == "kokoro":
        if not settings.KOKORO_TTS_URL:
            logger.error(
                "TTS_PROVIDER=kokoro, но KOKORO_TTS_URL не задан. Откат на Yandex TTS."
            )
        else:
            voice = settings.KOKORO_TTS_VOICE or "af_heart"
            speed = settings.KOKORO_TTS_SPEED or 1.0
            logger.warning(
                "[TTS] провайдер=kokoro url=%s voice=%s speed=%s",
                settings.KOKORO_TTS_URL, voice, speed,
            )
            return KokoroTTSProvider(
                url=settings.KOKORO_TTS_URL,
                voice=voice,
                speed=float(speed),
            )

    # fallback: Yandex TTS
    if not settings.YC_API_KEY or not settings.YC_FOLDER_ID:
        raise RuntimeError(
            "Yandex TTS выбран провайдером, но YC_API_KEY / YC_FOLDER_ID не заданы"
        )
    logger.warning("[TTS] провайдер=yandex voice=%s", settings.YC_TTS_VOICE)
    return YandexTTSProvider(
        api_key=settings.YC_API_KEY,
        voice=settings.YC_TTS_VOICE,
        folder_id=settings.YC_FOLDER_ID,
    )
