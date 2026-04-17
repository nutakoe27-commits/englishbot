"""
faster-whisper STT WebSocket server for EnglishBot.

Protocol (JSON over WebSocket):
  client -> server:
    {"type":"config","sample_rate":16000,"language":"en"}
    {"type":"audio","data":"<base64 raw PCM s16le>"}
    {"type":"eou"}                          # end of utterance
    {"type":"reset"}                        # drop buffer without transcribing
  server -> client:
    {"type":"ready"}                        # after config accepted
    {"type":"final","text":"..."}           # after EOU
    {"type":"error","message":"..."}

No VAD-based auto-EOU: backend controls utterance boundaries via
explicit {"type":"eou"} (matches current frontend button behavior).

Audio: mono, 16-bit signed PCM, 16000 Hz. Client is responsible for
resampling if needed. Chunks may be any size; server buffers until EOU.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from faster_whisper import WhisperModel

# config
MODEL_NAME = os.getenv("WHISPER_MODEL", "large-v3-turbo")
DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
DEVICE_INDEX = int(os.getenv("WHISPER_DEVICE_INDEX", "0"))
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "5"))
VAD_FILTER = os.getenv("WHISPER_VAD_FILTER", "true").lower() == "true"

SAMPLE_RATE = 16000
MAX_UTTER_SECONDS = 60
MAX_BUFFER_BYTES = SAMPLE_RATE * 2 * MAX_UTTER_SECONDS  # 16-bit PCM

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("stt")

# model singleton
log.info(
    "loading %s on %s:%d compute_type=%s",
    MODEL_NAME, DEVICE, DEVICE_INDEX, COMPUTE_TYPE,
)
_t0 = time.time()
model = WhisperModel(
    MODEL_NAME,
    device=DEVICE,
    device_index=DEVICE_INDEX,
    compute_type=COMPUTE_TYPE,
)
log.info("model loaded in %.1fs", time.time() - _t0)

# warmup with 1s of silence to pre-compile kernels
log.info("warming up...")
_t0 = time.time()
_silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
list(model.transcribe(_silence, beam_size=BEAM_SIZE, language="en")[0])
log.info("warmup done in %.2fs", time.time() - _t0)

# app
app = FastAPI(title="whisper-stt")


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "model": MODEL_NAME, "device": f"{DEVICE}:{DEVICE_INDEX}"})


@dataclass
class Session:
    sample_rate: int = SAMPLE_RATE
    language: Optional[str] = "en"
    buffer: bytearray = field(default_factory=bytearray)

    def append(self, pcm: bytes) -> None:
        self.buffer.extend(pcm)
        if len(self.buffer) > MAX_BUFFER_BYTES:
            overflow = len(self.buffer) - MAX_BUFFER_BYTES
            del self.buffer[:overflow]

    def reset(self) -> None:
        self.buffer.clear()

    def to_float32(self) -> np.ndarray:
        if not self.buffer:
            return np.zeros(0, dtype=np.float32)
        audio = np.frombuffer(bytes(self.buffer), dtype=np.int16)
        return (audio.astype(np.float32) / 32768.0)


async def transcribe_async(audio: np.ndarray, language: Optional[str]) -> str:
    """Run sync faster-whisper in thread pool to keep the event loop free."""
    def _run() -> str:
        segments, _info = model.transcribe(
            audio,
            beam_size=BEAM_SIZE,
            language=language,
            vad_filter=VAD_FILTER,
            condition_on_previous_text=False,
        )
        return "".join(seg.text for seg in segments).strip()

    return await asyncio.to_thread(_run)


@app.websocket("/ws")
async def ws_stt(ws: WebSocket):
    await ws.accept()
    peer = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
    log.info("[%s] ws connected", peer)
    session = Session()
    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")

            if mtype == "config":
                session.sample_rate = int(msg.get("sample_rate") or SAMPLE_RATE)
                session.language = msg.get("language") or None
                if session.sample_rate != SAMPLE_RATE:
                    await ws.send_json({
                        "type": "error",
                        "message": f"unsupported sample_rate {session.sample_rate}, expected {SAMPLE_RATE}",
                    })
                    await ws.close()
                    return
                log.info("[%s] config: sr=%d lang=%s", peer, session.sample_rate, session.language)
                await ws.send_json({"type": "ready"})

            elif mtype == "audio":
                data = msg.get("data") or ""
                try:
                    pcm = base64.b64decode(data, validate=True)
                except Exception as e:
                    log.warning("[%s] bad audio b64: %s", peer, e)
                    continue
                session.append(pcm)

            elif mtype == "eou":
                audio = session.to_float32()
                session.reset()
                if audio.size < SAMPLE_RATE // 10:  # <100ms
                    log.info("[%s] eou: empty/too short", peer)
                    await ws.send_json({"type": "final", "text": ""})
                    continue
                t0 = time.time()
                try:
                    text = await transcribe_async(audio, session.language)
                except Exception as e:
                    log.exception("[%s] transcribe failed", peer)
                    await ws.send_json({"type": "error", "message": str(e)})
                    continue
                dt = time.time() - t0
                log.info(
                    "[%s] eou: %.2fs audio=%.2fs text=%r",
                    peer, dt, audio.size / SAMPLE_RATE, text[:100],
                )
                await ws.send_json({"type": "final", "text": text})

            elif mtype == "reset":
                session.reset()

            else:
                log.warning("[%s] unknown message type: %r", peer, mtype)
    except WebSocketDisconnect:
        log.info("[%s] ws disconnected", peer)
    except Exception:
        log.exception("[%s] ws error", peer)
        try:
            await ws.close()
        except Exception:
            pass
