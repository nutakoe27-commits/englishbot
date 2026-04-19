"""
stt_providers.py — STT-провайдер (распознавание речи).

Единственный бэкенд — локальный faster-whisper (large-v3-turbo) на V100,
пробрасываемый на VPS через SSH-reverse-tunnel. WebSocket JSON-протокол.

Контракт провайдера:
    async def stream(
        stt_queue: asyncio.Queue[Optional[dict]],
    ) -> AsyncIterator[tuple[str, str]]

Очередь событий (события кладёт оркестратор в voice.py):
    {"kind": "audio", "data": bytes}  — PCM s16le 16kHz mono
    {"kind": "eou"}                    — клиент отпустил кнопку
    None                                — конец сессии

Выход:
    ("final",  text)  — финальная гипотеза распознавания
    ("eou",    "")    — подтверждение EOU, после этого генератор выходит

Одна итерация stream() покрывает ровно одну фразу push-to-talk:
от первого audio-чанка до Eou. На каждое нажатие кнопки оркестратор
создаёт новую очередь и заново вызывает stream().
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import AsyncIterator, Optional, Protocol

import websockets
from websockets.exceptions import ConnectionClosed

from .config import settings

logger = logging.getLogger(__name__)

# Аудио-параметры фиксированы по всему пайплайну (см. voice.py).
INPUT_SAMPLE_RATE = 16000


# ─── Общий контракт ──────────────────────────────────────────────────────────

class STTProvider(Protocol):
    def stream(
        self, stt_queue: "asyncio.Queue[Optional[dict]]"
    ) -> AsyncIterator[tuple[str, str]]: ...


# ─── Whisper (faster-whisper large-v3-turbo) ────────────────────────────────

class WhisperSTTProvider:
    """
    WebSocket-клиент к локальному STT-серверу на V100 (faster-whisper).

    Протокол (см. v100/stt_server.py):
      клиент → сервер:
        {"type":"config","sample_rate":16000,"language":"en"}
        {"type":"audio","data":"<base64 s16le PCM>"}
        {"type":"eou"}
      сервер → клиент:
        {"type":"ready"}
        {"type":"final","text":"..."}
        {"type":"error","message":"..."}

    Whisper выдаёт ровно один final после EOU — partial/refine нет.
    """

    def __init__(self, url: str, language: str = "en"):
        self.url = url
        self.language = language

    async def stream(
        self, stt_queue: "asyncio.Queue[Optional[dict]]"
    ) -> AsyncIterator[tuple[str, str]]:
        stats = {"audio_sent": 0, "audio_bytes": 0, "eou_sent": 0}

        try:
            ws = await websockets.connect(
                self.url,
                open_timeout=5.0,
                ping_interval=20,
                ping_timeout=20,
                max_size=4 * 1024 * 1024,
            )
        except Exception as exc:
            logger.error("[Whisper STT] не смог подключиться к %s: %s", self.url, exc)
            return

        logger.warning("[Whisper STT] WebSocket открыт: %s", self.url)

        # Насос: перекладывает события из stt_queue в WebSocket.
        # Крутится параллельно основному циклу чтения ответов.
        async def pump() -> None:
            try:
                # Отправляем конфиг первой же строкой
                await ws.send(json.dumps({
                    "type": "config",
                    "sample_rate": INPUT_SAMPLE_RATE,
                    "language": self.language,
                }))

                while True:
                    event = await stt_queue.get()
                    if event is None:
                        # Сессия завершена — ничего больше не шлём
                        return
                    kind = event.get("kind")
                    if kind == "audio":
                        data: bytes = event["data"]
                        stats["audio_sent"] += 1
                        stats["audio_bytes"] += len(data)
                        b64 = base64.b64encode(data).decode("ascii")
                        try:
                            await ws.send(json.dumps({"type": "audio", "data": b64}))
                        except ConnectionClosed:
                            return
                    elif kind == "eou":
                        stats["eou_sent"] += 1
                        logger.warning(
                            "[Whisper STT] → eou (audio: %d чанков, %d байт)",
                            stats["audio_sent"], stats["audio_bytes"],
                        )
                        try:
                            await ws.send(json.dumps({"type": "eou"}))
                        except ConnectionClosed:
                            return
                        # После EOU клиент больше ничего не шлёт в этой фразе —
                        # ждём final от сервера в основном цикле.
                        return
            except Exception as exc:
                logger.error("[Whisper STT] pump упал: %s", exc, exc_info=True)

        pump_task = asyncio.create_task(pump(), name="_whisper_stt_pump")

        try:
            # Таймаут на весь сеанс — защита от зависания, если сервер молчит.
            # После EOU на Whisper обычно < 1 с, но ставим с запасом.
            overall_deadline = None  # выставится после отправки eou
            while True:
                # Если pump уже отправил EOU, даём серверу 10 секунд на финал.
                if stats["eou_sent"] > 0 and overall_deadline is None:
                    overall_deadline = asyncio.get_event_loop().time() + 10.0

                if overall_deadline is not None:
                    remaining = overall_deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        logger.warning("[Whisper STT] таймаут ожидания final после EOU")
                        break
                    timeout = remaining
                else:
                    # Пока EOU не отправлен, ждём без таймаута — либо придёт ready,
                    # либо pump завершится сам по None.
                    timeout = None

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning("[Whisper STT] таймаут recv после EOU")
                    break
                except ConnectionClosed as exc:
                    logger.warning("[Whisper STT] WS закрыт: %s", exc)
                    break

                if isinstance(raw, bytes):
                    # Не ожидаем бинарных фреймов от сервера — игнорируем.
                    continue

                try:
                    msg = json.loads(raw)
                except Exception:
                    logger.warning("[Whisper STT] непарсимое сообщение: %r", raw[:200])
                    continue

                mtype = msg.get("type")
                if mtype == "ready":
                    logger.warning("[Whisper STT] сервер готов")
                    continue
                if mtype == "final":
                    text = (msg.get("text") or "").strip()
                    logger.warning("[Whisper STT] final: %r", text)
                    if text:
                        yield ("final", text)
                    # После final сразу эмитим eou — это сигнал оркестратору
                    # закрыть текущую фразу и перейти к LLM+TTS.
                    yield ("eou", "")
                    return
                if mtype == "error":
                    logger.error("[Whisper STT] сервер вернул ошибку: %s", msg.get("message"))
                    break
                logger.warning("[Whisper STT] неизвестное сообщение: %r", msg)
        finally:
            if not pump_task.done():
                pump_task.cancel()
                try:
                    await pump_task
                except (asyncio.CancelledError, Exception):
                    pass
            try:
                await ws.close()
            except Exception:
                pass
            logger.warning(
                "[Whisper STT] сессия завершена: audio_sent=%d (%d байт), eou_sent=%d",
                stats["audio_sent"], stats["audio_bytes"], stats["eou_sent"],
            )


# ─── Фабрика ─────────────────────────────────────────────────────────────────

def get_stt_provider(language: Optional[str] = None) -> STTProvider:
    """Создаёт WhisperSTTProvider из настроек.

    Args:
        language: язык STT для конкретной сессии. Если None — берём
                  дефолт из .env (WHISPER_STT_LANGUAGE). Пустая строка
                  = автодетект (Whisper сам определит).
    """
    if not settings.WHISPER_STT_URL:
        raise RuntimeError(
            "STT не сконфигурирован: задайте WHISPER_STT_URL в .env"
        )
    effective_lang = language if language is not None else (
        settings.WHISPER_STT_LANGUAGE or "en"
    )
    logger.warning(
        "[STT] url=%s language=%s",
        settings.WHISPER_STT_URL, effective_lang or "auto",
    )
    return WhisperSTTProvider(
        url=settings.WHISPER_STT_URL,
        language=effective_lang,
    )
