"""
gemini_live.py — обёртка над Google Gemini Live API.

Функция run_gemini_session() открывает двустороннюю аудио-сессию:
  Browser  ──(PCM 16kHz)──►  backend  ──►  Gemini Live
  Browser  ◄─(PCM 24kHz)──  backend  ◄──  Gemini Live

Аудио-форматы:
  Входящее (от браузера): PCM 16-bit 16 kHz mono, little-endian
  Исходящее (от Gemini):  PCM 16-bit 24 kHz mono, little-endian
"""

import asyncio
import logging
import os
from typing import NoReturn

from fastapi import WebSocket
from fastapi.websockets import WebSocketState
from google import genai
from google.genai import types

from .config import settings, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# --- Настройка SOCKS5/HTTPS-прокси для обхода гео-блокировки Gemini ---
#
# Gemini API блокирует IP из РФ (ошибка 1007 "User location is not supported").
# Обходим через US-прокси. Для обычных HTTP-запросов (httpx) достаточно
# переменных окружения HTTPS_PROXY/HTTP_PROXY. Но google-genai для Live API
# использует библиотеку websockets, которая НЕ читает env-переменные для
# SOCKS5-прокси при wss://-соединениях.
#
# Решение — monkey-patch: подменяем `ws_connect` в модуле google.genai.live
# на версию из библиотеки `websockets_proxy`, которая умеет ходить через
# SOCKS5/HTTP-прокси.
_proxy_url = (
    os.environ.get("HTTPS_PROXY")
    or os.environ.get("https_proxy")
    or os.environ.get("HTTP_PROXY")
    or os.environ.get("http_proxy")
)

if _proxy_url:
    try:
        from websockets_proxy import Proxy, proxy_connect
        import google.genai.live as _genai_live

        # python_socks (используется внутри websockets_proxy) не понимает схему socks5h.
        # Нормализуем: socks5h -> socks5 (DNS на стороне прокси включён по умолчанию
        # в python_socks для имён хостов).
        _normalized_proxy_url = _proxy_url
        if _normalized_proxy_url.startswith("socks5h://"):
            _normalized_proxy_url = "socks5://" + _normalized_proxy_url[len("socks5h://"):]
        elif _normalized_proxy_url.startswith("socks4a://"):
            _normalized_proxy_url = "socks4://" + _normalized_proxy_url[len("socks4a://"):]

        _proxy_obj = Proxy.from_url(_normalized_proxy_url)

        def _patched_ws_connect(uri, **kwargs):
            """Обёртка над proxy_connect с фиксированным proxy-объектом."""
            return proxy_connect(uri, proxy=_proxy_obj, **kwargs)

        _genai_live.ws_connect = _patched_ws_connect
        # httpx (REST-вызовы google-genai) читает HTTPS_PROXY сам.
        # Продублируем в нижнем регистре на всякий случай.
        os.environ.setdefault("https_proxy", _proxy_url)
        os.environ.setdefault("http_proxy", _proxy_url)

        _masked = _proxy_url.split("@")[-1] if "@" in _proxy_url else _proxy_url
        logger.info(
            "Gemini Live будет ходить через прокси %s (websockets monkey-patch активен)",
            _masked,
        )
    except ImportError:
        logger.error(
            "Переменная HTTPS_PROXY задана, но библиотека websockets_proxy "
            "не установлена. Установите: pip install websockets_proxy"
        )
    except Exception as exc:
        logger.exception("Не удалось настроить прокси для Gemini Live: %s", exc)
else:
    logger.warning(
        "HTTPS_PROXY не задан. Если сервер в РФ — Gemini вернёт ошибку 1007."
    )

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


async def run_gemini_session(client_ws: WebSocket) -> None:
    """
    Запускает двустороннюю аудио-сессию Gemini Live для одного WebSocket-соединения.

    Параметры:
        client_ws   — принятый WebSocket-соединение FastAPI (уже accept()'ed)

    Исключения перехватываются внутри; после завершения (разрыв со стороны
    клиента или Gemini) функция возвращает управление.
    """
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        logger.error("GEMINI_API_KEY не задан — невозможно открыть Live-сессию")
        await client_ws.close(code=1011, reason="Server misconfiguration")
        return

    client = genai.Client(
        api_key=api_key,
        http_options={"api_version": "v1beta"},
    )
    config = _build_live_config()

    logger.info(
        "Открываем Gemini Live сессию: model=%s voice=%s",
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
