# Исправление генерации грамматики: ручной деплой

Проект: `/var/www/englishbot`. База MySQL и пользователь: `megotim4y2`.
GitHub Actions остаётся отключённым; обновление сервера выполняется вручную.

Неудачная генерация и фоновая подготовка заданий больше не создают строки
в `sessions`. Сессия появляется при переходе к практике. Повторная активация
возвращает тот же ID, повторное завершение — сохранённый результат без
повторного начисления времени и попытки. Клиент отменяет запросы при выходе
и показывает ошибку сохранения с кнопкой повтора.

После запуска обновлённого backend старые незакрытые сессии грамматики
получат статус «прервана». Новые сессии без heartbeat закрываются примерно
через 3 минуты. Записи сохраняются; длительность и успешное прохождение
не выдумываются. Нулевые прерванные сессии не расходуют дневной лимит.

## 1. Получить код и применить миграцию

Выполнить на сервере. Миграция должна завершиться **до запуска нового backend**:
ORM теперь использует новые столбцы таблицы `sessions`. При ошибке остановиться
и устранить её до следующего шага. Для миграции нужны права `ALTER`,
`SELECT`, `UPDATE` и `INSERT` на базу.

```bash
cd /var/www/englishbot
git pull --ff-only origin main
mysql -u megotim4y2 -p megotim4y2 < db/migrations/0042_grammar_session_lifecycle.sql
mysql -u megotim4y2 -p megotim4y2 -e \
  "SELECT version FROM schema_version WHERE version=42; SHOW COLUMNS FROM sessions LIKE 'grammar%';"
```

Пароль вводится в приглашении MySQL. Ожидаются версия `42` и четыре столбца:
`grammar_status`, `grammar_last_seen_at`, `grammar_result`,
`grammar_preparation_id`. Миграцию можно повторять: она сохраняет данные.
Использовать текущие `.env` и адрес MySQL; заменять секреты не требуется.

## 2. Увеличить таймаут Nginx для грамматики

Обычный `location /` в конфигурации API использует стандартный таймаут
Nginx 60 секунд, а генерация с повторной попыткой может длиться дольше.
Обрыв ответа прокси может отображаться в браузере как `Failed to fetch`.
Backend теперь ограничивает всю генерацию 240 секундами и возвращает
понятную JSON-ошибку при таймауте; прокси должен дождаться этого ответа.

В активном HTTPS-блоке `server` для `api-english.krichigindocs.ru` добавить
следующий `location` рядом с `/api/listening/`. Если такой блок уже существует,
обновить его вместо добавления дубликата:

```nginx
location /api/grammar/ {
    proxy_pass http://127.0.0.1:8082;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_connect_timeout 10s;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
}
```

Блок также добавлен в `docker/nginx/vps-site/api-english.conf` репозитория.
`git pull` и пересборка Docker не обновляют конфигурацию системного Nginx.
Редактировать файл, который реально подключён через `/etc/nginx/sites-enabled/`.
Ниже пример имени файла из репозитория. Если на сервере имя отличается,
использовать соответствующий файл API из вывода `ls`.

```bash
ls -l /etc/nginx/sites-enabled/
sudo nano /etc/nginx/sites-enabled/api-english.conf
sudo nginx -t
sudo systemctl reload nginx
```

Перезагрузить конфигурацию только после успешного `nginx -t`. Если
`BACKEND_PORT` переопределён в `.env`, в `proxy_pass` использовать этот порт.

## 3. Пересобрать приложение

```bash
cd /var/www/englishbot
docker compose up -d --build backend miniapp admin
docker compose ps backend miniapp admin
curl -fsS http://127.0.0.1:8082/health
docker compose logs --tail=100 backend
```

Ожидается `{"status":"ok","service":"backend"}`. При другом `BACKEND_PORT`
использовать его и в `curl`. В логах не должно быть ошибок неизвестных столбцов
или `[grammar] session cleanup failed`. Пересборка APK для этого исправления
не нужна: меняются backend и веб-интерфейсы.

## 4. Проверить на телефоне

1. Перезагрузить приложение и админку, чтобы загрузить свежий JavaScript.
2. Открыть «Учить правила», прочитать теорию, затем выйти до практики.
   Новая сессия в админке не должна появиться.
3. Сгенерировать задания и пройти практику. В админке должна быть одна
   завершённая сессия с длительностью фактической практики.
4. Отменить генерацию или выйти из упражнений. Экран не должен возвращаться
   к упражнениям после запоздалого ответа. Начатая практика отмечается
   «прервана», при потере связи это произойдёт примерно через 3 минуты.
5. При ошибке генерации повторить запрос. Пустых сессий от неудачной
   генерации быть не должно. Если сохранение результата не удалось,
   нажать «Сохранить ещё раз»; это не создаёт новую сессию и не удваивает время.
6. В карточке тестового пользователя старые строки `0м 0с (в процессе)`
   должны стать `0м 0с (прервана)`, а успешно пройденная сессия сохранится.

## Регрессионные проверки

Серверные тесты используют настоящую локальную MySQL-базу с суффиксом
`_test` и подменённые LLM/MyTracker-запросы. Перед запуском на этой тестовой
базе должны быть применены миграции, включая 0042.

```bash
cd backend
TEST_DATABASE_URL='mysql+asyncmy://englishbot@127.0.0.1:3307/englishbot_mytracker_test?charset=utf8mb4' \
  python -m unittest discover -s tests -v
cd ..
node --test scripts/test_grammar_frontend.cjs scripts/test_mytracker_frontend.cjs
npm --prefix miniapp run build
npm --prefix admin run build
```

Адрес выше — локальная тестовая база, не production. Браузерные проверки
работают с настоящим React-экраном и подменяют сетевые ответы: обрыв связи,
отмена, запоздалый ответ, повтор старта и ошибка сохранения.
Для них нужны Playwright и Chromium, а также установленный `miniapp/node_modules`.
В отдельном терминале запустить Vite:

```bash
VITE_API_BASE=http://localhost:8082 npm --prefix miniapp run dev
```

Затем из корня проекта:

```bash
node --test scripts/test_grammar_browser.cjs
# Для системного Chromium вместо браузера Playwright:
CHROMIUM_PATH=/usr/bin/chromium node --test scripts/test_grammar_browser.cjs
```

Стандартные сборки проходят. Отдельный `tsc -p tsconfig.app.json --noEmit`
всё ещё выявляет существовавшие до исправления ошибки в других экранах
miniapp и типах метрик админки; новые ошибки этой правкой не добавлены.
