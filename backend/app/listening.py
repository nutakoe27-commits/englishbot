"""
listening.py — listening-тренажёр (генерация подкаста).

Поток:
    1) принимаем POST /api/listening/generate с category/duration_min/use_vocab/speed/level
    2) собираем prompt и зовём vLLM (один блокирующий complete()-вызов с увеличенным
       max_tokens — стримить смысла нет, аудио всё равно собирается целиком)
    3) текст отдаём в KokoroTTSProvider, склеиваем все PCM-чанки в bytes
    4) оборачиваем в WAV (44-байтный RIFF + PCM s16le 24kHz mono)
    5) кладём в in-memory store по audio_id, возвращаем audio_url
    6) пишем в `sessions` mode='listening' для streak/progress-учёта

Лимиты намеренно не блокируют — /api/listening/quota всегда отдаёт premium=true.
Когда захочется реальный лимит — добавить колонку DailyUsage.listening_used_seconds
и проверять в quota + в generate.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import secrets
import struct
import time
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .config import settings
from .db import db_session
from . import presence
from .tts_providers import KokoroTTSProvider, OUTPUT_SAMPLE_RATE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/listening", tags=["Listening"])


# ─── Конфигурация ────────────────────────────────────────────────────────────

# Kokoro выдаёт ~150 wpm; берём это за target words/min для промпта.
WORDS_PER_MINUTE = 150
# TTL онлайн-присутствия на время генерации подкаста (нет heartbeat — снимаем
# в finally; это лишь safety-net на случай жёсткого краша посреди генерации).
PRESENCE_GEN_TTL = 210
MAX_DURATION_MIN = 20
ALLOWED_CATEGORIES = {
    "news", "tech", "psychology", "history",
    "science", "travel", "business", "culture",
}
ALLOWED_SPEEDS = {0.75, 1.0, 1.25}
ALLOWED_LEVELS = {"A2", "B1", "B2", "C1"}

CATEGORY_HINTS = {
    "news": "current events and recent global news",
    "tech": "technology, software, and digital trends",
    "psychology": "psychology, behaviour, and emotional well-being",
    "history": "history — pick a specific event or era",
    "science": "science — pick a specific topic (physics, biology, space, etc.)",
    "travel": "travel — pick a specific destination or travel theme",
    "business": "business, entrepreneurship, and the economy",
    "culture": "culture, arts, music, film, or literature",
}

# In-memory store: audio_id → (wav_bytes, expires_at_unix). TTL 1 час.
_AUDIO_STORE: dict[str, tuple[bytes, float]] = {}
_AUDIO_TTL_SEC = 3600
_AUDIO_STORE_MAX = 200  # эвакуируем самые старые при переполнении


def _gc_audio_store() -> None:
    now = time.time()
    # удалить просроченные
    expired = [aid for aid, (_, exp) in _AUDIO_STORE.items() if exp < now]
    for aid in expired:
        _AUDIO_STORE.pop(aid, None)
    # обрезать по размеру (FIFO)
    while len(_AUDIO_STORE) >= _AUDIO_STORE_MAX:
        try:
            oldest = next(iter(_AUDIO_STORE))
            _AUDIO_STORE.pop(oldest, None)
        except StopIteration:
            break


def _wrap_pcm_to_wav(pcm: bytes, sample_rate: int = OUTPUT_SAMPLE_RATE) -> bytes:
    """16-bit mono PCM → WAV-байты (44-байтный RIFF header)."""
    n_channels = 1
    bps = 16
    byte_rate = sample_rate * n_channels * bps // 8
    block_align = n_channels * bps // 8
    data_size = len(pcm)
    riff_size = 36 + data_size
    header = (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, n_channels, sample_rate, byte_rate, block_align, bps)
        + b"data"
        + struct.pack("<I", data_size)
    )
    return header + pcm


# ─── Промпт и LLM-вызов ──────────────────────────────────────────────────────


def _build_listening_prompt(
    *,
    category: str,
    duration_min: int,
    level: str,
    vocab_words: list[str],
) -> tuple[str, str]:
    """Возвращает (system_prompt, user_prompt) для одного complete()-вызова."""
    target_words = duration_min * WORDS_PER_MINUTE
    cat_hint = CATEGORY_HINTS.get(category, category)

    vocab_clause = ""
    if vocab_words:
        joined = ", ".join(vocab_words)
        vocab_clause = (
            f"\n\nThe learner is actively practising these words: {joined}. "
            "Weave each of them naturally into the monologue at least once "
            "(in context, not as a list). Do not draw attention to the words."
        )

    system = (
        "You are an expert podcast scriptwriter for English learners. "
        "Write a single-host monologue in spoken English. "
        f"Topic: {cat_hint}. "
        f"Target length: about {target_words} words (roughly {duration_min} "
        "minute(s) when spoken). "
        f"Lexical complexity: CEFR {level}. "
        "Style: natural spoken English with contractions and light fillers ok, "
        "but no markdown, no headings, no stage directions, no speaker tags, "
        "no chapter markers, no music cues. Plain prose only. "
        "Open with a hook, develop one or two concrete ideas, close with a "
        "brief takeaway."
        + vocab_clause
    )
    user = (
        "/no_think\n"
        f"Write the podcast script now. Plain text only. ~{target_words} words."
    )
    return system, user


async def _call_llm_for_script(system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    """Прямой вызов vLLM с большим max_tokens.

    Не используем VLLMProvider.complete() — там жёсткий max_tokens=400, не хватит
    на 10+-минутный подкаст. Логика тут проще: один POST со stream=False, отдаём
    весь content (плюс гигиена reasoning-тегов).
    """
    base_url = (settings.VLLM_BASE_URL or "").rstrip("/")
    if not base_url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM not configured")
    model_name = settings.VLLM_MODEL_NAME or ""
    api_key = settings.VLLM_API_KEY or "not-needed"

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": max_tokens,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base_url}/chat/completions", json=payload, headers=headers,
        )
        if resp.status_code != 200:
            logger.error("[listening LLM] HTTP %s: %s", resp.status_code, resp.text[:500])
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"LLM returned {resp.status_code}",
            )
        data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        content = ""

    # Защита от reasoning-tag, на случай если enable_thinking не сработал.
    import re as _re
    content = _re.sub(r"<think>.*?</think>\s*", "", content, flags=_re.DOTALL | _re.IGNORECASE)
    content = content.strip()
    if not content:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "LLM returned empty script")
    return content


# ─── TTS ─────────────────────────────────────────────────────────────────────


async def _synthesize_full(text: str, speed: float) -> bytes:
    """Сгенерить весь PCM целиком через Kokoro. Возвращает raw s16le 24kHz mono."""
    if not settings.KOKORO_TTS_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "TTS not configured")
    # Kokoro не стримит синтез: считает целиком, потом отдаёт чанками. На
    # 10-15-минутном тексте до первого чанка может уйти 30-60 сек. Дефолтные
    # 15с (для голосовых сессий) — мало; ставим запас по длине.
    char_count = len(text)
    first_chunk_to = max(30.0, min(180.0, char_count / 60))   # ~60 чар/сек синтеза
    next_chunk_to = max(30.0, min(120.0, char_count / 120))
    provider = KokoroTTSProvider(
        url=settings.KOKORO_TTS_URL,
        voice=settings.KOKORO_TTS_VOICE or "af_heart",
        speed=speed,
        first_chunk_timeout=first_chunk_to,
        next_chunk_timeout=next_chunk_to,
    )
    buf = io.BytesIO()
    async for chunk in provider.synthesize(text):
        buf.write(chunk)
    pcm = buf.getvalue()
    if not pcm:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "TTS returned no audio")
    return pcm


# ─── Endpoints ───────────────────────────────────────────────────────────────


class _GenerateIn(BaseModel):
    init_data: str
    duration_min: int = Field(..., ge=1, le=MAX_DURATION_MIN)
    category: str
    use_vocab: bool = False
    speed: float = 1.0
    level: str = "B1"


class _GenerateOut(BaseModel):
    session_id: int
    transcript: str
    audio_url: str
    used_words: list[str]


def _tg_id_from_init_data(init_data: str) -> int:
    """Локальный wrapper над main.validate_telegram_init_data — чтобы не плодить
    циклические импорты, повторяем минимальную копию проверки."""
    from .main import _tg_id_from_init_data as _impl
    return _impl(init_data)


@router.post("/generate", response_model=_GenerateOut)
async def generate_podcast(body: _GenerateIn, request: Request) -> _GenerateOut:
    # ── Валидация входа ─────────────────────────────────────────────────
    if body.category not in ALLOWED_CATEGORIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_category")
    if body.speed not in ALLOWED_SPEEDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_speed")
    if body.level not in ALLOWED_LEVELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_level")

    tg_id = _tg_id_from_init_data(body.init_data)

    # ── Подгружаем user-слова если включён тумблер ──────────────────────
    user_id: Optional[int] = None
    vocab_words: list[str] = []
    if settings.DATABASE_URL:
        from .db import Repo
        async with db_session() as session:
            repo = Repo(session)
            user = await repo.upsert_user(tg_id=tg_id)
            user_id = user.id
            if body.use_vocab:
                vocab_words = await repo.get_user_words_for_prompt(user.id, limit=10)
            await session.commit()

    # Онлайн-присутствие: на время генерации юзер виден в админке как 🎧.
    # Снимаем в finally — даже при ошибке LLM/TTS не оставляем «фантом».
    if user_id is not None:
        presence.mark(
            user_id,
            mode="listening",
            level=body.level,
            role=body.category,
            ttl=PRESENCE_GEN_TTL,
        )

    try:
        # ── Генерация текста ────────────────────────────────────────────
        sys_prompt, usr_prompt = _build_listening_prompt(
            category=body.category,
            duration_min=body.duration_min,
            level=body.level,
            vocab_words=vocab_words,
        )
        # Запас по токенам: ~2 токена на слово + buffer.
        max_tokens = max(512, body.duration_min * WORDS_PER_MINUTE * 2 + 256)
        transcript = await _call_llm_for_script(sys_prompt, usr_prompt, max_tokens=max_tokens)

        # Если клиент отвалился пока ждали LLM — не тратим TTS.
        if await request.is_disconnected():
            raise HTTPException(status.HTTP_499_CLIENT_CLOSED_REQUEST
                                if hasattr(status, "HTTP_499_CLIENT_CLOSED_REQUEST") else 499,
                                "client_disconnected")

        # ── Какие vocab-слова реально вошли в текст ─────────────────────
        transcript_lower = transcript.lower()
        used_words = [w for w in vocab_words if w.lower() in transcript_lower]

        # ── TTS ─────────────────────────────────────────────────────────
        pcm = await _synthesize_full(transcript, speed=body.speed)
        wav_bytes = _wrap_pcm_to_wav(pcm)
        audio_duration_sec = len(pcm) // 2 // OUTPUT_SAMPLE_RATE

        # ── Кешируем аудио в памяти ─────────────────────────────────────
        _gc_audio_store()
        audio_id = secrets.token_urlsafe(16)
        _AUDIO_STORE[audio_id] = (wav_bytes, time.time() + _AUDIO_TTL_SEC)

        # ── Запись сессии + общая статистика + стрик ────────────────────
        session_id = 0
        duration_seconds = audio_duration_sec or (body.duration_min * 60)
        if user_id is not None and settings.DATABASE_URL:
            from .db import Repo
            from .voice import STREAK_MIN_DURATION_SEC
            async with db_session() as session:
                repo = Repo(session)
                row = await repo.open_session(
                    user_id=user_id,
                    mode="listening",
                    level=body.level,
                    role=body.category,
                )
                await repo.close_session(
                    session_id=row.id,
                    used_seconds=duration_seconds,
                )
                # DailyUsage — чтобы listening шёл в общий счётчик минут (ProgressScreen,
                # «Мой прогресс» использует user_total_seconds + daily series).
                await repo.add_used_seconds(user_id=user_id, seconds=duration_seconds)
                # Streak — поднимаем, если подкаст ≥ STREAK_MIN_DURATION_SEC.
                # role=body.category — категория сохраняется в users.last_session_role,
                # как и в speaking-сессиях (для умной выдачи role-quest).
                if duration_seconds >= STREAK_MIN_DURATION_SEC:
                    try:
                        await repo.bump_streak(user_id, role=body.category)
                    except Exception as exc:
                        logger.warning("[listening] bump_streak failed: %s", exc)
                session_id = row.id
                await session.commit()

        return _GenerateOut(
            session_id=session_id,
            transcript=transcript,
            audio_url=f"/api/listening/audio/{audio_id}.wav",
            used_words=used_words,
        )
    finally:
        if user_id is not None:
            presence.clear(user_id)


@router.get("/audio/{audio_id}.wav")
async def get_audio(audio_id: str, request: Request) -> Response:
    """Отдаёт WAV из in-memory store. Поддерживает HTTP Range Requests —
    без этого iOS Safari WebView не умеет проигрывать большие (>10 МБ) аудио
    и помечает источник как «Ошибка» рядом с play."""
    entry = _AUDIO_STORE.get(audio_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audio_not_found")
    wav_bytes, expires_at = entry
    if expires_at < time.time():
        _AUDIO_STORE.pop(audio_id, None)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audio_expired")

    total = len(wav_bytes)
    range_header = request.headers.get("range") or request.headers.get("Range")

    if range_header:
        import re as _re
        m = _re.match(r"bytes=(\d+)-(\d*)", range_header.strip())
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else total - 1
            end = min(end, total - 1)
            if start > end or start >= total:
                return Response(
                    status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": f"bytes */{total}"},
                )
            chunk = wav_bytes[start : end + 1]
            return Response(
                content=chunk,
                status_code=status.HTTP_206_PARTIAL_CONTENT,
                media_type="audio/wav",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{total}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(len(chunk)),
                    "Cache-Control": "no-cache",
                },
            )

    # Без Range — отдаём целиком, но с Accept-Ranges чтобы клиент знал
    # что endpoint их поддерживает и переспросил с Range при необходимости.
    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(total),
            "Cache-Control": "no-cache",
        },
    )


@router.get("/quota")
async def get_quota(init_data: str = "") -> dict:
    """Лимит на listening. Сейчас заглушка: все юзеры — premium."""
    # Валидируем init_data чтобы не отдавать инфу анонимам, но результат всё
    # равно фиксирован.
    _tg_id_from_init_data(init_data)
    return {
        "premium": True,
        "remaining_seconds": 999_999,
        "used_seconds_today": 0,
    }
