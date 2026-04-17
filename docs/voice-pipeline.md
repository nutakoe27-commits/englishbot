# Voice Pipeline — Архитектура голосового диалога

## Обзор

Голосовой режим реализован как нативный speech-to-speech диалог через Gemini Live API.
Браузер записывает речь пользователя, отправляет на backend по WebSocket, backend
пересылает в Gemini и транслирует аудио-ответ обратно в браузер.

## Схема потока данных

```
┌───────────────────────┐        ┌──────────────────────────────┐        ┌─────────────────┐
│    Browser (Mini App) │        │    Backend (FastAPI)         │        │  Gemini Live    │
│                       │        │                              │        │  API (Google)   │
│  Microphone           │        │                              │        │                 │
│      │                │        │                              │        │                 │
│  getUserMedia()       │        │                              │        │                 │
│      │                │        │                              │        │                 │
│  AudioContext         │        │                              │        │                 │
│  AudioWorklet         │        │                              │        │                 │
│  (pcm-recorder-       │        │                              │        │                 │
│   worklet.js)         │        │                              │        │                 │
│      │                │        │  /ws/voice                   │        │                 │
│  PCM 16kHz 16-bit ────┼──ws────┼──► validate initData        │        │                 │
│  (binary frames)      │        │      │                       │        │                 │
│                       │        │  run_gemini_session()        │        │                 │
│                       │        │      │                       │        │                 │
│                       │        │  send_realtime_input() ──────┼──wss───┼─► model input  │
│                       │        │                              │        │                 │
│                       │        │  session.receive() ◄─────────┼──wss───┼── audio parts  │
│                       │        │      │                       │        │                 │
│  PCM 24kHz 16-bit ◄───┼──ws────┼── send_bytes(audio)        │        │                 │
│  AudioBuffer queue    │        │                              │        │                 │
│  AudioContext.play()  │        │  send_json(transcript) ──────┼──ws────┼──► log entry   │
│                       │        │                              │        │                 │
│  Dialog log (text) ◄──┼──ws────┼──                            │        │                 │
└───────────────────────┘        └──────────────────────────────┘        └─────────────────┘
```

## Форматы аудио

| Направление           | Формат                          | Частота  | Разрядность | Каналы |
|-----------------------|---------------------------------|----------|-------------|--------|
| Браузер → Backend     | PCM raw, little-endian          | 16 000 Гц | 16-bit      | 1 (mono) |
| Backend → Gemini Live | `audio/pcm;rate=16000`          | 16 000 Гц | 16-bit      | 1 (mono) |
| Gemini Live → Backend | PCM raw, little-endian          | 24 000 Гц | 16-bit      | 1 (mono) |
| Backend → Браузер     | PCM raw (binary WS frames)      | 24 000 Гц | 16-bit      | 1 (mono) |

### Конвертация в браузере (AudioWorklet)

`pcm-recorder-worklet.js` выполняет:
1. Принимает Float32-семплы на нативной частоте AudioContext (44100 или 48000 Гц)
2. Lowpass-фильтрация (IIR) для предотвращения алиасинга
3. Линейная интерполяция (downsampling) до 16 000 Гц
4. Конвертация Float32 → Int16 (PCM 16-bit)
5. Буферизация по 320 семплов (20 мс) и отправка через `port.postMessage()`

## Конфигурация Gemini Live сессии

```python
types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
        )
    ),
    system_instruction=types.Content(
        parts=[types.Part(text=SYSTEM_PROMPT)],
        role="user",
    ),
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
)
```

Доступные голоса: `Puck`, `Charon`, `Kore`, `Fenrir`, `Aoede`, `Zephyr`.
Управляется переменной окружения `GEMINI_VOICE`.

## Валидация Telegram initData

Каждое WebSocket-соединение проверяет подпись Telegram Mini App initData:

1. Браузер передаёт `initData` как query-параметр: `?init_data=<url-encoded>`
2. Backend вычисляет `HMAC-SHA256(data_check_string, secret_key)`
   - `secret_key = HMAC-SHA256("WebAppData", BOT_TOKEN)`
   - `data_check_string = sorted key=value pairs joined by \n`
3. Сравнивает с полем `hash` в initData
4. В `ENVIRONMENT=production` — невалидная подпись = разрыв с кодом 4001
5. В `ENVIRONMENT=development` — проверка пропускается (удобно для тестирования)

## Отладка

### Подключение к WebSocket вручную (wscat)

```bash
# Установить: npm install -g wscat
# В dev-режиме (без initData)
wscat -c "wss://api-english.krichigindocs.ru/ws/voice"

# Отправить аудио (PCM 16kHz файл)
# После подключения — wscat не поддерживает бинарные сообщения напрямую,
# используйте websocat:
websocat -b "wss://api-english.krichigindocs.ru/ws/voice" < sample_16k_16bit_mono.pcm
```

### Генерация тестового PCM-файла

```bash
# ffmpeg: конвертируем любой аудиофайл в нужный формат
ffmpeg -i input.mp3 -ar 16000 -ac 1 -f s16le output_16k.pcm
```

### Просмотр логов backend

```bash
# На VPS через docker compose
docker compose logs -f backend

# Пример ожидаемых логов при успешном соединении:
# INFO: WebSocket /ws/voice принят: client=... user_id=123456
# INFO: Открываем Gemini Live сессию: model=gemini-2.5-flash-preview-native-audio-dialog voice=Puck
# INFO: Gemini Live сессия установлена
# INFO: Gemini Live сессия завершена
# INFO: Сессия /ws/voice завершена: client=...
```

### Healthcheck API

```bash
curl https://api-english.krichigindocs.ru/health
# {"status":"ok","service":"backend"}

curl https://api-english.krichigindocs.ru/api/v1/ping
# {"pong":true,"version":"0.2.0"}
```

### Проверка переменных окружения

```bash
# Убедиться что GEMINI_API_KEY задан:
docker compose exec backend env | grep GEMINI
```

## Переменные окружения (backend)

| Переменная       | Обязательна | По умолчанию                                    | Описание                          |
|------------------|-------------|--------------------------------------------------|-----------------------------------|
| `GEMINI_API_KEY` | Да          | —                                                | API-ключ Google AI Studio         |
| `GEMINI_MODEL`   | Нет         | `gemini-2.5-flash-preview-native-audio-dialog`  | Модель для нативного аудио        |
| `GEMINI_VOICE`   | Нет         | `Puck`                                           | Голос AI-репетитора               |
| `BOT_TOKEN`      | Production  | —                                                | Telegram Bot Token для валидации  |
| `ENVIRONMENT`    | Нет         | `production`                                     | `development` отключает валидацию |

## Известные ограничения (MVP)

- Без БД: история диалога не сохраняется между сессиями
- Push-to-talk: нет режима always-on VAD (можно добавить в будущем через `ActivityStart/End`)
- Одна сессия на соединение: переподключение создаёт новый контекст Gemini
- Контекстное окно ограничено (~25 600 токенов) — при длинных разговорах модель теряет начало
