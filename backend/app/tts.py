"""
tts.py — озвучка одного слова/короткой фразы для словаря (SRS + «Мои слова»).

GET /api/tts/word?text=hello — синтезирует слово через тот же Kokoro-82M,
что и подкасты, оборачивает в WAV, отдаёт байтами. In-memory кеш по
(text, voice, speed) — слова повторяются (SRS), не синтезируем дважды.

Отличие от listening.py: короткие таймауты (слово синтезируется <1с) и
строгий лимит на длину текста.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response

from .config import settings
from .tts_providers import KokoroTTSProvider, wrap_pcm_to_wav

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tts", tags=["TTS"])

# ─── Конфигурация ────────────────────────────────────────────────────────────
MAX_TEXT_LEN = 60          # одно слово / короткая фраза
CACHE_TTL_SEC = 24 * 3600  # WAV живёт сутки
CACHE_MAX_ENTRIES = 2000   # GC при превышении

# In-memory кеш: key -> (wav_bytes, created_at)
_cache: dict[str, tuple[bytes, float]] = {}


def _gc_cache() -> None:
    """Удаляем протухшие записи; если всё равно много — режем самые старые."""
    now = time.monotonic()
    expired = [k for k, (_, ts) in _cache.items() if now - ts > CACHE_TTL_SEC]
    for k in expired:
        _cache.pop(k, None)
    if len(_cache) > CACHE_MAX_ENTRIES:
        # Сортируем по времени создания, оставляем свежие.
        ordered = sorted(_cache.items(), key=lambda kv: kv[1][1])
        for k, _ in ordered[: len(_cache) - CACHE_MAX_ENTRIES]:
            _cache.pop(k, None)


def _cache_key(text: str, voice: str, speed: float) -> str:
    return hashlib.md5(f"{text}|{voice}|{speed}".encode("utf-8")).hexdigest()


@router.get("/word")
async def tts_word(
    text: str = Query(..., min_length=1, max_length=MAX_TEXT_LEN),
    voice: Optional[str] = Query(default=None),
    speed: float = Query(default=1.0, ge=0.5, le=2.0),
) -> Response:
    """Озвучить слово/короткую фразу. Возвращает audio/wav."""
    if not settings.KOKORO_TTS_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "tts_unavailable")

    norm = text.strip().lower()
    if not norm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_text")

    use_voice = voice or settings.KOKORO_TTS_VOICE
    key = _cache_key(norm, use_voice, speed)

    cached = _cache.get(key)
    if cached is not None:
        wav, _ts = cached
        return Response(
            content=wav,
            media_type="audio/wav",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    # Синтез: короткие таймауты — слово готово быстро.
    provider = KokoroTTSProvider(
        url=settings.KOKORO_TTS_URL,
        voice=use_voice,
        speed=speed,
        first_chunk_timeout=8.0,
        next_chunk_timeout=8.0,
    )
    pcm = bytearray()
    try:
        async for chunk in provider.synthesize(norm):
            pcm.extend(chunk)
    except Exception:
        logger.exception("[tts] synthesize failed for %r", norm)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "tts_failed")

    if not pcm:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "tts_empty")

    wav = wrap_pcm_to_wav(bytes(pcm))
    _gc_cache()
    _cache[key] = (wav, time.monotonic())

    return Response(
        content=wav,
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=86400"},
    )
