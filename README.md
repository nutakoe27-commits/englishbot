# AI English Tutor — Telegram Mini App

Telegram-приложение для практики английского с AI-репетитором. Внутри Telegram
пользователь либо **говорит** с голосовым тьютором (push-to-talk, живой диалог с
исправлением ошибок), либо **слушает** сгенерированный под него подкаст. Весь
AI-стек (LLM, распознавание и синтез речи) — **self-hosted** на GPU-сервере, без
внешних облачных API.

> Статус: продакшн. Голосовой и текстовый режимы, listening-подкасты, дуэли
> (Battle Mode), дневные квесты, стрики/медали, личный словарь, напоминания и
> win-back, приём платежей через ЮKassa и админ-панель — всё работает.

---

## Что умеет

- **🎙 Разговор (voice).** Push-to-talk: запись PCM 16 кГц → Whisper STT → vLLM
  (тьютор) → Kokoro TTS. Стриминг ответа, мягкое исправление ошибок (correction),
  перевод слова по тапу, объяснение исправлений.
- **💬 Чат (chat).** Тот же тьютор, но текстом — для ситуаций, где нельзя говорить.
- **🎧 Слушание (listening).** Генерация подкаста на 1/3/5/10/15 мин: выбор уровня
  (A2–C1), темы (News/Tech/Psychology/…), скорости речи и опциональной вставки
  слов из личного словаря. Текст скрыт, раскрывается тап-переводом.
- **⚔️ Battle Mode.** Дуэли между пользователями по inline-вызову в чатах, запись
  ответа и судейство через LLM.
- **🎯 Удержание.** Дневные квесты, стрики, медали (achievements), личный словарь,
  умные напоминания и win-back-рассылки.
- **🛠 Админ-панель.** Метрики (DAU/WAU/MAU, выручка), разбивка по режимам,
  онлайн «кто сейчас занимается» (live), поиск/профили юзеров, батлы, квесты,
  рассылки, режим техработ.
- **💳 Платежи.** Telegram Payments + ЮKassa (с фискализацией 54-ФЗ). Есть
  `FREE_PERIOD` — промо-режим без оплаты для всех.

---

## Стек

| Компонент | Технология |
|-----------|-----------|
| Backend | Python 3.11, FastAPI 0.115, uvicorn, WebSocket |
| ORM / БД | SQLAlchemy 2.0 (async) + asyncmy → **MySQL 8** (на хосте VPS) |
| Bot | Python 3.11, aiogram 3.13 (long polling) + внутренний HTTP-сервер |
| Mini App | React 18 + Vite 5 + TypeScript 5.5 + `@twa-dev/sdk` |
| Admin | React 18 + Vite 5 + TypeScript + recharts |
| LLM | **vLLM** (OpenAI-совместимый), Qwen3.5-35B-A3B-AWQ |
| STT | **faster-whisper** (WebSocket) |
| TTS | **Kokoro-82M** (WebSocket) |
| GPU | NVIDIA V100, проброс на VPS через reverse SSH-туннель |
| Прокси | системный Nginx на VPS + Let's Encrypt |
| Оркестрация | Docker Compose |
| CI/CD | GitHub Actions (SSH-деплой в `main`) |

> Redis и контейнерный MySQL в `docker-compose.yml` намеренно закомментированы:
> БД работает как сервис **на самом VPS**, а контейнеры ходят в неё через
> `host.docker.internal`. Кэш сейчас не используется (presence/онлайн — in-memory
> на single-worker backend).

---

## Архитектура

```
                          Telegram
                             │
            ┌────────────────┼─────────────────┐
            │                │                  │
      Mini App SPA      Bot (aiogram)     Admin SPA
   englishbot.*          long polling   admin-english.*
            │                │                  │
            ▼                ▼                  ▼
   ┌─────────────────── системный Nginx (VPS, 443) ───────────────────┐
   │  englishbot.* → :8081   api-english.* → :8082   admin-english.* → :8083 │
   └────────────────────────────────┬─────────────────────────────────┘
                                     │
                         Backend FastAPI (контейнер :8000)
                          /ws/voice · /api/* · /api/admin/*
                                     │
                 ┌───────────────────┼────────────────────┐
                 ▼                   ▼                    ▼
            MySQL 8 (хост)   bot:8080 (internal)   reverse SSH-туннель
                                                   к GPU-серверу (V100):
                                              vLLM :23333 · Whisper :23334
                                                    · Kokoro :23335
```

Docker-сервисы (`docker-compose.yml`) слушают только на `127.0.0.1`, наружу их
публикует системный Nginx:

| Сервис | Контейнер | Порт на хосте | Поддомен |
|--------|-----------|---------------|----------|
| backend | `:8000` | `127.0.0.1:8082` | `api-english.krichigindocs.ru` |
| miniapp | `:80` | `127.0.0.1:8081` | `englishbot.krichigindocs.ru` |
| admin | `:80` | `127.0.0.1:8083` | `admin-english.krichigindocs.ru` |
| bot | `:8080` (internal) | — | — (long polling) |

---

## Структура репозитория

```
englishbot/
├── backend/                 # FastAPI: голос, listening, API, админка
│   └── app/
│       ├── main.py          # точка входа, WS /ws/voice, /api/*
│       ├── voice.py         # voice/chat сессии (STT→LLM→TTS)
│       ├── listening.py     # генерация подкастов
│       ├── llm_providers.py # клиент vLLM (+ перевод, объяснения)
│       ├── stt_providers.py # клиент Whisper
│       ├── tts_providers.py # клиент Kokoro
│       ├── tutor_prompt.py  # сборка system-prompt тьютора
│       ├── presence.py      # in-memory реестр «онлайн сейчас»
│       ├── admin.py         # /api/admin/* (метрики, юзеры, онлайн)
│       ├── battle*.py       # Battle Mode
│       ├── quests.py / achievements.py / session_recap.py
│       ├── limits.py        # дневные лимиты / FREE_PERIOD
│       └── db/              # SQLAlchemy: engine, models, repo
├── bot/                     # aiogram 3: команды, платежи, напоминания, win-back
│   └── app/ (main, reminders, internal_http, backend_client)
├── miniapp/                 # React Mini App (speaking + listening)
├── admin/                   # React админ-панель
├── db/migrations/           # SQL-миграции (применяются вручную)
├── docker/nginx/vps-site/   # конфиги системного Nginx (эталон)
├── v100/                    # серверные скрипты для GPU (Kokoro TTS)
├── docs/                    # подробная документация
└── docker-compose.yml
```

---

## AI-пайплайн (self-hosted)

Никаких облачных AI-API. Три сервиса крутятся на GPU-сервере (V100) и
пробрасываются на VPS через reverse SSH-туннель:

| Сервис | Протокол | Порт (туннель) | Назначение |
|--------|----------|----------------|------------|
| vLLM | HTTP (OpenAI-compatible) | `23333` | реплики тьютора, генерация подкастов, перевод |
| Whisper | WebSocket | `23334` | распознавание речи (PCM 16 кГц → текст) |
| Kokoro | WebSocket | `23335` | синтез речи (текст → PCM 24 кГц) |

Backend обращается к ним по `host.docker.internal:<порт>` (см. `extra_hosts` в
`docker-compose.yml`). Настройка — в [`docs/local_llm_setup.md`](docs/local_llm_setup.md),
[`docs/v100_vllm_systemd.md`](docs/v100_vllm_systemd.md) и
[`docs/voice-pipeline.md`](docs/voice-pipeline.md).

---

## База данных

MySQL 8 на хосте VPS. Миграции — обычные SQL-файлы в `db/migrations/`,
применяются **вручную** (CI их не накатывает), идемпотентны, версия пишется в
таблицу `schema_version`.

```bash
mysql -u <user> -p <db> < db/migrations/0008_listening_mode.sql
```

Основные таблицы: `users`, `sessions` (mode = voice/chat/listening), `daily_usage`,
`user_vocabulary`, `user_mistakes`, `user_quests` / `quests_catalog`,
`user_achievements`, `battles`, `payments`, `settings_kv`, `schema_version`.

Схема и порядок применения — в [`docs/DATABASE_SETUP.md`](docs/DATABASE_SETUP.md).

---

## Переменные окружения

Полный список с комментариями — в [`.env.example`](.env.example). Ключевые:

| Переменная | Назначение |
|-----------|-----------|
| `BOT_TOKEN`, `BOT_USERNAME` | токен и username бота (@BotFather) |
| `MINIAPP_URL`, `ADMIN_HOST` | домены (CORS, кнопки бота) |
| `DATABASE_URL` | `mysql+asyncmy://user:pwd@host.docker.internal:3306/db?charset=utf8mb4` |
| `VLLM_BASE_URL`, `VLLM_MODEL_NAME`, `VLLM_API_KEY` | LLM |
| `WHISPER_STT_URL`, `WHISPER_STT_LANGUAGE` | STT |
| `KOKORO_TTS_URL`, `KOKORO_TTS_VOICE`, `KOKORO_TTS_SPEED` | TTS |
| `ADMIN_IDS`, `ADMIN_TOKEN` | доступ к админке (`X-Admin-Token`) |
| `YOOKASSA_PROVIDER_TOKEN`, `YOOKASSA_*` | платежи и фискализация |
| `FREE_PERIOD` | `1` = промо без оплаты для всех |
| `LLM_TIMEOUT_SEC` | таймаут одного хода LLM |

---

## Локальный запуск

```bash
# 1. Клонировать
git clone <repo-url> && cd englishbot

# 2. Заполнить окружение
cp .env.example .env      # как минимум BOT_TOKEN, DATABASE_URL, VLLM_*/WHISPER_*/KOKORO_*

# 3. Поднять сервисы (системный nginx/SSL для локалки не нужны)
docker compose up -d backend bot miniapp admin

# 4. Проверить backend
curl http://localhost:8082/health        # {"status":"ok","service":"backend"}
```

Фронтенды по отдельности (hot-reload):

```bash
cd miniapp && npm install && npm run dev   # http://localhost:5173
cd admin   && npm install && npm run dev
```

> Для полноценной работы голоса/подкастов нужен доступ к vLLM/Whisper/Kokoro
> (локально или через туннель). Без них backend поднимется, но AI-функции
> вернут 5xx.

---

## Деплой в прод

На VPS уже работает **системный Nginx** (общий с другими проектами), поэтому
контейнерные nginx/certbot отключены. Контейнеры публикуются на `127.0.0.1`,
а Nginx-конфиги-эталоны лежат в `docker/nginx/vps-site/`.

**Первый раз:**
1. DNS A-записи для трёх поддоменов → IP VPS.
2. `git clone` в `/var/www/englishbot`, создать `.env`.
3. Разложить конфиги из `docker/nginx/vps-site/` в `/etc/nginx/sites-enabled/`,
   получить сертификаты (Let's Encrypt/certbot), `sudo nginx -t && sudo systemctl reload nginx`.
4. Накатить миграции из `db/migrations/` (см. выше).
5. `docker compose up -d --build`.

**Последующие деплои — автоматически.** Пуш в `main` запускает
[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml): SSH на VPS →
`git reset --hard origin/main` → `docker compose up -d --build` → prune.

> ⚠️ CI **не** применяет миграции БД и не перекладывает Nginx-конфиги — это
> делается вручную при изменениях схемы или роутинга.

Подробно — в [`docs/deployment.md`](docs/deployment.md).

### GitHub Actions Secrets

| Secret | Описание |
|--------|----------|
| `VPS_HOST` | IP/hostname VPS |
| `VPS_SSH_KEY` | приватный SSH-ключ (деплой под `root`) |

---

## API (основное)

Backend (`api-english.krichigindocs.ru`):

- `GET /health` — healthcheck.
- `WS /ws/voice` — голосовой/текстовый диалог (STT → LLM → TTS).
- `POST /api/listening/generate` · `GET /api/listening/audio/{id}.wav` — подкасты.
- `GET/POST/DELETE /api/user-words` — личный словарь.
- `POST /api/translate` · `POST /api/explain-correction` — перевод/объяснения.
- `GET /api/me/progress` · `GET /api/me/achievements` — прогресс и медали.
- `GET /api/admin/*` — админка (метрики, юзеры, `/online`, батлы, квесты,
  рассылки); защищено заголовком `X-Admin-Token`.

Авторизация Mini-App-запросов — по подписи Telegram `initData`.

---

## Документация

- [`docs/deployment.md`](docs/deployment.md) — деплой на VPS.
- [`docs/DATABASE_SETUP.md`](docs/DATABASE_SETUP.md) — схема и миграции.
- [`docs/voice-pipeline.md`](docs/voice-pipeline.md) — голосовой конвейер.
- [`docs/local_llm_setup.md`](docs/local_llm_setup.md) — локальный AI-стек.
- [`docs/v100_vllm_systemd.md`](docs/v100_vllm_systemd.md) — vLLM на GPU как systemd-сервис.
