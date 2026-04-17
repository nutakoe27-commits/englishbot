"""
yandex_voice.py — голосовой диалог через Yandex SpeechKit + YandexGPT.

Архитектура:
    Browser (PCM 16kHz 16-bit LE mono)
      → WebSocket /ws/voice
      → Yandex STT v3 streaming (gRPC, ExternalEouClassifier) → финалы пользователя
      → YandexGPT (HTTP) → текст ответа репетитора
      → Yandex TTS v3 streaming (gRPC, LINEAR16_PCM 24kHz) → PCM в браузер
      → Browser

End-of-utterance (EOU) управляется клиентом:
    Когда пользователь отжимает кнопку записи, фронт шлёт JSON {"type":"eou"}.
    Сервер шлёт в STT Eou-маркер. Только после этого аккумулированные финалы
    собираются в одну реплику пользователя и уходят в YandexGPT → TTS.

Почему Yandex, а не Gemini/Vertex:
    Gemini Developer API блокирует IP РФ (ошибка 1007).
    Vertex AI требует активного billing, который недоступен без зарубежной карты.
    Yandex работает с РФ без прокси и поддерживает API-Key аутентификацию.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

import grpc
import httpx
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

# Yandex Cloud API — сгенерированные protobuf stubs (пакет yandexcloud)
import yandex.cloud.ai.stt.v3.stt_pb2 as stt_pb2
import yandex.cloud.ai.stt.v3.stt_service_pb2_grpc as stt_service_pb2_grpc
import yandex.cloud.ai.tts.v3.tts_pb2 as tts_pb2
import yandex.cloud.ai.tts.v3.tts_service_pb2_grpc as tts_service_pb2_grpc

from .config import SYSTEM_PROMPT, settings

logger = logging.getLogger(__name__)

# ─── Константы ────────────────────────────────────────────────────────────────
STT_ENDPOINT = "stt.api.cloud.yandex.net:443"
TTS_ENDPOINT = "tts.api.cloud.yandex.net:443"
YANDEXGPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

# Аудио-параметры. Браузер шлёт 16kHz, воспроизводит 24kHz.
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000

# История диалога — храним несколько последних реплик для контекста YandexGPT.
MAX_HISTORY_TURNS = 6


# ─── STT: потоковое распознавание с внешним EOU ──────────────────────────────

# Типы событий в очереди stt:
#   {"kind": "audio", "data": bytes}  — PCM-чанк
#   {"kind": "eou"}                    — клиент отпустил кнопку, пора финализировать
#   None                                — закрыть стрим (session end)
#
# STT отдаёт события наверх:
#   ("final", text)            — финальная гипотеза от Yandex
#   ("refine", text)           — уточнение final_refinement (точнее, приходит позже)
#   ("eou", "")                — сервер подтвердил end-of-utterance
#
# ВАЖНО: gRPC-стрим Yandex STT закрывается после Eou. Для диалога push-to-talk
# из нескольких фраз нужно переоткрывать STT-сессию на каждую реплику.
# Этот генератор покрывает одну фразу (от начала аудио до Eou), вызывать
# его нужно заново на каждое новое нажатие кнопки.


async def _stt_stream(
    stt_queue: "asyncio.Queue[Optional[dict]]",
    api_key: str,
) -> AsyncIterator[tuple[str, str]]:
    """
    Принимает события (audio/eou) из общей очереди, шлёт их в Yandex STT v3.
    Использует ExternalEouClassifier — сервер не авто-закрывает фразу на паузах,
    финализирует только по явному Eou-маркеру от клиента.
    """

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
                # ── Внешний EOU-классификатор ──────────────────────────────
                # Выключает авто-детектор пауз на стороне Yandex.
                # Финал будет отправлен только после получения Eou от клиента.
                eou_classifier=stt_pb2.EouClassifierOptions(
                    external_classifier=stt_pb2.ExternalEouClassifier()
                ),
            )
        )

    async def request_iterator():
        # Первое сообщение — опции сессии
        yield options_request()
        while True:
            event = await stt_queue.get()
            if event is None:
                # Полный конец сессии — закрываем upstream
                return
            kind = event.get("kind")
            if kind == "audio":
                yield stt_pb2.StreamingRequest(
                    chunk=stt_pb2.AudioChunk(data=event["data"])
                )
            elif kind == "eou":
                # Явный EOU от клиента — просим Yandex финализировать фразу.
                # Пустой Eou-месседж допустим (см. stt-v3 api-ref).
                yield stt_pb2.StreamingRequest(eou=stt_pb2.Eou())
            # неизвестные kind молча игнорируем

    credentials = grpc.ssl_channel_credentials()
    async with grpc.aio.secure_channel(STT_ENDPOINT, credentials) as channel:
        stub = stt_service_pb2_grpc.RecognizerStub(channel)
        metadata = (("authorization", f"Api-Key {api_key}"),)
        stream = stub.RecognizeStreaming(request_iterator(), metadata=metadata)

        try:
            async for response in stream:
                event = response.WhichOneof("Event")
                if event == "final":
                    alts = response.final.alternatives
                    if alts and alts[0].text.strip():
                        logger.warning("[STT] final: %s", alts[0].text.strip())
                        yield ("final", alts[0].text.strip())
                elif event == "final_refinement":
                    alts = response.final_refinement.normalized_text.alternatives
                    if alts and alts[0].text.strip():
                        yield ("refine", alts[0].text.strip())
                elif event == "eou_update":
                    # Подтверждение финализации фразы от Yandex
                    yield ("eou", "")
                elif event == "status_code":
                    logger.debug(
                        "STT status: %s — %s",
                        response.status_code.code_type,
                        response.status_code.message,
                    )
        except grpc.aio.AioRpcError as exc:
            logger.error("STT gRPC ошибка: %s — %s", exc.code(), exc.details())
            raise


# ─── YandexGPT: генерация ответа репетитора ──────────────────────────────────

async def _yandex_gpt_complete(
    user_text: str,
    history: list[dict],
    api_key: str,
    folder_id: str,
) -> str:
    """
    Отправляет диалог в YandexGPT и возвращает ответ ассистента.
    history — список {"role": "user"|"assistant", "text": "..."} последних реплик.
    """
    messages = [{"role": "system", "text": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "text": user_text})

    payload = {
        "modelUri": f"gpt://{folder_id}/yandexgpt-lite/latest",
        "completionOptions": {
            "stream": False,
            "temperature": 0.6,
            "maxTokens": 200,
        },
        "messages": messages,
    }
    headers = {
        "Authorization": f"Api-Key {api_key}",
        "x-folder-id": folder_id,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(YANDEXGPT_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            logger.error("YandexGPT HTTP %s: %s", resp.status_code, resp.text)
            resp.raise_for_status()
        data = resp.json()

    # Структура ответа: result.alternatives[0].message.text
    try:
        return data["result"]["alternatives"][0]["message"]["text"].strip()
    except (KeyError, IndexError) as exc:
        logger.error("Неожиданный формат ответа YandexGPT: %s — %s", data, exc)
        return "Sorry, I didn't catch that. Could you say it again?"


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

    # Общая очередь событий для STT: audio-чанки + eou-маркеры от клиента.
    # Порядок важен: eou должен прийти после всех audio-чанков текущей фразы.
    stt_queue: asyncio.Queue[Optional[dict]] = asyncio.Queue(maxsize=512)
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
                    try:
                        await asyncio.wait_for(
                            stt_queue.put({"kind": "audio", "data": msg["bytes"]}),
                            timeout=2.0,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Очередь STT переполнена — дропаем чанк")
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
            reply = await _yandex_gpt_complete(
                user_text=text,
                history=history,
                api_key=api_key,
                folder_id=folder_id,
            )
        except Exception as exc:
            logger.error("YandexGPT сбой: %s", exc, exc_info=True)
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
        локальную (на одну фразу) и запускаем _stt_stream для каждой.
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
                    async for kind, text in _stt_stream(utter_queue, api_key):
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
                    # Если _stt_stream закончился раньше, чем pump_task, останавливаем pump
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
