# Деплой AI English Tutor на VPS — пошаговая инструкция

Инструкция для Ubuntu 24.04 LTS. Предполагается, что на сервере уже установлены Docker и Docker Compose.

---

## Шаг 0: Установка Docker (если не установлен)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Выйти и зайти заново, чтобы применились права
```

---

## Шаг 1: Настройка DNS

Перед деплоем убедитесь, что в DNS-панели вашего домена настроены A-записи:

| Поддомен | Тип | Значение |
|----------|-----|---------|
| `englishbot.krichigindocs.ru` | A | `<IP вашего VPS>` |
| `api-english.krichigindocs.ru` | A | `<IP вашего VPS>` |
| `admin-english.krichigindocs.ru` | A | `<IP вашего VPS>` |

Дождитесь распространения DNS (обычно до 15 минут). Проверить:
```bash
dig englishbot.krichigindocs.ru +short
```

---

## Шаг 2: Подготовка директории на VPS

```bash
sudo mkdir -p /var/www/englishbot
sudo chown $USER:$USER /var/www/englishbot
cd /var/www/englishbot
```

---

## Шаг 3: Клонировать репозиторий

**Вариант A — через HTTPS (PAT):**
```bash
git clone https://<username>:<PAT>@github.com/<org>/englishbot.git .
```

**Вариант B — через SSH Deploy Key:**
```bash
# 1. Сгенерировать ключ на сервере
ssh-keygen -t ed25519 -C "deploy@vps" -f ~/.ssh/englishbot_deploy -N ""
# 2. Добавить ~/.ssh/englishbot_deploy.pub в GitHub → Settings → Deploy keys (Read-only)
# 3. Клонировать
GIT_SSH_COMMAND="ssh -i ~/.ssh/englishbot_deploy" \
  git clone git@github.com:<org>/englishbot.git .
```

---

## Шаг 4: Заполнить переменные окружения

```bash
cp .env.example .env
nano .env   # или vim .env
```

Обязательно заполнить:
- `BOT_TOKEN` — токен из @BotFather
- `BOT_WEBHOOK_SECRET` — любая случайная строка: `openssl rand -hex 32`
- `ADMIN_JWT_SECRET` — случайная строка: `openssl rand -hex 32`
- `LETSENCRYPT_EMAIL` — ваш email для уведомлений LE

---

## Шаг 5: Первый запуск сервисов (без SSL)

Поднимаем всё кроме nginx и certbot — они нужны для получения сертификатов:

```bash
docker compose up -d backend bot miniapp admin
```

Проверить, что backend работает:
```bash
curl http://localhost:8000/health
# Ожидаемый ответ: {"status":"ok","service":"backend"}
```

---

## Шаг 6: Инициализация SSL-сертификатов

```bash
# Сделать скрипт исполняемым
chmod +x docker/nginx/init-letsencrypt.sh

# Запустить (получит сертификаты для всех 3 поддоменов)
bash docker/nginx/init-letsencrypt.sh
```

Скрипт:
1. Создаёт временные самоподписанные сертификаты
2. Запускает nginx (для ACME challenge)
3. Получает настоящие сертификаты через certbot
4. Перезапускает nginx с настоящими сертификатами

> **Важно:** Если скрипт упал с ошибкой rate limit, установите `STAGING=1` в скрипте для тестирования, потом верните `STAGING=0`.

---

## Шаг 7: Запустить все сервисы

```bash
docker compose up -d
```

Проверить статус:
```bash
docker compose ps
docker compose logs -f
```

Проверить HTTPS:
```bash
curl https://api-english.krichigindocs.ru/health
```

---

## Шаг 8: Настройка автодеплоя через GitHub Actions

В репозитории на GitHub добавьте Secrets (Settings → Secrets → Actions):

| Secret | Значение |
|--------|---------|
| `VPS_HOST` | IP или hostname сервера |
| `VPS_SSH_KEY` | Приватный SSH-ключ (содержимое `~/.ssh/id_rsa` или deploy key) |

После этого каждый пуш в ветку `main` будет автоматически деплоиться через `.github/workflows/deploy.yml`.

---

## Управление сервисами

```bash
# Просмотр логов
docker compose logs -f backend
docker compose logs -f bot

# Перезапустить конкретный сервис
docker compose restart backend

# Остановить всё
docker compose down

# Полный rebuild с нуля
docker compose up -d --build

# Очистить неиспользуемые образы
docker image prune -f
```

---

## Подключение MySQL и Redis (Phase 1)

Когда база данных понадобится, раскомментируйте секции `mysql` и `redis` в `docker-compose.yml` и добавьте соответствующие переменные в `.env`:

```
MYSQL_ROOT_PASSWORD=strong_password_here
MYSQL_USER=englishbot
MYSQL_PASSWORD=another_strong_password
```

Затем:
```bash
docker compose up -d mysql redis
docker compose restart backend
```
