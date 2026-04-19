"""
voice.py — оркестратор голосового диалога.

Архитектура:
    Browser (PCM 16kHz 16-bit LE mono)
      → WebSocket /ws/voice
      → WhisperSTTProvider → финалы пользователя
      → VLLMProvider (Qwen3) → текст ответа репетитора
      → KokoroTTSProvider → PCM 24kHz в браузер
      → Browser

End-of-utterance (EOU) управляется клиентом:
    Когда пользователь отжимает кнопку записи, фронт шлёт JSON {"type":"eou"}.
    STT-провайдер прокидывает маркер на свой бэкенд. По финалу фраза собирается
    и уходит в LLM → TTS.

Весь стек (STT/LLM/TTS) крутится на собственном V100 и пробрасывается
на VPS через SSH-reverse-tunnel.
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from .llm_providers import get_llm_provider
from .stt_providers import get_stt_provider
from .tts_providers import get_tts_provider
from .tutor_prompt import SessionSettings, build_greeting, build_system_prompt

logger = logging.getLogger(__name__)

# ─── Константы ────────────────────────────────────────────────────────────────
# Аудио-параметры. Браузер шлёт 16kHz, воспроизводит 24kHz.
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000

# История диалога — храним несколько последних реплик для контекста LLM.
MAX_HISTORY_TURNS = 6


# ─── Хелпер: синтез + отправка в WebSocket ───────────────────────────

async def _send_tts_to_ws(websocket: WebSocket, tts, text: str) -> None:
    """Синтезирует текст через TTS-провайдер и стримит PCM в WS."""
    chunks_sent = 0
    bytes_sent = 0
    logger.warning("[TTS] старт синтеза: %r", text[:80])
    try:
        async for pcm_chunk in tts.synthesize(text):
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

async def run_voice_session(websocket: WebSocket) -> None:
    """
    Основной цикл голосового диалога.

    Протокол WebSocket:
      Клиент → PCM 16kHz 16-bit LE mono (binary frames)
      Сервер → JSON {"type":"text","role":"user"|"tutor","text":"..."}
      Сервер → PCM 24kHz 16-bit LE mono (binary frames)
    """
    logger.warning("[voice] run_voice_session старт")

    # Настройки сессии (уровень, роль, длина, исправления) — из query-параметров WS.
    session_settings = SessionSettings.from_query(dict(websocket.query_params))
    system_prompt = build_system_prompt(session_settings)
    logger.warning(
        "[voice] настройки: level=%s role=%s length=%s corrections=%s speech_lang=%s%s",
        session_settings.level,
        session_settings.role,
        session_settings.length,
        session_settings.corrections,
        session_settings.speech_lang,
        f" custom={session_settings.role_custom!r}"
        if session_settings.role == "custom"
        else "",
    )

    # Инициализируем три провайдера — каждый читает свои настройки.
    try:
        llm = get_llm_provider()
    except Exception as exc:
        logger.error("Не удалось создать LLM-провайдера: %s", exc)
        await websocket.close(code=1011, reason="Server misconfiguration: LLM provider")
        return

    try:
        # Язык STT приходит из настроек сессии: en / ru / "" (auto)
        stt = get_stt_provider(language=session_settings.whisper_language())
    except Exception as exc:
        logger.error("Не удалось создать STT-провайдера: %s", exc)
        await websocket.close(code=1011, reason="Server misconfiguration: STT provider")
        return

    try:
        tts = get_tts_provider()
    except Exception as exc:
        logger.error("Не удалось создать TTS-провайдера: %s", exc)
        await websocket.close(code=1011, reason="Server misconfiguration: TTS provider")
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
    greeting = build_greeting(session_settings)
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({
                "type": "text",
                "role": "tutor",
                "text": greeting,
            })
            # Озвучиваем приветствие в фоне — не блокируем STT-пайплайн
            asyncio.create_task(_send_tts_to_ws(websocket, tts, greeting))
            # Добавляем приветствие в историю, чтобы LLM видел контекст
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
        await _send_tts_to_ws(websocket, tts, text)

    async def handle_user_utterance(text: str) -> None:
        """Для готовой реплики пользователя: LLM в ответ и озвучка."""
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({
                "type": "text",
                "role": "user",
                "text": text,
            })

        try:
            reply = await llm.complete(
                user_text=text,
                history=history,
                system_prompt=system_prompt,
            )
        except Exception as exc:
            logger.error("LLM сбой: %s", exc, exc_info=True)
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

        Перекладываем события из общей очереди в локальную (на одну фразу) и
        запускаем stt.stream() для каждой.
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
                            # STT-сервер подтвердил EOU — стрим сейчас закроется
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
        logger.warning("[voice] сессия завершена для %s", websocket.client)
