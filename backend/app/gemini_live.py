"""
gemini_live.py — обёртка над Google Gemini Live API через Vertex AI.

Функция run_gemini_session() открывает двустороннюю аудио-сессию:
  Browser  ──(PCM 16kHz)──►  backend  ──►  Gemini Live (Vertex AI)
  Browser  ◄─(PCM 24kHz)──  backend  ◄──  Gemini Live (Vertex AI)

Аудио-форматы:
  Входящее (от браузера): PCM 16-bit 16 kHz mono, little-endian
  Исходящее (от Gemini):  PCM 16-bit 24 kHz mono, little-endian

Почему Vertex AI, а не Gemini Developer API:
  Gemini Developer API (AI Studio ключи) блокирует российские IP с ошибкой
  1007 "User location is not supported". Vertex AI использует стандартную
  Google Cloud авторизацию через service account и не имеет такой блокировки
  для API-ключей. Аутентификация — по файлу JSON service account из
  GOOGLE_APPLICATION_CREDENTIALS.
"""

import asyncio
import logging
from typing import NoReturn

from fastapi import WebSocket
from fastapi.websockets import WebSocketState
from google import genai
from google.genai import types

from .config import settings, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# MIME-тип входящего аудио, требуемый Gemini Live API
_INPUT_MIME_TYPE = "audio/pcm;rate=16000"


def _build_live_config() -> dict:
    """
    Собирает конфигурацию Live-сессии.

    Возвращаем dict вместо types.LiveConnectConfig, потому что:
    - в старых версиях google-genai (0.3.x) класс AudioTranscriptionConfig
      отсутствует, а пустой dict принимается сервером как валидный конфиг
      транскрипции (этот тип "has no fields" согласно API reference).
    - SDK принимает config как dict или как LiveConnectConfig.
    """
    return {
        "response_modalities": ["AUDIO"],
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {
                    "voice_name": settings.GEMINI_VOICE,
                }
            }
        },
        "system_instruction": {
            "parts": [{"text": SYSTEM_PROMPT}],
            "role": "user",
        },
        # Включаем транскрипции для отображения в диалог-логе.
        # Пустой dict = AudioTranscriptionConfig без полей.
        "input_audio_transcription": {},
        "output_audio_transcription": {},
    }


async def _send_audio_to_gemini(
    client_ws: WebSocket,
    session: "genai.AsyncLiveSession",  # type: ignore[name-defined]
) -> NoReturn:
    """
    Задача A: читает бинарные фреймы от браузера и пересылает в Gemini.

    Браузер присылает PCM 16-bit 16kHz mono в виде двоичных WebSocket-сообщений.
    """
    while True:
        try:
            data: bytes = await client_ws.receive_bytes()
        except Exception:
            # Соединение закрыто или ошибка — пробрасываем для отмены TaskGroup
            raise

        if not data:
            continue

        try:
            await session.send_realtime_input(
                audio=types.Blob(data=data, mime_type=_INPUT_MIME_TYPE)
            )
        except Exception as exc:
            logger.warning("Ошибка при отправке аудио в Gemini: %s", exc)
            raise


async def _receive_from_gemini(
    client_ws: WebSocket,
    session: "genai.AsyncLiveSession",  # type: ignore[name-defined]
) -> NoReturn:
    """
    Задача B: получает ответы от Gemini и пересылает в браузер.

    Аудио → двоичные WS-фреймы (PCM 24 kHz).
    Текст / транскрипции → JSON-фреймы {type, role, text}.
    """
    async for message in session.receive():
        if client_ws.client_state != WebSocketState.CONNECTED:
            break

        server_content = message.server_content
        if server_content is None:
            continue

        # ── Аудио-части ────────────────────────────────────────────────────
        model_turn = server_content.model_turn
        if model_turn and model_turn.parts:
            for part in model_turn.parts:
                if part.inline_data and part.inline_data.data:
                    try:
                        await client_ws.send_bytes(part.inline_data.data)
                    except Exception as exc:
                        logger.warning("Ошибка при отправке аудио клиенту: %s", exc)
                        raise

                if part.text:
                    try:
                        await client_ws.send_json(
                            {"type": "text", "role": "tutor", "text": part.text}
                        )
                    except Exception as exc:
                        logger.warning("Ошибка при отправке текста клиенту: %s", exc)
                        raise

        # ── Транскрипция входящего аудио (речь пользователя) ──────────────
        input_transcript = server_content.input_transcription
        if input_transcript and input_transcript.text:
            try:
                await client_ws.send_json(
                    {
                        "type": "text",
                        "role": "user",
                        "text": input_transcript.text,
                    }
                )
            except Exception as exc:
                logger.warning("Ошибка при отправке транскрипции клиенту: %s", exc)
                raise

        # ── Транскрипция исходящего аудио (речь модели) ───────────────────
        output_transcript = server_content.output_transcription
        if output_transcript and output_transcript.text:
            try:
                await client_ws.send_json(
                    {
                        "type": "text",
                        "role": "tutor",
                        "text": output_transcript.text,
                    }
                )
            except Exception as exc:
                logger.warning("Ошибка при отправке транскрипции модели клиенту: %s", exc)
                raise


def _make_vertex_client() -> "genai.Client":
    """
    Создаёт genai.Client для Vertex AI.

    Требует:
      - GOOGLE_CLOUD_PROJECT (project_id)
      - GOOGLE_CLOUD_LOCATION (например us-central1)
      - GOOGLE_APPLICATION_CREDENTIALS (путь к JSON service account)
        ЛИБО credentials уже в окружении контейнера (Workload Identity и т.п.)
    """
    if not settings.GOOGLE_CLOUD_PROJECT:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT не задан в .env — невозможно создать Vertex AI клиент"
        )

    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


async def run_gemini_session(client_ws: WebSocket) -> None:
    """
    Запускает двустороннюю аудио-сессию Gemini Live для одного WebSocket-соединения.

    Параметры:
        client_ws   — принятый WebSocket-соединение FastAPI (уже accept()'ed)

    Исключения перехватываются внутри; после завершения (разрыв со стороны
    клиента или Gemini) функция возвращает управление.
    """
    try:
        client = _make_vertex_client()
    except Exception as exc:
        logger.error("Не удалось создать Vertex AI клиент: %s", exc)
        await client_ws.close(code=1011, reason="Server misconfiguration")
        return

    config = _build_live_config()

    logger.info(
        "Открываем Vertex AI Live сессию: project=%s location=%s model=%s voice=%s",
        settings.GOOGLE_CLOUD_PROJECT,
        settings.GOOGLE_CLOUD_LOCATION,
        settings.GEMINI_MODEL,
        settings.GEMINI_VOICE,
    )

    try:
        async with client.aio.live.connect(
            model=settings.GEMINI_MODEL, config=config
        ) as session:
            logger.info("Gemini Live сессия установлена")

            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(
                        _send_audio_to_gemini(client_ws, session),
                        name="send_audio",
                    )
                    tg.create_task(
                        _receive_from_gemini(client_ws, session),
                        name="receive_audio",
                    )
            except* (asyncio.CancelledError, Exception) as eg:
                # TaskGroup завершается при первом исключении в любой задаче.
                # Это нормальное поведение при разрыве соединения.
                for exc in eg.exceptions:
                    if not isinstance(exc, asyncio.CancelledError):
                        logger.debug(
                            "Задача завершилась с ошибкой (ожидаемо при разрыве): %s: %s",
                            type(exc).__name__,
                            exc,
                        )

    except asyncio.CancelledError:
        logger.info("Gemini Live сессия отменена")
    except Exception as exc:
        logger.error("Неожиданная ошибка в Gemini Live сессии: %s", exc, exc_info=True)
    finally:
        logger.info("Gemini Live сессия завершена")
        # Пытаемся закрыть WebSocket, если он ещё открыт
        if client_ws.client_state == WebSocketState.CONNECTED:
            try:
                await client_ws.close(code=1000)
            except Exception:
                pass
