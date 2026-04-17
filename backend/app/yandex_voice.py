"""
yandex_voice.py — оркестратор голосового диалога.

Архитектура:
    Browser (PCM 16kHz 16-bit LE mono)
      → WebSocket /ws/voice
      → STTProvider (yandex | whisper) → финалы пользователя
      → LLMProvider (yandex | vllm) → текст ответа репетитора
      → Yandex TTS v3 streaming (gRPC, LINEAR16_PCM 24kHz) → PCM в браузер
      → Browser

End-of-utterance (EOU) управляется клиентом:
    Когда пользователь отжимает кнопку записи, фронт шлёт JSON {"type":"eou"}.
    STT-провайдер прокидывает маркер на свой бэкенд. По финалу фраза собирается
    и уходит в LLM → TTS.

Историческая справка:
    Изначально всё шло через Yandex Cloud (сервер в РФ, Gemini/Vertex недоступны).
    Сейчас STT и LLM переехали на собственный V100 через SSH-reverse-tunnel;
    TTS пока остаётся Yandex.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

import grpc
import httpx
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

# Yandex Cloud API — сгенерированные protobuf stubs (пакет yandexcloud).
# STT-stubs теперь живут в stt_providers.py, здесь нужны только TTS.
import yandex.cloud.ai.tts.v3.tts_pb2 as tts_pb2
import yandex.cloud.ai.tts.v3.tts_service_pb2_grpc as tts_service_pb2_grpc

from .config import SYSTEM_PROMPT, settings
from .llm_providers import get_llm_provider
from .stt_providers import get_stt_provider

logger = logging.getLogger(__name__)

# ─── Константы ────────────────────────────────────────────────────────────────
TTS_ENDPOINT = "tts.api.cloud.yandex.net:443"

# Аудио-параметры. Браузер шлёт 16kHz, воспроизводит 24kHz.
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000

# История диалога — храним несколько последних реплик для контекста YandexGPT.
MAX_HISTORY_TURNS = 6


# ─── STT: вынесено в stt_providers.py ────────────────────────────────────────
# Вся логика потокового распознавания (Yandex SpeechKit + Whisper) теперь живёт
# в stt_providers.py — get_stt_provider() выбирает бэкенд по settings.STT_PROVIDER.

# ─── LLM: генерация ответа репетитора ─────────────────────────────────────
# Вся логика вынесена в llm_providers.py — get_llm_provider() выбирает
# YandexGPT или локальный vLLM по settings.LLM_PROVIDER.


# ─── TTS: потоковый синтез речи ──────────────────────────────────────────────

async def _tts_stream(
    text: str,
    api_key: str,
    voice: str,
    folder_id: str,
) -> AsyncIterator[bytes]:
    """
    Синтезирует текст через Yandex TTS v3 StreamSynthesis.
    Возвращает PCM 24kHz 16-bit LE chunks для отправки в браузер.
    """

    # Конфигурация по образцу оф. примера (aistudio.yandex.ru/docs/en/speechkit/tts/api/tts-streaming).
    # Не задаём model / role / loudness_normalization_type / x-folder-id — оно не требуется
    # и у ряда голосов вешает стрим.
    synthesis_options = tts_pb2.SynthesisOptions(
        voice=voice,
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
    async with grpc.aio.secure_channel(TTS_ENDPOINT, credentials) as channel:
        stub = tts_service_pb2_grpc.SynthesizerStub(channel)
        metadata = (
            ("authorization", f"Api-Key {api_key}"),
        )
        stream = stub.StreamSynthesis(request_iterator(), metadata=metadata)

        logger.warning("[TTS] gRPC стрим открыт, ждём чанки…")
        chunks_sent = 0
        first_chunk = True
        try:
            while True:
                try:
                    # Жёсткий таймаут на первый ответ сервера, чтобы не висеть.
                    response = await asyncio.wait_for(
                        stream.read(), timeout=10.0 if first_chunk else 30.0
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        "[TTS] таймаут ожидания чанка (%s). Закрываем стрим.",
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
            logger.warning("[TTS] gRPC стрим закрылся штатно, чанков: %d", chunks_sent)
        except grpc.aio.AioRpcError as exc:
            logger.error(
                "[TTS] gRPC ошибка: code=%s details=%s",
                exc.code(), exc.details(),
            )
            raise
        except Exception as exc:
            logger.error("[TTS] неизвестная ошибка: %s", exc, exc_info=True)
            raise


# ─── Хелпер: синтез + отправка в WebSocket ───────────────────────────

async def _send_tts_to_ws(
    websocket: WebSocket, text: str, api_key: str, voice: str, folder_id: str
) -> None:
    """Синтезирует текст и стримит PCM-чанки в WebSocket."""
    chunks_sent = 0
    bytes_sent = 0
    logger.warning("[TTS] старт синтеза: voice=%s text=%r", voice, text[:80])
    try:
        async for pcm_chunk in _tts_stream(text, api_key, voice, folder_id):
            if websocket.client_state != WebSocketState.CONNECTED:
                logger.warning("[TTS] WS не в CONNECTED (сост. %s), прерываем отправку",
                    websocket.client_state)
                return
            await websocket.send_bytes(pcm_chunk)
            chunks_sent += 1
            bytes_sent += len(pcm_chunk)
        logger.warning("[TTS] готово: %d чанков, %d байт отправлено", chunks_sent, bytes_sent)
    except Exception as exc:
        logger.error("[TTS] ошибка отправки (после %d чанков): %s", chunks_sent, exc, exc_info=True)


# ─── Оркестратор WebSocket-сессии ─────────────────────────────────────────────

async def run_yandex_session(websocket: WebSocket) -> None:
    """
    Основной цикл голосового диалога.

    Протокол WebSocket:
      Клиент → PCM 16kHz 16-bit LE mono (binary frames)
      Сервер → JSON {"type":"text","role":"user"|"tutor","text":"..."}
      Сервер → PCM 24kHz 16-bit LE mono (binary frames)
    """
    api_key = settings.YC_API_KEY
    folder_id = settings.YC_FOLDER_ID
    voice = settings.YC_TTS_VOICE

    if not api_key or not folder_id:
        logger.error("YC_API_KEY или YC_FOLDER_ID не заданы")
        await websocket.close(code=1011, reason="Server misconfiguration: Yandex credentials missing")
        return

    logger.warning("[Yandex] run_yandex_session старт (folder=%s, voice=%s)", folder_id, voice)

    # LLM-провайдер выбирается по settings.LLM_PROVIDER (yandex | vllm).
    # Создаём один раз на сессию — он лёгкий и содержит только URL/токены.
    try:
        llm = get_llm_provider()
    except Exception as exc:
        logger.error("Не удалось создать LLM-провайдера: %s", exc)
        await websocket.close(code=1011, reason="Server misconfiguration: LLM provider")
        return

    # STT-провайдер — по settings.STT_PROVIDER (yandex | whisper).
    # Тоже лёгкий объект: URL + язык или API-ключ, без сетевых соединений.
    try:
        stt = get_stt_provider()
    except Exception as exc:
        logger.error("Не удалось создать STT-провайдера: %s", exc)
        await websocket.close(code=1011, reason="Server misconfiguration: STT provider")
        return

    # Общая очередь событий для STT: audio-чанки + eou-маркеры от клиента.
    # Порядок важен: eou должен прийти после всех audio-чанков текущей фразы.
    # Каждый чанк = 20мс PCM @ 16kHz (= 50 чанков/сек). 4096 ≈ 80 сек запаса.
    stt_queue: asyncio.Queue[Optional[dict]] = asyncio.Queue(maxsize=4096)
    stt_dropped_audio = 0  # счётчик дропнутых аудио-чанков (диагностика)
    history: list[dict] = []
    # Буфер финалов, пришедших между EOU — соберём их в одну реплику.
    pending_finals: list[str] = []

    # ─── Приветственное сообщение репетитора ───────────────────────
    # Сразу после подключения приглашаем пользователя заговорить — чтобы было понятно,
    # что бот готов, и сессия не выглядела «зависшей» при молчании.
    greeting = "Hi! I'm your English tutor. What would you like to talk about today?"
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({
                "type": "text",
                "role": "tutor",
                "text": greeting,
            })
            # Озвучиваем приветствие в фоне — не блокируем STT-пайплайн
            asyncio.create_task(_send_tts_to_ws(websocket, greeting, api_key, voice, folder_id))
            # Добавляем приветствие в историю, чтобы GPT видел контекст
            history.append({"role": "assistant", "text": greeting})
    except Exception as exc:
        logger.warning("Не удалось отправить приветствие: %s", exc)

    async def receive_from_client() -> None:
        """Фоновая задача: читаем сообщения от клиента.

        Бинарные фреймы → PCM-чанки (kind=audio).
        JSON-фреймы: {"type":"eou"} → явный end-of-utterance (kind=eou).
        """
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if "bytes" in msg and msg["bytes"]:
                    nonlocal stt_dropped_audio
                    audio_event = {"kind": "audio", "data": msg["bytes"]}
                    try:
                        stt_queue.put_nowait(audio_event)
                    except asyncio.QueueFull:
                        # Drop-oldest: освобождаем место сбрасывая самый старый audio-чанк.
                        # EOU/None класть в очередь может только эта же корутина — и только
                        # после audio. Значит на голове очереди всегда audio, безопасно.
                        try:
                            dropped = stt_queue.get_nowait()
                            if dropped is None or dropped.get("kind") != "audio":
                                # Защита от гонок: если наверху всё-таки не audio — вернём.
                                await stt_queue.put(dropped)
                            else:
                                stt_dropped_audio += 1
                                stt_queue.put_nowait(audio_event)
                        except asyncio.QueueEmpty:
                            pass
                        if stt_dropped_audio and stt_dropped_audio % 50 == 1:
                            logger.warning(
                                "[STT] дропнуто audio-чанков: %d (queue=%d/%d)",
                                stt_dropped_audio,
                                stt_queue.qsize(),
                                stt_queue.maxsize,
                            )
                elif "text" in msg and msg["text"]:
                    # Контрольный JSON-фрейм от клиента
                    try:
                        ctrl = json.loads(msg["text"])
                    except Exception:
                        logger.debug("Непарсимый text-фрейм: %r", msg["text"][:120])
                        continue
                    if ctrl.get("type") == "eou":
                        logger.warning("[WS] EOU от клиента")
                        await stt_queue.put({"kind": "eou"})
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.error("Ошибка в receive_from_client: %s", exc, exc_info=True)
        finally:
            # Сигнал полного завершения сессии для STT
            await stt_queue.put(None)

    async def send_tts(text: str) -> None:
        """Синтезирует текст и отправляет PCM в WebSocket."""
        await _send_tts_to_ws(websocket, text, api_key, voice, folder_id)

    async def handle_user_utterance(text: str) -> None:
        """Для готовой реплики пользователя: GPT в ответ и озвучка."""
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({
                "type": "text",
                "role": "user",
                "text": text,
            })

        try:
            reply = await llm.complete(user_text=text, history=history)
        except Exception as exc:
            logger.error("LLM сбой (%s): %s", type(llm).__name__, exc, exc_info=True)
            reply = "I'm having trouble right now. Let's try again in a moment."

        history.append({"role": "user", "text": text})
        history.append({"role": "assistant", "text": reply})
        if len(history) > MAX_HISTORY_TURNS * 2:
            del history[: len(history) - MAX_HISTORY_TURNS * 2]

        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({
                "type": "text",
                "role": "tutor",
                "text": reply,
            })

        # Синтез и стриминг PCM 24kHz в браузер
        await send_tts(reply)

    async def process_stt() -> None:
        """
        Внешний цикл диалога: на каждую фразу открываем новую STT-сессию.

        Yandex STT закрывает gRPC-стрим после Eou, поэтому нельзя держать
        один поток на всю WS-сессию. Перекладываем события из общей очереди в
        локальную (на одну фразу) и запускаем stt.stream() для каждой.
        """
        try:
            while True:
                # Ждём первое аудио-событие текущей фразы.
                # None в очереди значит WS закрылся — выходим.
                first = await stt_queue.get()
                if first is None:
                    logger.info("[STT] WS закрыт, выходим из process_stt")
                    return
                if first.get("kind") == "eou":
                    # EOU без начавшейся фразы (но трек был mute'нут) — игнорируем.
                    logger.info("[STT] EOU без аудио — пропускаем")
                    continue

                # Локальная очередь на одну фразу. Сразу кладём в неё первый чанк.
                utter_queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()
                await utter_queue.put(first)
                pending_finals.clear()
                eou_seen = False

                async def pump_to_utter() -> None:
                    """Перекладываем события из глобальной очереди в локальную, пока не Eou/None."""
                    nonlocal eou_seen
                    while True:
                        ev = await stt_queue.get()
                        if ev is None:
                            # Конец всей сессии
                            await utter_queue.put(None)
                            return
                        await utter_queue.put(ev)
                        if ev.get("kind") == "eou":
                            eou_seen = True
                            return

                pump_task = asyncio.create_task(pump_to_utter(), name="pump_to_utter")

                try:
                    async for kind, text in stt.stream(utter_queue):
                        if kind == "final" and text:
                            pending_finals.append(text)
                        elif kind == "refine" and text:
                            if pending_finals:
                                pending_finals[-1] = text
                            else:
                                pending_finals.append(text)
                        elif kind == "eou":
                            # Yandex подтвердил EOU — стрим сейчас закроется
                            pass
                except Exception as exc:
                    logger.error("[STT] stream error: %s", exc, exc_info=True)
                finally:
                    # Если stt.stream закончился раньше, чем pump_task, останавливаем pump
                    if not pump_task.done():
                        pump_task.cancel()
                        try:
                            await pump_task
                        except (asyncio.CancelledError, Exception):
                            pass

                utterance = " ".join(s.strip() for s in pending_finals if s.strip()).strip()
                pending_finals.clear()
                if not utterance:
                    logger.warning("[STT] фраза пустая после EOU — пропускаем")
                    if not eou_seen:
                        # pump завершился по None (WS закрыт) — выходим из внешнего цикла
                        return
                    continue

                logger.warning("[STT] user utterance: %s", utterance)
                try:
                    await handle_user_utterance(utterance)
                except Exception as exc:
                    logger.error("[pipeline] handle_user_utterance упал: %s", exc, exc_info=True)

                if not eou_seen:
                    # pump вышел по None (WS закрылся) — больше фраз не будет
                    return
        except Exception as exc:
            logger.error("Ошибка в process_stt: %s", exc, exc_info=True)

    # Запускаем две корутины параллельно
    recv_task = asyncio.create_task(receive_from_client(), name="recv_from_client")
    proc_task = asyncio.create_task(process_stt(), name="process_stt")

    try:
        # Ждём, пока любая из задач не завершится (обычно — disconnect)
        done, pending = await asyncio.wait(
            {recv_task, proc_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        # Даём им завершиться корректно
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        logger.warning("[Yandex] сессия завершена для %s", websocket.client)
