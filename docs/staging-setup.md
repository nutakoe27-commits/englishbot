# Тестовый стенд (staging) на том же VPS

Прод и тест живут на одной машине в **разных каталогах** и слушают **разные
порты** на `127.0.0.1`. Системный nginx разводит их по поддоменам.

| Компонент | Прод | Тест |
|---|---|---|
| Каталог | `/var/www/englishbot` | `/var/www/englishbot-staging/englishbot` |
| База | `megotim4y2` | `englishbot_staging` |
| Mini App / сайт | `englishbot.krichigindocs.ru` → 8081 | `englishbot-test.krichigindocs.ru` → 8091 |
| Backend API | `api-english.krichigindocs.ru` → 8082 | `api-english-test.krichigindocs.ru` → 8092 |
| Админка | `admin-english.krichigindocs.ru` → 8083 | `admin-english-test.krichigindocs.ru` → **9083** |
| Бот | боевой токен | **отдельный тестовый бот** |

Порты контейнеров задаются переменными `MINIAPP_PORT`, `BACKEND_PORT`,
`ADMIN_PORT` в `.env` соответствующего каталога (в `docker-compose.yml`
проставлены дефолты прода, поэтому боевой стенд менять не нужно).

---

## 1. DNS

В панели управления доменом `krichigindocs.ru` добавить **A-запись**:

```
admin-english-test    A    <IP вашего VPS>    TTL 300
```

Если тестовые поддомены `englishbot-test` и `api-english-test` уже заведены —
добавляется только запись для админки. Проверка (запись должна отдавать IP
сервера):

```bash
dig +short admin-english-test.krichigindocs.ru
```

## 2. Сертификат Let's Encrypt

Новый поддомен нужно добавить в существующий сертификат:

```bash
sudo certbot --expand -d englishbot.krichigindocs.ru \
  -d api-english.krichigindocs.ru \
  -d admin-english.krichigindocs.ru \
  -d englishbot-test.krichigindocs.ru \
  -d api-english-test.krichigindocs.ru \
  -d admin-english-test.krichigindocs.ru
```

Перечислять нужно **все** домены сразу — certbot пересоздаёт сертификат по
этому списку. Уточнить текущий список: `sudo certbot certificates`.

## 3. Конфиг nginx для тестовой админки

```bash
sudo cp /var/www/englishbot-staging/englishbot/docker/nginx/vps-site/admin-english-test.conf \
        /etc/nginx/sites-available/admin-english-test.conf
sudo ln -sf /etc/nginx/sites-available/admin-english-test.conf \
            /etc/nginx/sites-enabled/admin-english-test.conf
sudo nginx -t && sudo systemctl reload nginx
```

## 4. Переменные окружения тестового стенда

В `.env` каталога тестового стенда должны быть заданы (помимо общих):

```ini
# Порты, чтобы не конфликтовать с продом (значения — пример; главное,
# чтобы они совпадали с proxy_pass в nginx-конфигах тестовых поддоменов)
MINIAPP_PORT=9081
BACKEND_PORT=9082
ADMIN_PORT=9083

# ОБЯЗАТЕЛЬНО для админки: без этого она собирается с относительным /api,
# уходит на собственный домен, получает index.html вместо JSON и падает
# в белый экран.
VITE_API_BASE=https://api-english-test.krichigindocs.ru

# Тестовая база
DATABASE_URL=mysql+asyncmy://englishbot_staging:PASSWORD@host.docker.internal:3306/englishbot_staging?charset=utf8mb4

# Тестовый бот и тестовые домены
BOT_TOKEN=<токен тестового бота>
BOT_USERNAME=<имя тестового бота без @>
MINIAPP_URL=https://englishbot-test.krichigindocs.ru
API_PUBLIC_URL=https://api-english-test.krichigindocs.ru
ADMIN_HOST=admin-english-test.krichigindocs.ru

# Отдельный токен админки — не переиспользуем боевой
ADMIN_TOKEN=<openssl rand -hex 32>

# Отдельный секрет JWT, чтобы токены прода и теста не подходили друг к другу
AUTH_JWT_SECRET=<openssl rand -hex 32>
BACKEND_BOT_SECRET=<openssl rand -hex 32>
```

Важно: `ADMIN_HOST` попадает в CORS-белый список backend'а — без него
тестовая админка получит ошибку CORS при запросах к API.

Mini App собирается с `VITE_API_BASE`, поэтому при пересборке тестового
стенда передайте тестовый API (см. `docker-compose.yml`, секция
`miniapp.build.args`) либо задайте в `.env`:

```ini
VITE_API_BASE=https://api-english-test.krichigindocs.ru
```

## 5. Запуск

```bash
cd /var/www/englishbot-staging/englishbot
docker compose up -d --build backend bot miniapp admin
docker compose ps          # у всех статус running/healthy
curl -s http://127.0.0.1:9082/health    # {"status":"ok"}
curl -s http://127.0.0.1:9083 | head -5 # HTML админки
```

Открыть `https://admin-english-test.krichigindocs.ru`, ввести `ADMIN_TOKEN`
из `.env` тестового стенда.

## Диагностика

| Симптом | Причина и что делать |
|---|---|
| 502 от nginx | Контейнер не поднялся или слушает другой порт — `docker compose ps`, `docker compose logs admin` |
| Админка просит токен по кругу | Неверный `ADMIN_TOKEN` либо backend не видит его: `docker compose exec backend printenv ADMIN_TOKEN` |
| Ошибка CORS в консоли браузера | В `.env` не задан `ADMIN_HOST` тестового домена — задать и пересобрать backend |
| Сертификат невалиден | Домен не добавлен в сертификат — повторить шаг 2 |
| 404 от nginx (не от контейнера) | Ни один server-блок не совпал: конфиг не подключён, битый симлинк в `sites-enabled` или не сделан `reload`. Проверить: `sudo nginx -T \| grep -c admin-english-test` |
| `conflicting server name … ignored` в `nginx -t` | Для домена есть второй server-блок, один из них игнорируется. Найти: `grep -rln admin-english-test /etc/nginx/sites-enabled/` |
| Белый экран после ввода токена | Админка собрана без `VITE_API_BASE` → запросы уходят на её домен, SPA-fallback отдаёт index.html. Задать переменную и `docker compose up -d --build admin` |
| Порт занят | `sudo ss -ltnp \| grep 9083` — сменить `ADMIN_PORT` и порт в конфиге nginx |
