"""
stt_providers.py — абстракция над STT-провайдерами.

Позволяет переключать STT-бэкенд через переменную окружения STT_PROVIDER:
  - "yandex"  — Yandex SpeechKit STT v3 (gRPC, ExternalEouClassifier)
  - "whisper" — локальный faster-whisper (large-v3-turbo) на V100 через
                SSH-reverse-туннель, WebSocket JSON-протокол

Контракт провайдера:
    async def stream(
        stt_queue: asyncio.Queue[Optional[dict]],
    ) -> AsyncIterator[tuple[str, str]]

Очередь событий (события кладёт оркестратор в yandex_voice.py):
    {"kind": "audio", "data": bytes}  — PCM s16le 16kHz mono
    {"kind": "eou"}                    — клиент отпустил кнопку
    None                                — конец сессии

Выход:
    ("final",  text)  — финальная гипотеза распознавания
    ("refine", text)  — уточнение (только Yandex; Whisper не эмитит)
    ("eou",    "")    — подтверждение EOU сервером, после этого генератор выходит

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

import grpc
import websockets
from websockets.exceptions import ConnectionClosed

import yandex.cloud.ai.stt.v3.stt_pb2 as stt_pb2
import yandex.cloud.ai.stt.v3.stt_service_pb2_grpc as stt_service_pb2_grpc

from .config import settings

logger = logging.getLogger(__name__)

# Аудио-параметры фиксированы по всему пайплайну (см. yandex_voice.py).
INPUT_SAMPLE_RATE = 16000

# Endpoint Yandex STT
STT_ENDPOINT = "stt.api.cloud.yandex.net:443"


# ─── Общий контракт ──────────────────────────────────────────────────────────

class STTProvider(Protocol):
    def stream(
        self, stt_queue: "asyncio.Queue[Optional[dict]]"
    ) -> AsyncIterator[tuple[str, str]]: ...


# ─── Yandex SpeechKit STT ───────────────────────────────────────────────────

class YandexSTTProvider:
    """
    Потоковое распознавание через Yandex SpeechKit STT v3 (gRPC).

    Использует ExternalEouClassifier — сервер не детектит паузы автоматически,
    ждёт явный Eou-маркер от нас. Это нужно для push-to-talk: пауза ≠ конец фразы.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def stream(
        self, stt_queue: "asyncio.Queue[Optional[dict]]"
    ) -> AsyncIterator[tuple[str, str]]:
        api_key = self.api_key

        def options_request() -> stt_pb2.StreamingRequest:
            return stt_pb2.StreamingRequest(
                session_options=stt_pb2.StreamingOptions(
                    recognition_model=stt_pb2.RecognitionModelOptions(
                        audio_format=stt_pb2.AudioFormatOptions(
                            raw_audio=stt_pb2.RawAudio(
                                audio_encoding=stt_pb2.RawAudio.LINEAR16_PCM,
                                sample_rate_hertz=INPUT_SAMPLE_RATE,
                                audio_channel_count=1,
                            )
                        ),
                        text_normalization=stt_pb2.TextNormalizationOptions(
                            text_normalization=stt_pb2.TextNormalizationOptions.TEXT_NORMALIZATION_ENABLED,
                            profanity_filter=False,
                            literature_text=False,
                        ),
                        language_restriction=stt_pb2.LanguageRestrictionOptions(
                            restriction_type=stt_pb2.LanguageRestrictionOptions.WHITELIST,
                            language_code=["en-US", "ru-RU"],
                        ),
                        audio_processing_type=stt_pb2.RecognitionModelOptions.REAL_TIME,
                    ),
                    eou_classifier=stt_pb2.EouClassifierOptions(
                        external_classifier=stt_pb2.ExternalEouClassifier()
                    ),
                )
            )

        stats = {"audio_sent": 0, "audio_bytes": 0, "eou_sent": 0}
        eou_sent_event = asyncio.Event()

        async def request_iterator():
            yield options_request()
            while True:
                event = await stt_queue.get()
                if event is None:
                    return
                kind = event.get("kind")
                if kind == "audio":
                    data = event["data"]
                    stats["audio_sent"] += 1
                    stats["audio_bytes"] += len(data)
                    yield stt_pb2.StreamingRequest(chunk=stt_pb2.AudioChunk(data=data))
                elif kind == "eou":
                    stats["eou_sent"] += 1
                    logger.warning(
                        "[Yandex STT] → Eou() (аудио отправлено: %d чанков, %d байт)",
                        stats["audio_sent"], stats["audio_bytes"],
                    )
                    eou_sent_event.set()
                    yield stt_pb2.StreamingRequest(eou=stt_pb2.Eou())

        credentials = grpc.ssl_channel_credentials()
        async with grpc.aio.secure_channel(STT_ENDPOINT, credentials) as channel:
            stub = stt_service_pb2_grpc.RecognizerStub(channel)
            metadata = (("authorization", f"Api-Key {api_key}"),)
            grpc_stream = stub.RecognizeStreaming(request_iterator(), metadata=metadata)
            logger.warning("[Yandex STT] gRPC стрим открыт, ждём события…")

            partial_count = 0
            eou_received = False
            out_queue: asyncio.Queue = asyncio.Queue()

            async def reader():
                nonlocal partial_count, eou_received
                try:
                    async for response in grpc_stream:
                        event_type = response.WhichOneof("Event")
                        if event_type == "partial":
                            alts = response.partial.alternatives
                            txt = alts[0].text.strip() if alts else ""
                            partial_count += 1
                            if partial_count <= 3 or partial_count % 5 == 0:
                                logger.warning("[Yandex STT] partial #%d: %r", partial_count, txt)
                        elif event_type == "final":
                            alts = response.final.alternatives
                            txt = alts[0].text.strip() if alts else ""
                            logger.warning("[Yandex STT] final: %r", txt)
                            if txt:
                                await out_queue.put(("final", txt))
                        elif event_type == "final_refinement":
                            alts = response.final_refinement.normalized_text.alternatives
                            txt = alts[0].text.strip() if alts else ""
                            if txt:
                                logger.warning("[Yandex STT] refine: %r", txt)
                                await out_queue.put(("refine", txt))
                        elif event_type == "eou_update":
                            logger.warning("[Yandex STT] eou_update от сервера")
                            eou_received = True
                            await out_queue.put(("eou", ""))
                            return
                        elif event_type == "status_code":
                            logger.warning(
                                "[Yandex STT] status: code=%s msg=%s",
                                response.status_code.code_type,
                                response.status_code.message,
                            )
                        else:
                            logger.warning("[Yandex STT] другое событие: %s", event_type)
                    logger.warning("[Yandex STT] gRPC стрим закрыт сервером")
                except grpc.aio.AioRpcError as exc:
                    logger.error("[Yandex STT] gRPC ошибка: %s — %s", exc.code(), exc.details())
                except Exception as exc:
                    logger.error("[Yandex STT] чтение упало: %s", exc, exc_info=True)
                finally:
                    await out_queue.put(None)

            reader_task = asyncio.create_task(reader(), name="_yandex_stt_reader")

            try:
                while True:
                    timeout = 5.0 if eou_sent_event.is_set() else None
                    try:
                        item = await asyncio.wait_for(out_queue.get(), timeout=timeout)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "[Yandex STT] таймаут: сервер не ответил на Eou() за 5с. Закрываем стрим."
                        )
                        break
                    if item is None:
                        break
                    yield item
                    if item[0] == "eou":
                        break
            finally:
                if not reader_task.done():
                    reader_task.cancel()
                    try:
                        await reader_task
                    except (asyncio.CancelledError, Exception):
                        pass
                logger.warning(
                    "[Yandex STT] сессия завершена: audio_sent=%d (%d байт), partial=%d, eou_received=%s",
                    stats["audio_sent"], stats["audio_bytes"], partial_count, eou_received,
                )


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

def get_stt_provider() -> STTProvider:
    """
    Возвращает провайдера согласно settings.STT_PROVIDER.
    Значения: "yandex" (по умолчанию) или "whisper".
    """
    provider = (settings.STT_PROVIDER or "yandex").lower()

    if provider == "whisper":
        if not settings.WHISPER_STT_URL:
            logger.error(
                "STT_PROVIDER=whisper, но WHISPER_STT_URL не задан. Откат на Yandex STT."
            )
        else:
            logger.warning(
                "[STT] провайдер=whisper url=%s language=%s",
                settings.WHISPER_STT_URL, settings.WHISPER_STT_LANGUAGE,
            )
            return WhisperSTTProvider(
                url=settings.WHISPER_STT_URL,
                language=settings.WHISPER_STT_LANGUAGE or "en",
            )

    # fallback: Yandex STT
    if not settings.YC_API_KEY:
        raise RuntimeError("Yandex STT выбран провайдером, но YC_API_KEY не задан")
    logger.warning("[STT] провайдер=yandex")
    return YandexSTTProvider(api_key=settings.YC_API_KEY)
