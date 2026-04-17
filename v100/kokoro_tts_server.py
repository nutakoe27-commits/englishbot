"""
kokoro_tts_server.py — локальный TTS-сервер на V100.

FastAPI + WebSocket, принимает текст и стримит PCM 24kHz 16-bit LE mono.
Модель: Kokoro-82M (hexgrad/Kokoro-82M), голос по умолчанию af_heart.

Протокол WebSocket (совместимый с Whisper-сервером по духу):
    клиент → сервер:
      {"type":"config","voice":"af_heart","speed":1.0,"lang":"a"}
      {"type":"text","text":"Hello, how are you today?"}
      {"type":"close"}
    сервер → клиент:
      {"type":"ready"}               — конфиг принят
      {"type":"audio","data":"<b64>"} — PCM-чанк (24kHz s16le mono)
      {"type":"done"}                 — синтез текущего текста завершён
      {"type":"error","message":"..."}

Бэкенд бота (tts_providers.py) для каждой реплики репетитора:
  1) открывает WS, шлёт config, ждёт ready
  2) шлёт text, принимает audio-чанки до done
  3) может послать ещё один text в этой же сессии (поддерживается)
  4) закрывает WS (или посылает close)

Запуск (через systemd):
    /home/user/kokoro-tts/venv/bin/python /home/user/kokoro-tts/kokoro_tts_server.py

Конфигурируется переменными окружения:
    KOKORO_HOST       (default 0.0.0.0)
    KOKORO_PORT       (default 23335)
    KOKORO_LANG       (default 'a' — American English; 'b' — British)
    KOKORO_DEVICE     (default 'cuda')
    KOKORO_GPU_INDEX  (default '0' — ставим на GPU 0 рядом с Whisper)
    KOKORO_CHUNK_MS   (default 40 — размер PCM-чанка при отправке)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("kokoro-tts")

# ─── Конфиг из ENV ───────────────────────────────────────────────────────────
HOST = os.environ.get("KOKORO_HOST", "0.0.0.0")
PORT = int(os.environ.get("KOKORO_PORT", "23335"))
DEFAULT_LANG = os.environ.get("KOKORO_LANG", "a")
DEVICE = os.environ.get("KOKORO_DEVICE", "cuda")
GPU_INDEX = os.environ.get("KOKORO_GPU_INDEX", "0")
CHUNK_MS = int(os.environ.get("KOKORO_CHUNK_MS", "40"))

SAMPLE_RATE = 24000  # Kokoro всегда выдаёт 24kHz
# Число сэмплов на чанк (20мс при 24kHz = 480; 40мс = 960).
# Бэкенд уже рассчитан на мелкие чанки от Yandex TTS, так что 40мс норм.
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000

# ─── Загрузка модели ─────────────────────────────────────────────────────────
# KPipeline — официальный интерфейс hexgrad/kokoro.
# lang_code='a' → American English, 'b' → British English.
#
# Пайплайн — не thread-safe. Под каждую сессию создавать новый дорого
# (внутри torch.nn.Module). Делаем один глобальный на процесс и
# сериализуем доступ через asyncio.Lock. Синтез быстрее realtime даже на
# одном GPU, параллелизм тут не критичен — WS-сессий у нас единицы.

_pipeline = None
_pipeline_lock = asyncio.Lock()


def _load_pipeline(lang_code: str):
    """Ленивая инициализация KPipeline. Однажды загруженная модель кэшируется."""
    # Важно: если есть CUDA и KOKORO_GPU_INDEX задан — пинуем на него.
    if DEVICE == "cuda" and torch.cuda.is_available():
        os.environ["CUDA_VISIBLE_DEVICES"] = GPU_INDEX

    from kokoro import KPipeline  # импорт внутри, чтобы ENV сработал

    logger.info("Загружаем Kokoro KPipeline lang_code=%s device=%s", lang_code, DEVICE)
    pipeline = KPipeline(lang_code=lang_code)
    logger.info("Kokoro готов")
    return pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipeline
    _pipeline = _load_pipeline(DEFAULT_LANG)

    # Прогрев: одна короткая фраза, чтобы первый реальный запрос не тормозил
    # из-за JIT/кэша CUDA.
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _warmup, _pipeline)
    except Exception as exc:
        logger.warning("warmup не удался: %s", exc)

    yield

    # Cleanup не нужен — процесс просто умирает


def _warmup(pipeline) -> None:
    """Короткий прогон, чтобы прогрузить веса в GPU и прогреть CUDA-ядра."""
    logger.info("warmup: синтезируем 'Hello.'")
    gen = pipeline("Hello.", voice="af_heart", speed=1.0)
    n = 0
    for _, _, audio in gen:
        n += len(audio)
    logger.info("warmup: готово, %d сэмплов", n)


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model": "kokoro-82m",
        "sample_rate": SAMPLE_RATE,
        "device": f"{DEVICE}:{GPU_INDEX}" if DEVICE == "cuda" else DEVICE,
        "default_lang": DEFAULT_LANG,
    }


# ─── WebSocket ──────────────────────────────────────────────────────────────

def _audio_to_pcm16(audio: "np.ndarray | torch.Tensor") -> bytes:
    """Конвертирует float32 [-1,1] из Kokoro в PCM s16le little-endian."""
    if isinstance(audio, torch.Tensor):
        audio = audio.detach().cpu().numpy()
    # Kokoro возвращает float32 в диапазоне [-1, 1]. Клиппим и масштабируем.
    audio = np.clip(audio, -1.0, 1.0)
    pcm16 = (audio * 32767.0).astype(np.int16)
    return pcm16.tobytes()


async def _synthesize_and_stream(
    ws: WebSocket,
    text: str,
    voice: str,
    speed: float,
) -> None:
    """Синтезирует текст и отправляет PCM-чанки в WebSocket."""
    global _pipeline
    assert _pipeline is not None, "pipeline не инициализирован"

    loop = asyncio.get_event_loop()
    total_samples = 0

    # KPipeline сам разбивает текст на сегменты (по split_pattern='\n+' дефолту).
    # Нам нужно одно предложение целиком — не ставим split_pattern, полагаемся
    # на встроенное внутреннее разбиение.
    async with _pipeline_lock:
        # Собираем сегменты синхронно в executor, чтобы не блокировать loop.
        def run_pipeline():
            results = []
            for gs, ps, audio in _pipeline(text, voice=voice, speed=speed):
                results.append((gs, ps, audio))
            return results

        segments = await loop.run_in_executor(None, run_pipeline)

    for gs, ps, audio in segments:
        if isinstance(audio, torch.Tensor):
            audio_np = audio.detach().cpu().numpy()
        else:
            audio_np = audio
        total_samples += len(audio_np)
        pcm = _audio_to_pcm16(audio_np)
        # Режем на мелкие чанки, чтобы фронту было что играть постепенно.
        # Это не настоящий streaming (Kokoro не стримит), но сглаживает UX.
        chunk_bytes = CHUNK_SAMPLES * 2  # s16 → 2 байта на сэмпл
        for off in range(0, len(pcm), chunk_bytes):
            if ws.client_state != WebSocketState.CONNECTED:
                logger.warning("WS закрылся во время отправки")
                return
            piece = pcm[off:off + chunk_bytes]
            b64 = base64.b64encode(piece).decode("ascii")
            await ws.send_text(json.dumps({"type": "audio", "data": b64}))

    duration_s = total_samples / SAMPLE_RATE
    logger.info("TTS готово: %.2f сек аудио для %r", duration_s, text[:80])

    if ws.client_state == WebSocketState.CONNECTED:
        await ws.send_text(json.dumps({"type": "done"}))


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("WS открыт: %s", websocket.client)

    # Состояние сессии
    voice = "af_heart"
    speed = 1.0

    try:
        # Шлём ready сразу — клиент может сразу шлать config + text.
        # Дефолты разумные, config опционален.
        await websocket.send_text(json.dumps({"type": "ready"}))

        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "invalid json",
                }))
                continue

            mtype = msg.get("type")

            if mtype == "config":
                voice = msg.get("voice") or voice
                speed = float(msg.get("speed") or speed)
                # lang сейчас не перепривязываем к другому pipeline —
                # бот использует только American English ('a').
                logger.info("config: voice=%s speed=%s", voice, speed)
                await websocket.send_text(json.dumps({"type": "ready"}))
                continue

            if mtype == "text":
                text = (msg.get("text") or "").strip()
                if not text:
                    await websocket.send_text(json.dumps({"type": "done"}))
                    continue
                try:
                    await _synthesize_and_stream(websocket, text, voice, speed)
                except Exception as exc:
                    logger.error("synth error: %s", exc, exc_info=True)
                    try:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": str(exc),
                        }))
                    except Exception:
                        pass
                continue

            if mtype == "close":
                logger.info("клиент запросил close")
                break

            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"unknown type: {mtype}",
            }))

    except WebSocketDisconnect:
        logger.info("WS disconnect: %s", websocket.client)
    except Exception as exc:
        logger.error("WS ошибка: %s", exc, exc_info=True)
    finally:
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()
        except Exception:
            pass
        logger.info("WS закрыт: %s", websocket.client)


if __name__ == "__main__":
    logger.info(
        "Запуск Kokoro TTS WS на %s:%d (device=%s, gpu=%s, sample_rate=%d, chunk_ms=%d)",
        HOST, PORT, DEVICE, GPU_INDEX, SAMPLE_RATE, CHUNK_MS,
    )
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
