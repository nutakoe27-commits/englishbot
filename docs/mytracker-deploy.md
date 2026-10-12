# Деплой событий MyTracker → VK Ads

База данных и пользователь MySQL: **`megotim4y2`**.
Проект на сервере: `/var/www/englishbot`.

GitHub Actions отключён владельцем. Пуш в `main` публикует код;
обновление сервера выполняется вручную по командам ниже.

Порядок: получить код → применить миграции → проверить таблицы →
пересобрать backend/miniapp → проверить API и новые события.
Если любой шаг завершился ошибкой, устранить её до следующего шага.
Пользователю MySQL нужны права `CREATE`, `SELECT` и `INSERT` на эту базу,
помимо текущих прав приложения.

В `.env` должны остаться ваши реальные значения:

```ini
DATABASE_URL=mysql+asyncmy://megotim4y2:<ваш_пароль>@host.docker.internal:3306/megotim4y2?charset=utf8mb4
MYTRACKER_APP_ID=<ID Android-приложения в MyTracker>
MYTRACKER_S2S_TOKEN=<существующий S2S API-ключ>
ADMIN_TOKEN=<существующий токен админки>
```

Это шаблон: не заменяйте текущий пароль и адрес MySQL на буквальные
значения в угловых скобках. Используйте текущий адрес базы из `.env`;
`host.docker.internal:3306` — адрес из принятой Docker-конфигурации.
Пароли и API-ключи в Git не добавляются.

## Команды ручного деплоя

Выполнить на сервере из папки проекта:

```bash
cd /var/www/englishbot
set -e
git pull --ff-only origin main

# -p запросит пароль пользователя megotim4y2; он не попадёт в историю shell.
mysql -u megotim4y2 -p megotim4y2 < db/migrations/0038_mytracker_installs.sql
mysql -u megotim4y2 -p megotim4y2 < db/migrations/0041_mytracker_events.sql
mysql -u megotim4y2 -p megotim4y2 -e \
  "SELECT version FROM schema_version WHERE version IN (38,41); SHOW TABLES LIKE 'mytracker%';"

docker compose up -d --build backend miniapp
docker compose ps backend miniapp
curl -fsS http://127.0.0.1:8082/health
```

Ожидаются обе версии, обе таблицы и ответ backend
`{"status":"ok","service":"backend"}`. Если порт настроен через
`BACKEND_PORT`, используйте его вместо 8082.

Если миграция остановилась из-за доступа к БД, проверьте пароль и права
существующего пользователя и повторите её. Новый MyTracker-токен для
ошибки миграции не нужен. Миграции идемпотентны: повторное применение
сохраняет существующие связки, события и их статусы доставки.

Если MySQL-клиента на хосте нет, вместо двух команд `mysql` можно использовать
проверенный помощник в существующем backend-образе. Он читает пароль
из `DATABASE_URL`, проверяет имена базы/пользователя и применяет только
миграции 0038/0041. Запускать **до** `docker compose up --build`:

```bash
docker compose run --rm -T --no-deps \
  -v "$PWD/db/migrations:/migrations:ro" \
  -v "$PWD/scripts/apply_mytracker_migrations.py:/tmp/apply_mytracker_migrations.py:ro" \
  backend python /tmp/apply_mytracker_migrations.py \
    --directory /migrations \
    --expected-database megotim4y2 \
    --expected-user megotim4y2
```

Ожидаемый итог: `MyTracker tables and versions 38/41 verified`.
Уже применённые миграции пропускаются, а наличие таблиц проверяется заново.

## Проверка доставки без вывода секретов

На сервере выполните:

```bash
cd /var/www/englishbot
docker compose exec -T backend python - <<'PY_STATUS'
import json
import urllib.request
from app.config import settings
req = urllib.request.Request(
    "http://127.0.0.1:8000/api/analytics/mt-status",
    headers={"X-Admin-Token": settings.ADMIN_TOKEN or ""},
)
with urllib.request.urlopen(req, timeout=10) as response:
    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
PY_STATUS
```

`tracking_enabled=true` подтверждает наличие конфигурации, а рост `accepted`
после нового действия — успешные ответы S2S API. `pending` и `retrying`
показывают очередь. Секретный токен в ответ не входит.

Если данные не отправляются:

```bash
docker compose logs --tail=200 backend | rg '\[mytracker\]'
```

Если `rg` на сервере не установлен, используйте `grep '\[mytracker\]'`.
`HTTP=401/403` требует проверки прав S2S-ключа на выбранное приложение;
`transport=...` — доступа к `tracker-s2s.my.com` по HTTPS;
`delivery queue failed` — проверки миграций и доступа к таблице очереди.

## Приёмка на телефоне и в кабинетах

1. Откройте установленное Android-приложение и зарегистрируйте новый
   аккаунт. Проверьте `registration` в **Android-приложении** MyTracker.
   Вход старого аккаунта даёт `login`.
2. Начните занятие: должно появиться `session_started` с параметром `mode`.
3. Успешный подтверждённый платёж создаёт `subscription_paid` и выручку
   `customRevenue`. Не отправляйте вымышленные покупки в боевой счётчик.
4. В MyTracker проверьте передачу этих событий в нужный кабинет VK Ads,
   затем их доступность для соответствующего Android-приложения в VK Ads.
   Для оптимизации по оплате выбирайте одну конверсию, чтобы не считать
   выручку и `subscription_paid` двумя покупками.

Если `linked_installs` не растёт после входа, проверить передачу SDK UUID
через `mt_iid`. Пересборка APK нужна только если текущий APK не передаёт
этот идентификатор; обновление backend/веб-части само по себе её не требует.
Файлы и команды Bubblewrap указаны в [основной инструкции](mytracker.md).

Приём S2S-запроса, отображение в MyTracker и появление в VK Ads — разные
этапы. Проверки в облачной среде не подтверждают завершение обработки
в ваших рекламных кабинетах. Старые пропущенные события автоматически
не восстанавливаются.
