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
import re
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from .config import settings as app_settings
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

# Период «тика» учёта времени для бесплатных юзеров (секунды).
USAGE_HEARTBEAT_SECONDS = 5

# ─── Language guard: отказ принимать русскую речь ─────────────────────
# Whisper при language="en" в большинстве случаев переводит/транслитерирует
# русскую речь, но иногда возвращает кириллицу. Проверяем долю кириллических
# букв среди всех букв фразы — если получилась, блокируем.
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿԀ-ԯ]")
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

RUSSIAN_REMINDER_EN = (
    "Let's stick to English here — I won't understand Russian. "
    "Try saying it in English, even with mistakes, that's fine."
)

# Fallback, если LLM не уложился в LLM_TIMEOUT_SEC.
LLM_TIMEOUT_FALLBACK_EN = (
    "Sorry, I'm thinking too long right now. Could you say that again?"
)


async def _llm_complete_with_timeout(llm, *, user_text, history, system_prompt):
    """Зовёт llm.complete с таймаутом из настроек. На таймаут — fallback."""
    timeout = app_settings.LLM_TIMEOUT_SEC
    coro = llm.complete(
        user_text=user_text,
        history=history,
        system_prompt=system_prompt,
    )
    if timeout and timeout > 0:
        return await asyncio.wait_for(coro, timeout=timeout)
    return await coro


# ─── Correction split: «Correction: ...\n\n<body>» ──────────────────────
# tutor_prompt._CORRECTION_ON инструктирует LLM выдавать первой строкой
# `Correction: <fixed>\n\n<reply>`. Парсим её и отдаём отдельно — фронт
# подсвечивает correction жёлтым блоком, а TTS озвучивает только body.
_CORRECTION_LINE_RE = re.compile(
    r"^\s*Correction\s*:\s*(.+?)\s*\n\s*\n+(.+)\Z",
    re.DOTALL | re.IGNORECASE,
)


def _split_correction(reply: str) -> tuple[Optional[str], str]:
    """Возвращает (correction, body). Если структуры нет — (None, reply)."""
    if not reply:
        return None, reply or ""
    m = _CORRECTION_LINE_RE.match(reply)
    if not m:
        return None, reply.strip()
    correction = m.group(1).strip()
    body = m.group(2).strip()
    if not correction or not body:
        return None, reply.strip()
    # Защита от вырожденных случаев — если correction слишком длинный,
    # это, скорее всего, не correction, а первая строка ответа.
    if len(correction) > 200:
        return None, reply.strip()
    return correction, body


async def _load_learner_context(limits_ctx) -> Optional[dict]:
    """Тянем из БД recent_vocab + recent_mistakes для текущего юзера.

    Best-effort: при отсутствии БД / любой ошибке — возвращаем None,
    тогда build_system_prompt просто не подмешает учительский контекст.
    """
    if limits_ctx is None:
        return None
    try:
        from .db import Repo, db_session
    except Exception:
        return None
    try:
        async with db_session() as session:
            repo = Repo(session)
            return await repo.get_learner_context(limits_ctx.user_db_id)
    except Exception as exc:
        logger.warning("[voice] не смог загрузить learner_context: %s", exc)
        return None


def _is_russian_utterance(text: str) -> bool:
    """Есть ли значимая доля кириллицы в тексте.

    Порог: хотя бы 3 кириллические буквы И доля ≥ 30% от всех букв.
    Это исключает ложные срабатывания на редкие вкрапления
    кириллицы (например имя бренда или случайный символ).
    """
    if not text:
        return False
    cyr = _CYRILLIC_RE.findall(text)
    letters = _LETTER_RE.findall(text)
    if len(cyr) < 3:
        return False
    total = len(letters) if letters else len(cyr)
    return total > 0 and (len(cyr) / total) >= 0.3


async def _usage_watchdog(websocket: WebSocket, limits_ctx) -> None:
    """Каждые USAGE_HEARTBEAT_SECONDS списывает порцию с дневного лимита.

    Если у юзера активная подписка — продолжает писать в daily_usage
    (для аналитики), но никогда не закрывает WS.
    Если бесплатный и лимит исчерпан — отправляет {"type":"limit_reached"}
    и закрывает WS кодом 4004.
    """
    if limits_ctx is None:
        return
    try:
        while True:
            await asyncio.sleep(USAGE_HEARTBEAT_SECONDS)
            if websocket.client_state != WebSocketState.CONNECTED:
                return
            await limits_ctx.heartbeat(USAGE_HEARTBEAT_SECONDS)
            if limits_ctx.is_exceeded():
                logger.warning(
                    "[limits] бесплатный лимит исчерпан, закрываем WS user_db_id=%s",
                    limits_ctx.user_db_id,
                )
                try:
                    await websocket.send_json(
                        {"type": "limit_reached", **limits_ctx.snapshot().to_dict()}
                    )
                    await websocket.close(code=4004, reason="Daily limit reached")
                except Exception:
                    pass
                return
    except asyncio.CancelledError:
        # Нормальный путь — сессия закрылась раньше тика
        raise
    except Exception as exc:
        logger.error("[limits] watchdog упал: %s", exc, exc_info=True)



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


# ─── Чисто текстовая сессия (chat-mode) ──────────────────────────────────────

async def _run_chat_session(
    *,
    websocket: WebSocket,
    llm,
    system_prompt: str,
    session_settings: SessionSettings,
    limits_ctx=None,
) -> None:
    """Чисто текстовый диалог: клиент шлёт user_text — сервер отвечает text без озвучки.

    Протокол WebSocket:
      Клиент → JSON {"type":"user_text","text":"..."}
      Сервер → JSON {"type":"text","role":"user"|"tutor","text":"..."}
      Сервер → JSON {"type":"thinking"} и {"type":"thinking_done"} (индикация)
    """
    logger.warning("[chat] старт текстовой сессии")
    history: list[dict] = []
    chat_session_start = asyncio.get_event_loop().time()

    watchdog: Optional[asyncio.Task] = (
        asyncio.create_task(_usage_watchdog(websocket, limits_ctx))
        if limits_ctx is not None
        else None
    )

    # Приветствие — только текстом, без TTS.
    greeting = build_greeting(session_settings)
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({
                "type": "text",
                "role": "tutor",
                "text": greeting,
            })
            history.append({"role": "assistant", "text": greeting})
    except Exception as exc:
        logger.warning("[chat] не удалось отправить приветствие: %s", exc)

    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break

            text_payload = msg.get("text")
            if not text_payload:
                # Бинарные фреймы в chat-режиме не ждём — просто игнор.
                continue
            try:
                ctrl = json.loads(text_payload)
            except Exception:
                logger.debug("[chat] непарсимый text-фрейм: %r", text_payload[:120])
                continue

            if ctrl.get("type") != "user_text":
                # Никаких EOU/audio в chat-режиме не ждём.
                continue

            user_text = (ctrl.get("text") or "").strip()
            if not user_text:
                continue
            # Разумный лимит на длину сообщения, чтобы не раздувать контекст LLM.
            user_text = user_text[:2000]

            logger.warning("[chat] user: %s", user_text[:120])

            # Эхо пользовательского сообщения отправлять обратно не нужно — клиент уже
            # отрендерил его локально (в отличие от voice-режима, где отправляем
            # финал STT, чтобы показать распознанный текст).

            # Индикация «thinking…» (опционально, фронт может показывать «typing…»).
            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    await websocket.send_json({"type": "thinking"})
                except Exception:
                    pass

            try:
                reply = await _llm_complete_with_timeout(
                    llm,
                    user_text=user_text,
                    history=history,
                    system_prompt=system_prompt,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "[chat] LLM timeout %dс",
                    app_settings.LLM_TIMEOUT_SEC,
                )
                reply = LLM_TIMEOUT_FALLBACK_EN
            except Exception as exc:
                logger.error("[chat] LLM сбой: %s", exc, exc_info=True)
                reply = "I'm having trouble right now. Let's try again in a moment."

            # Debug: видим что отдал LLM до парсинга — без этого непонятно,
            # выводит ли он "Correction:" вообще или нет.
            logger.warning("[chat] llm_reply_raw: %s", (reply or "")[:200])
            correction, body = _split_correction(reply)
            if correction:
                logger.warning("[chat] correction extracted: %s", correction)

            history.append({"role": "user", "text": user_text})
            history.append({"role": "assistant", "text": body})
            if len(history) > MAX_HISTORY_TURNS * 2:
                del history[: len(history) - MAX_HISTORY_TURNS * 2]

            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    await websocket.send_json({"type": "thinking_done"})
                except Exception:
                    pass
                await websocket.send_json({
                    "type": "text",
                    "role": "tutor",
                    "text": body,
                    "correction": correction,
                })
            logger.warning("[chat] tutor: %s", body[:120])
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("[chat] ошибка в цикле: %s", exc, exc_info=True)
    finally:
        if watchdog and not watchdog.done():
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass
        duration_sec = int(asyncio.get_event_loop().time() - chat_session_start)
        asyncio.create_task(_bump_streak_after_session(
            limits_ctx=limits_ctx, duration_sec=duration_sec,
        ))
        asyncio.create_task(_capture_session_recap_hook(
            limits_ctx=limits_ctx, history=list(history), duration_sec=duration_sec,
        ))
        if limits_ctx is not None:
            asyncio.create_task(_check_quest_after_session(
                limits_ctx=limits_ctx,
                history=history,
                role=session_settings.role,
                duration_sec=duration_sec,
            ))
        logger.warning("[chat] сессия завершена для %s (%dс)", websocket.client, duration_sec)


# ─── Оркестратор WebSocket-сессии ─────────────────────────────────────────────

async def run_voice_session(websocket: WebSocket, limits_ctx=None) -> None:
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
    # Контекст учащегося (recent_vocab + recent_mistakes за неделю) — best-effort.
    # Если БД нет / запрос упал — сессия идёт без подмеса.
    learner_context = await _load_learner_context(limits_ctx)
    system_prompt = build_system_prompt(session_settings, learner_context=learner_context)
    logger.warning(
        "[voice] настройки: level=%s role=%s length=%s corrections=%s mode=%s%s",
        session_settings.level,
        session_settings.role,
        session_settings.length,
        session_settings.corrections,
        session_settings.mode,
        f" custom={session_settings.role_custom!r}"
        if session_settings.role == "custom"
        else "",
    )

    # LLM нужен в любом режиме.
    try:
        llm = get_llm_provider()
    except Exception as exc:
        logger.error("Не удалось создать LLM-провайдера: %s", exc)
        await websocket.close(code=1011, reason="Server misconfiguration: LLM provider")
        return

    # Чисто текстовый режим — передаём управление отдельному хэндлеру без STT/TTS.
    if session_settings.mode == "chat":
        await _run_chat_session(
            websocket=websocket,
            llm=llm,
            system_prompt=system_prompt,
            session_settings=session_settings,
            limits_ctx=limits_ctx,
        )
        return

    # Голосовой режим — нужны STT и TTS.
    try:
        # Принудительно английский: бот для практики английского,
        # автодетект пускает в LLM русскую речь — это ломает сценарий.
        stt = get_stt_provider(language="en")
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
        # Language guard: если Whisper всё же распознал кириллицу —
        # не отдаём в LLM, но показываем всплывающую подсказку на английском.
        if _is_russian_utterance(text):
            logger.warning("[lang-guard] дропаем русскую реплику: %r", text[:120])
            if websocket.client_state == WebSocketState.CONNECTED:
                # Показываем, что услышал — для обратной связи (как и обычно).
                await websocket.send_json({
                    "type": "text",
                    "role": "user",
                    "text": text,
                })
                # Реплику тьютора на английском — без LLM, фиксированную.
                await websocket.send_json({
                    "type": "text",
                    "role": "tutor",
                    "text": RUSSIAN_REMINDER_EN,
                })
            # Историю не трогаем — LLM не должен видеть русскую реплику в контексте.
            await send_tts(RUSSIAN_REMINDER_EN)
            return

        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({
                "type": "text",
                "role": "user",
                "text": text,
            })

        try:
            reply = await _llm_complete_with_timeout(
                llm,
                user_text=text,
                history=history,
                system_prompt=system_prompt,
            )
        except asyncio.TimeoutError:
            logger.error("LLM timeout %dс", app_settings.LLM_TIMEOUT_SEC)
            reply = LLM_TIMEOUT_FALLBACK_EN
        except Exception as exc:
            logger.error("LLM сбой: %s", exc, exc_info=True)
            reply = "I'm having trouble right now. Let's try again in a moment."

        # Debug: видим что отдал LLM до парсинга.
        logger.warning("[voice] llm_reply_raw: %s", (reply or "")[:200])
        # «Correction: <fixed>\n\n<body>» → выделяем correction и тело отдельно.
        # В историю / TTS отдаём только body (correction — мета-информация).
        correction, body = _split_correction(reply)
        if correction:
            logger.warning("[voice] correction extracted: %s", correction)

        history.append({"role": "user", "text": text})
        history.append({"role": "assistant", "text": body})
        if len(history) > MAX_HISTORY_TURNS * 2:
            del history[: len(history) - MAX_HISTORY_TURNS * 2]

        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({
                "type": "text",
                "role": "tutor",
                "text": body,
                "correction": correction,
            })

        # Синтез и стриминг PCM 24kHz в браузер — без correction.
        await send_tts(body)

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

    # Запускаем три корутины параллельно: приём, обработка, и watchdog лимитов
    recv_task = asyncio.create_task(receive_from_client(), name="recv_from_client")
    proc_task = asyncio.create_task(process_stt(), name="process_stt")
    watchdog_task: Optional[asyncio.Task] = (
        asyncio.create_task(_usage_watchdog(websocket, limits_ctx), name="usage_watchdog")
        if limits_ctx is not None
        else None
    )

    session_start = asyncio.get_event_loop().time()
    try:
        wait_set = {recv_task, proc_task}
        if watchdog_task is not None:
            wait_set.add(watchdog_task)
        # Ждём, пока любая из задач не завершится (обычно — disconnect или
        # лимит-watchdog при превышении).
        done, pending = await asyncio.wait(
            wait_set,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        # Даём им завершиться корректно
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        duration_sec = int(asyncio.get_event_loop().time() - session_start)
        # Streak: фиксируем сегодняшнюю практику.
        asyncio.create_task(_bump_streak_after_session(
            limits_ctx=limits_ctx, duration_sec=duration_sec,
        ))
        # Recap: vocabulary + mistakes из транскрипта (LLM-анализ).
        asyncio.create_task(_capture_session_recap_hook(
            limits_ctx=limits_ctx, history=list(history), duration_sec=duration_sec,
        ))
        # Daily Quest: проверяем выполнение квеста по транскрипту сессии.
        if limits_ctx is not None:
            asyncio.create_task(_check_quest_after_session(
                limits_ctx=limits_ctx,
                history=history,
                role=session_settings.role,
                duration_sec=duration_sec,
            ))
        logger.warning("[voice] сессия завершена для %s (%dс)", websocket.client, duration_sec)


# ─── Hook после сессии: streak + recap ────────────────────────────────

# Минимальная длительность сессии, чтобы засчитать день для стрика.
# Защищает от накрутки секундными подключениями.
STREAK_MIN_DURATION_SEC = 30

# Минимальная длительность сессии для recap-захвата (LLM-анализ дороже стоит).
RECAP_MIN_DURATION_SEC = 60


async def _capture_session_recap_hook(*, limits_ctx, history: list[dict], duration_sec: int) -> None:
    """LLM-анализ транскрипта → user_vocabulary + user_mistakes.
    Best-effort, никогда не пробрасывает исключения."""
    if limits_ctx is None or duration_sec < RECAP_MIN_DURATION_SEC:
        return
    if not history:
        return
    try:
        from . import session_recap
        from .db import db_session
    except Exception as exc:
        logger.warning("[voice][recap] не могу импортировать: %s", exc)
        return
    try:
        async with db_session() as session:
            await session_recap.capture_session_recap(
                session,
                user_db_id=limits_ctx.user_db_id,
                history=list(history),
            )
            await session.commit()
    except Exception as exc:
        logger.warning("[voice][recap] capture failed: %s", exc)


async def _bump_streak_after_session(*, limits_ctx, duration_sec: int) -> None:
    """Зафиксировать сегодняшнюю практику и обновить стрик. Вызывается
    из finally в run_voice_session / _run_chat_session.

    Тихо завершается, если:
      - limits_ctx None (нет привязки к юзеру в БД);
      - сессия была короче STREAK_MIN_DURATION_SEC;
      - БД недоступна.
    """
    if limits_ctx is None:
        return
    if duration_sec < STREAK_MIN_DURATION_SEC:
        return
    try:
        from .db import Repo, db_session
    except Exception as exc:
        logger.warning("[voice][streak] не могу импортировать БД: %s", exc)
        return
    try:
        async with db_session() as session:
            repo = Repo(session)
            current, best = await repo.bump_streak(limits_ctx.user_db_id)
            await session.commit()
        logger.info(
            "[voice][streak] user_db_id=%s streak=%d best=%d",
            limits_ctx.user_db_id, current, best,
        )
    except Exception as exc:
        logger.warning("[voice][streak] bump failed: %s", exc)


# ─── Hook после сессии: проверка Daily Quest ─────────────────────────────

async def _check_quest_after_session(
    *,
    limits_ctx,
    history: list[dict],
    role: str,
    duration_sec: int,
) -> None:
    """Собирает транскрипт user-реплик и зовёт quests.verify_session.

    Если квест засчитан — дёргает внутренний endpoint бота, чтобы тот
    прислал юзеру DM о выполнении.
    """
    # Импорты локально, чтобы не падать если quests недоступен (например при
    # отсутствии БД в dev-режиме).
    try:
        from . import quests as quests_mod
        from .db import db_session
    except Exception as exc:
        logger.warning("[voice][quest-hook] не могу импортировать: %s", exc)
        return

    user_text = "\n".join(
        (turn.get("text") or "").strip()
        for turn in history
        if turn.get("role") == "user"
    ).strip()

    if not user_text and role not in ("barista", "interviewer", "travel_agent",
                                       "doctor", "friend", "shopkeeper"):
        logger.info("[voice][quest-hook] пустой транскрипт и роль не ролевая — пропускаем")
        return

    try:
        async with db_session() as s:
            result = await quests_mod.verify_session(
                s,
                user_id=limits_ctx.user_db_id,
                transcript=user_text,
                role=role,
                duration_sec=duration_sec,
            )
            await s.commit()
    except Exception as exc:
        logger.error("[voice][quest-hook] verify_session failed: %s", exc, exc_info=True)
        return

    if not result.completed:
        logger.info(
            "[voice][quest-hook] квест не выполнен: key=%s debug=%s",
            result.quest_key, result.debug if hasattr(result, 'debug') else None,
        )
        return

    logger.warning(
        "[voice][quest-hook] КВЕСТ ВЫПОЛНЕН user=%s quest=%s +%dсек",
        limits_ctx.tg_id, result.quest_key, result.reward_seconds,
    )

    # Уведомляем бот (fire-and-forget).
    import os
    import httpx

    bot_url = os.getenv("BOT_INTERNAL_URL", "http://bot:8080").rstrip("/")
    secret = os.getenv("BACKEND_BOT_SECRET", "").strip()
    if not secret:
        logger.warning("[voice][quest-hook] BACKEND_BOT_SECRET не задан — не можем уведомить бот")
        return

    # Узнаём title_ru чтобы передать в DM.
    title_ru = ""
    try:
        from sqlalchemy import select
        from .db import db_session
        from .db.models import QuestCatalog
        async with db_session() as s:
            r = await s.execute(
                select(QuestCatalog).where(QuestCatalog.key == result.quest_key)
            )
            q = r.scalar_one_or_none()
            if q is not None:
                title_ru = q.title_ru
    except Exception:
        pass

    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                f"{bot_url}/internal/quest-completed",
                json={
                    "tg_id": limits_ctx.tg_id,
                    "title_ru": title_ru,
                    "reward_seconds": result.reward_seconds,
                },
                headers={"X-Bot-Secret": secret},
            )
            if r.status_code >= 400:
                logger.warning("[voice][quest-hook] bot notify %s: %s", r.status_code, r.text[:200])
    except Exception as exc:
        logger.warning("[voice][quest-hook] bot notify failed: %s", exc)
