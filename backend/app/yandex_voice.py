"""
yandex_voice.py — голосовой диалог через Yandex SpeechKit + YandexGPT.

Архитектура:
    Browser (PCM 16kHz 16-bit LE mono)
      → WebSocket /ws/voice
      → Yandex STT v3 streaming (gRPC) → текст пользователя (final)
      → YandexGPT (HTTP) → текст ответа репетитора
      → Yandex TTS v3 streaming (gRPC) → PCM 24kHz 16-bit LE mono
      → Browser

Почему Yandex, а не Gemini/Vertex:
    Gemini Developer API блокирует IP РФ (ошибка 1007).
    Vertex AI требует активного billing, который недоступен без зарубежной карты.
    Yandex работает с РФ без прокси и поддерживает API-Key аутентификацию.
"""

import asyncio
import logging
from typing import AsyncIterator, Optional

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


# ─── STT: потоковое распознавание ─────────────────────────────────────────────

async def _stt_stream(
    audio_queue: "asyncio.Queue[Optional[bytes]]",
    api_key: str,
) -> AsyncIterator[str]:
    """
    Принимает PCM-чанки из очереди, шлёт их в Yandex STT v3 по gRPC.
    Возвращает финальные фразы (str) по мере завершения утверждений.
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
                )
            )
        )

    async def request_iterator():
        # Первое сообщение — опции сессии
        yield options_request()
        while True:
            chunk = await audio_queue.get()
            if chunk is None:
                # Маркер конца аудио
                return
            yield stt_pb2.StreamingRequest(chunk=stt_pb2.AudioChunk(data=chunk))

    # aio gRPC канал
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
                        yield alts[0].text.strip()
                elif event == "final_refinement":
                    # Нормализованный текст — обычно точнее, но приходит после final
                    alts = response.final_refinement.normalized_text.alternatives
                    if alts and alts[0].text.strip():
                        # Заменяем предыдущий final более точным вариантом
                        yield "__REFINE__" + alts[0].text.strip()
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
) -> AsyncIterator[bytes]:
    """
    Синтезирует текст через Yandex TTS v3 StreamSynthesis.
    Возвращает PCM 24kHz 16-bit LE chunks для отправки в браузер.
    """

    async def request_iterator():
        # Первое сообщение — опции
        yield tts_pb2.StreamSynthesisRequest(
            options=tts_pb2.SynthesisOptions(
                voice=voice,
                output_audio_spec=tts_pb2.AudioFormatOptions(
                    raw_audio=tts_pb2.RawAudio(
                        audio_encoding=tts_pb2.RawAudio.LINEAR16_PCM,
                        sample_rate_hertz=OUTPUT_SAMPLE_RATE,
                    )
                ),
                loudness_normalization_type=tts_pb2.SynthesisOptions.LUFS,
            )
        )
        # Второе — текст для синтеза
        yield tts_pb2.StreamSynthesisRequest(
            synthesis_input=tts_pb2.SynthesisInput(text=text)
        )

    credentials = grpc.ssl_channel_credentials()
    async with grpc.aio.secure_channel(TTS_ENDPOINT, credentials) as channel:
        stub = tts_service_pb2_grpc.SynthesizerStub(channel)
        metadata = (("authorization", f"Api-Key {api_key}"),)
        stream = stub.StreamSynthesis(request_iterator(), metadata=metadata)

        try:
            async for response in stream:
                if response.audio_chunk.data:
                    yield response.audio_chunk.data
        except grpc.aio.AioRpcError as exc:
            logger.error("TTS gRPC ошибка: %s — %s", exc.code(), exc.details())
            raise


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

    audio_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=256)
    history: list[dict] = []
    last_final: str = ""

    async def receive_audio() -> None:
        """Фоновая задача: читаем PCM из WebSocket и кладём в очередь STT."""
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if "bytes" in msg and msg["bytes"]:
                    try:
                        await asyncio.wait_for(audio_queue.put(msg["bytes"]), timeout=2.0)
                    except asyncio.TimeoutError:
                        logger.warning("Очередь аудио переполнена — дропаем чанк")
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.error("Ошибка в receive_audio: %s", exc, exc_info=True)
        finally:
            # Сигнал конца аудио для STT
            await audio_queue.put(None)

    async def send_tts(text: str) -> None:
        """Синтезирует текст и отправляет PCM в WebSocket."""
        try:
            async for pcm_chunk in _tts_stream(text, api_key, voice):
                if websocket.client_state != WebSocketState.CONNECTED:
                    return
                await websocket.send_bytes(pcm_chunk)
        except Exception as exc:
            logger.error("Ошибка отправки TTS: %s", exc, exc_info=True)

    async def process_stt() -> None:
        """Главный цикл: STT → YandexGPT → TTS."""
        nonlocal last_final
        try:
            async for recognized in _stt_stream(audio_queue, api_key):
                # final_refinement приходит после final и уточняет текст
                if recognized.startswith("__REFINE__"):
                    # Мы уже обработали first final, пропускаем рефайн
                    # (чтобы не отвечать дважды)
                    continue

                text = recognized.strip()
                if not text or text == last_final:
                    continue
                last_final = text

                # Показываем пользователю, что он сказал
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_json({
                        "type": "text",
                        "role": "user",
                        "text": text,
                    })

                # Запрос к YandexGPT
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

                # Обновляем историю
                history.append({"role": "user", "text": text})
                history.append({"role": "assistant", "text": reply})
                # Ограничиваем длину истории
                if len(history) > MAX_HISTORY_TURNS * 2:
                    del history[: len(history) - MAX_HISTORY_TURNS * 2]

                # Отправляем текст ответа в UI
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_json({
                        "type": "text",
                        "role": "tutor",
                        "text": reply,
                    })

                # Синтез и стриминг аудио
                await send_tts(reply)

        except Exception as exc:
            logger.error("Ошибка в process_stt: %s", exc, exc_info=True)

    # Запускаем две корутины параллельно
    recv_task = asyncio.create_task(receive_audio(), name="recv_audio")
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
        logger.info("Yandex-сессия завершена для %s", websocket.client)
