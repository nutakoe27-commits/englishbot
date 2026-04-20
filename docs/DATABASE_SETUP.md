# Настройка базы данных EnglishBot

Эта инструкция — для разового первого запуска БД на VPS (89.111.143.45).
MySQL 5.7 уже установлен на хосте; ничего поднимать не нужно — только
создать базу, пользователя и применить SQL-миграцию.

> ⚠️ Все команды выполняются из-под `root` на VPS. Все упоминания
> `<СЛОЖНЫЙ_ПАРОЛЬ>` нужно заменить на реальный пароль (16+ случайных
> символов, без `'` и `"`). Пароль нигде не записывай открыто — только
> в `.env`.

---

## Шаг 1. Сгенерировать пароль для пользователя БД

На любой машине:

```bash
openssl rand -base64 24
# пример вывода: kQ8hN2pV9mX7sR3tF6yL4wA1bC5dE0gH
```

Сохрани его в надёжное место — он понадобится дважды (в SQL и в `.env`).

---

## Шаг 2. Зайти на VPS и проверить, что MySQL 5.7 жив

```bash
ssh root@89.111.143.45
mysql --version
# должно показать что-то вроде: mysql  Ver 14.14 Distrib 5.7.x

systemctl status mysql        # или mysqld — зависит от дистрибутива
```

Если статус **active (running)** — переходи к шагу 3.

---

## Шаг 3. Создать базу и пользователя

Войди в MySQL под `root` (если у тебя пароль root установлен, MySQL спросит):

```bash
mysql -u root -p
```

В консоли MySQL выполни (вставь весь блок целиком, заменив пароль):

```sql
-- 1. База
CREATE DATABASE IF NOT EXISTS englishbot
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

-- 2. Пользователь.
--    Хост '%' — чтобы коннект из Docker-контейнера тоже работал.
--    Если потом захочешь жёстче — поменяй на реальную подсеть Docker.
CREATE USER IF NOT EXISTS 'englishbot'@'%'
    IDENTIFIED BY '<СЛОЖНЫЙ_ПАРОЛЬ>';

-- 3. Права только на эту базу. SELECT/INSERT/UPDATE/DELETE достаточно
--    для работы приложения; ALTER/CREATE — чтобы применять миграции.
GRANT SELECT, INSERT, UPDATE, DELETE,
      CREATE, ALTER, INDEX, DROP, REFERENCES
    ON englishbot.* TO 'englishbot'@'%';

FLUSH PRIVILEGES;

-- 4. Проверить
SHOW GRANTS FOR 'englishbot'@'%';
\q
```

---

## Шаг 4. Разрешить MySQL слушать сеть (если ещё нет)

По умолчанию MySQL 5.7 на Debian/Ubuntu биндится только на `127.0.0.1`,
а из Docker-контейнера хост виден как `host.docker.internal` →
маршрутизируется на `172.17.0.1` (или подобный). Нужно либо разрешить
бинд на 0.0.0.0, либо явно на `172.17.0.1`.

```bash
# Открыть конфиг
nano /etc/mysql/mysql.conf.d/mysqld.cnf
```

Найди строку `bind-address` и поменяй на:

```ini
bind-address = 0.0.0.0
```

> ⚠️ **Важно.** Это безопасно ровно потому, что **наружу 3306 закрыт
> файрволом** (см. шаг 5). Пользователь `englishbot` не имеет
> административных прав, и доступ только к одной базе.

Перезапусти MySQL:

```bash
systemctl restart mysql
```

Проверь, что слушает:

```bash
ss -tlnp | grep 3306
# должно показать: LISTEN 0 ... *:3306 ...
```

---

## Шаг 5. Закрыть 3306 наружу через файрвол

**Обязательно**, если у тебя есть публичный IP:

```bash
# UFW (Ubuntu)
ufw status
# Если активен:
ufw deny 3306/tcp
ufw reload

# Или iptables (минимально)
iptables -A INPUT -p tcp --dport 3306 ! -i lo ! -i docker0 -j DROP
```

Проверь снаружи (с другой машины):

```bash
nc -zv 89.111.143.45 3306
# должно: Connection timed out / refused
```

---

## Шаг 6. Применить миграцию (создать таблицы)

Перейди в проект:

```bash
cd /var/www/englishbot
git pull origin main          # подтянуть последнюю версию с db/migrations/
```

Применить:

```bash
mysql -u englishbot -p englishbot < db/migrations/0001_init.sql
# введи пароль englishbot
```

Если без ошибок — проверь:

```bash
mysql -u englishbot -p englishbot -e "SHOW TABLES;"
```

Должно вывести:

```
+----------------------+
| Tables_in_englishbot |
+----------------------+
| daily_usage          |
| payments             |
| schema_version       |
| sessions             |
| settings_kv          |
| users                |
+----------------------+
```

И версию миграции:

```bash
mysql -u englishbot -p englishbot -e "SELECT * FROM schema_version;"
```

---

## Шаг 7. Прописать `DATABASE_URL` и `ADMIN_IDS` в `.env`

```bash
nano /var/www/englishbot/.env
```

Добавь в конец (или замени, если уже есть):

```ini
# ─── База данных ─────────────────────────────────────────────────────────
DATABASE_URL=mysql+aiomysql://englishbot:<СЛОЖНЫЙ_ПАРОЛЬ>@host.docker.internal:3306/englishbot?charset=utf8mb4

# ─── Админы ──────────────────────────────────────────────────────────────
# Свой Telegram ID можно узнать у @userinfobot
ADMIN_IDS=123456789
```

> Если у тебя несколько админов — через запятую без пробелов:
> `ADMIN_IDS=123456789,987654321`

---

## Шаг 8. Пересобрать и перезапустить backend

```bash
cd /var/www/englishbot
docker compose build backend
docker compose up -d backend
docker compose logs --tail 50 backend
```

В логах должно появиться:

```
INFO  app.main: DB ready=True
```

Если `DB ready=False` или ошибка `Can't connect to MySQL server` — см.
раздел «Что если что-то пошло не так» ниже.

---

## Что если что-то пошло не так

**`Can't connect to MySQL server on 'host.docker.internal'`**
Не настроен `extra_hosts` либо MySQL биндится только на `127.0.0.1`.
Проверь шаг 4 (`bind-address = 0.0.0.0`) и `docker-compose.yml` —
у `backend` должно быть:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

(уже есть в репозитории).

**`Access denied for user 'englishbot'@'172.17.0.1'`**
Пользователь создан как `'englishbot'@'localhost'` вместо `@'%'`.
Перевыполни в MySQL:

```sql
DROP USER 'englishbot'@'localhost';
CREATE USER 'englishbot'@'%' IDENTIFIED BY '<пароль>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES
    ON englishbot.* TO 'englishbot'@'%';
FLUSH PRIVILEGES;
```

**`Unknown collation: 'utf8mb4_0900_ai_ci'`**
MySQL 5.7 не поддерживает 8.0-collation. Миграция использует
`utf8mb4_unicode_ci` — должна пройти. Если ошибка где-то ещё — пришли
лог.

**Хочу ронять и пересоздавать с нуля при разработке**

```sql
DROP DATABASE englishbot;
CREATE DATABASE englishbot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- и снова применить 0001_init.sql
```

---

## Что дальше

После успешного шага 8 **схема готова, но пока не используется** —
это PR B (только инфраструктура). Учёт минут, лимиты, экран
«лимит исчерпан», админка — будут в PR C и PR D.
