# Выпуск учебной программы в прод

Обновление выполняется вручную; GitHub Actions не требуется. Рабочая база
и пользователь — `megotim4y2`. Нужна схема 0001–0044: помощник миграций
находит пропуски и проверяет таблицы, столбцы и ENUM после применения.
Тестовый сервис использует отдельную базу `englishbot_staging`.

## Поведение доступа

- Первые сутки от регистрации: полный доступ (`FREE_TRIAL_DAYS=1`).
- После пробного доступа: один новый урок в сутки по умолчанию.
  Квота списывается при первом начале урока; продолжение её не списывает.
- Разговор бесплатного урока: отдельные 300 секунд на этот урок в сутки.
  Время сохраняется при переподключении и обновляется в 00:00 МСК.
  После трёх ответов и закрытия сессии можно завершить разговор; если
  время закончилось раньше, ответы сохраняются для продолжения.
- Самостоятельные разговор, грамматика и аудирование имеют собственные
  квоты. Сессии урока из этих квот исключены. Слова не имеют дневной квоты.
- Подписка, действующее место в школе и пробный доступ снимают ограничения.
  `FREE_PERIOD=true` также снимает их для всех пользователей.

Параметры в `settings_kv`: `free_lessons_per_day` (1),
`free_lesson_conversation_seconds` (300, минимум 1),
`free_seconds_per_day` (300), `free_grammar_per_day` (1),
`free_listening_per_day` (1). Мигратор сохраняет существующие значения.
Для `free_lessons_per_day` значение 0 означает отсутствие дневной квоты.
Исторические минуты, начисленные до обновления, не пересчитываются.

## Получить код и подготовить образы

Работать в каталоге рабочего checkout. Имя проекта взять из
`docker compose ls`: это должно быть существующее имя продового стека.

```bash
cd /ПУТЬ/К/РАБОЧЕМУ/englishbot
git pull --ff-only origin main
docker compose ls
PROD_PROJECT='ВСТАВЬ_ИМЯ_РАБОЧЕГО_COMPOSE_ПРОЕКТА'

docker compose -p "$PROD_PROJECT" build backend miniapp admin
```

В рабочем `.env` проверить `VITE_API_BASE=https://api-english.krichigindocs.ru`,
`FREE_PERIOD=false`, `FREE_TRIAL_DAYS=1` и рабочие настройки GPU, входа,
платежей и MyTracker. Существующие секреты сохранить. Длительность пробного
доступа на фронтенде также равна одним суткам (`miniapp/src/trial.ts`).
Для запуска программы поставить `LEARNING_ENABLED=true`.

## Сделать резервную копию

```bash
umask 077
mkdir -p backups
BACKUP_SQL="backups/megotim4y2-before-learning-$(date +%Y%m%d-%H%M%S).sql"
mysqldump --single-transaction --quick --no-tablespaces \
  -u megotim4y2 -p megotim4y2 > "$BACKUP_SQL" && test -s "$BACKUP_SQL"
```

При ошибке резервного копирования сначала устранить её. Если MySQL размещён
не на этом хосте, использовать существующие параметры подключения.

## Проверить и применить недостающие миграции

Проверка ниже ничего не изменяет. В выводе будут `Applied versions` и
`Missing migrations`; полный URL базы и пароль не печатаются.

```bash
docker compose -p "$PROD_PROJECT" run --rm -T --no-deps \
  --entrypoint python \
  -v "$PWD/db/migrations:/migrations:ro" \
  -v "$PWD/scripts/migrate_database.py:/tmp/migrate_database.py:ro" \
  backend /tmp/migrate_database.py --directory /migrations \
  --expected-database megotim4y2 --expected-user megotim4y2
```

При наличии пропусков остановить рабочий backend, затем применить их:

```bash
docker compose -p "$PROD_PROJECT" stop backend

docker compose -p "$PROD_PROJECT" run --rm -T --no-deps \
  --entrypoint python \
  -v "$PWD/db/migrations:/migrations:ro" \
  -v "$PWD/scripts/migrate_database.py:/tmp/migrate_database.py:ro" \
  backend /tmp/migrate_database.py --directory /migrations \
  --expected-database megotim4y2 --expected-user megotim4y2 --apply
```

Ожидаемый итог: `Current application tables, columns and ENUM values verified`
и `All migration versions verified`. При ошибке устранить указанную причину
и повторить помощник до запуска нового backend. Новая 0044 добавляет
nullable-привязку сессии к уроку, восстанавливает известные привязки и
добавляет настройку времени разговора. Уроки, ответы и платежи сохраняются.

## Запустить и проверить

```bash
docker compose -p "$PROD_PROJECT" up -d backend miniapp admin
docker compose -p "$PROD_PROJECT" exec -T backend \
  curl -fsS http://localhost:8000/health
docker compose -p "$PROD_PROJECT" ps backend miniapp admin
docker compose -p "$PROD_PROJECT" logs --tail=150 backend
```

Проверить сохранённые маршруты Nginx и GPU-подключения по
[инструкции обновления](learning-modules-deploy.md). Каталог
`./data/learning` должен сохраняться между пересозданиями контейнеров.
Обновить приложение, чтобы получить новый frontend.

1. Бесплатный аккаунт вне пробных суток: блок доступа показывает остаток
   новых уроков и время обновления. После начала урок можно продолжить.
   Следующий новый урок предлагает подписку; диагностика после 20 занятий
   остаётся доступной.
2. Потратить свободные разговорные минуты и открыть разговор урока:
   у урока собственный остаток. Перезагрузка его не восстанавливает.
   Грамматика и подкаст урока сохраняют квоты самостоятельных режимов.
3. Пробный и платный аккаунты: уроки открываются без дневной квоты.
   Подтверждённая оплата обновляет доступ; незавершённый платёж его не
   открывает. Повторное уведомление не продлевает подписку второй раз.
4. Проверить доставку `lesson_started`, `lesson_completed`, событий оплаты
   и других настроенных конверсий в рабочий MyTracker/VK Ads. Тестовые
   аккаунты и тестовые платежи использовать на staging с его интеграциями.

## Быстро отключить программу

Поставить `LEARNING_ENABLED=false` в рабочем `.env`, затем:

```bash
docker compose -p "$PROD_PROJECT" up -d --force-recreate backend
```

Самостоятельные режимы останутся доступны, данные программы сохранятся.
Схему 0044 для такого отключения откатывать не требуется.

Автопроверки используют настоящую MySQL и API; внешние AI/STT/TTS и
платёжные вызовы подменяются. Живые учебные сценарии проверяются на
устройствах и стенде с рабочими GPU-подключениями.
