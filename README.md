# AI English Tutor — Telegram Mini App

Telegram Mini App для разговорной практики английского с AI-репетитором. Пользователь открывает приложение прямо из Telegram, нажимает кнопку разговора и практикует английский с голосовым AI-агентом на базе Google Gemini 2.5 Live API. На текущем этапе (Phase 0) реализован каркас без голоса: бот отвечает на команды, Mini App показывает заглушку, backend возвращает healthcheck.

---

## Стек

| Компонент | Технология |
|-----------|-----------|
| Backend   | Python 3.11 + FastAPI + uvicorn |
| Bot       | Python 3.11 + aiogram 3 |
| Mini App  | React 18 + Vite 5 + TypeScript + @twa-dev/sdk |
| Admin     | React 18 + Vite 5 + TypeScript (stub) |
| База данных | MySQL 8 (Phase 1, сейчас закомментирована) |
| Кэш       | Redis 7 (Phase 1, сейчас закомментирован) |
| Прокси    | Nginx + Let's Encrypt (certbot) |
| Оркестрация | Docker Compose |

---

## Схема поддоменов

```
englishbot.krichigindocs.ru   → Mini App (React SPA)
api-english.krichigindocs.ru  → Backend FastAPI (/api/* + /ws/*)
admin-english.krichigindocs.ru → Admin Panel (React SPA)
```

---

## Локальный запуск (без SSL)

```bash
# 1. Клонировать репозиторий
git clone <repo-url>
cd englishbot

# 2. Скопировать и заполнить переменные окружения
cp .env.example .env
# Отредактировать .env: BOT_TOKEN, MINIAPP_URL и т.д.

# 3. Поднять сервисы (без nginx/certbot для локальной разработки)
docker compose up -d backend bot miniapp admin

# Backend доступен на http://localhost:8000
# Healthcheck: http://localhost:8000/health
```

---

## Деплой в прод (автоматический)

1. Настроить DNS-записи A для всех трёх поддоменов → IP вашего VPS.
2. На VPS создать директорию и склонировать репо:
   ```bash
   sudo mkdir -p /var/www/englishbot
   cd /var/www/englishbot
   git clone <repo-url> .
   ```
3. Скопировать `.env.example` → `.env` и заполнить все переменные.
4. Получить SSL-сертификаты:
   ```bash
   bash docker/nginx/init-letsencrypt.sh
   ```
5. Поднять все сервисы:
   ```bash
   docker compose up -d
   ```
6. Дальнейшие деплои — автоматически через GitHub Actions при пуше в `main`.

Подробнее см. [docs/deployment.md](docs/deployment.md).

---

## GitHub Actions Secrets

| Secret | Описание |
|--------|----------|
| `VPS_HOST` | IP или hostname VPS |
| `VPS_SSH_KEY` | Приватный SSH-ключ для доступа к VPS |
