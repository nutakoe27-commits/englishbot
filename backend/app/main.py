from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import settings

app = FastAPI(
    title="AI English Tutor — Backend",
    version="0.1.0",
    description="Backend API для Telegram Mini App с AI-репетитором английского.",
)

# ─── CORS ────────────────────────────────────────────────────────────────────
# Разрешаем запросы с Mini App и Admin Panel
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.MINIAPP_URL,
        settings.admin_url,
        # Для локальной разработки
        "http://localhost:5173",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Healthcheck ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health() -> dict:
    """Проверка работоспособности сервиса. Используется Docker healthcheck."""
    return {"status": "ok", "service": "backend"}


# ─── REST API v1 ──────────────────────────────────────────────────────────────
@app.get("/api/v1/ping", tags=["API v1"])
async def ping() -> dict:
    """Тестовый эндпоинт. Проверяет доступность API."""
    return {"pong": True, "version": "0.1.0"}


# ─── WebSocket — голосовой диалог (заглушка) ──────────────────────────────────
@app.websocket("/ws/voice")
async def ws_voice(websocket: WebSocket):
    """
    WebSocket-эндпоинт для голосового диалога с AI-репетитором.

    ТЕКУЩЕЕ СОСТОЯНИЕ (Phase 0):
        Заглушка — принимает текстовые сообщения и возвращает их эхом.

    БУДУЩЕЕ (Phase 2):
        Интеграция с Google Gemini 2.5 Live API для двустороннего
        аудио-диалога в реальном времени.
    """
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            # TODO Phase 2: передать data в Gemini Live API и стримить ответ
            await websocket.send_text(f"[echo] {data}")
    except WebSocketDisconnect:
        pass
