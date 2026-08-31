# Телеграм-бот на зарубежном хосте

## Зачем

Доступ к `api.telegram.org` с российского VPS пропадает рывками: в одну
минуту хост отвечает за 0.14 с, в следующую уходит в таймаут. Это
провайдерская фильтрация, и починить её на своей стороне нельзя — в DNS,
маршрутах и iptables всё исправно (разбор был подробный, см. историю).

Всё остальное на российской площадке живёт хорошо: сайт, API, оплата через
ЮKassa, туннель до GPU-сервера. Переезжает **только бот**.

## Что меняется, и это всё

| Что | Было | Стало |
|---|---|---|
| `BACKEND_URL` | `http://backend:8000` | `https://api-english.krichigindocs.ru` |
| `DATABASE_URL` | `host.docker.internal:3306` | `127.0.0.1:3307` через SSH-туннель |

Больше ничего. Запросы бота к API и так подписаны общим секретом
`X-Bot-Secret`, так что публичный адрес ничего не ослабляет.

**Обратных вызовов нет.** Backend бота никуда не дёргает: внутренний
HTTP-сервер бота (`bot/app/internal_http.py`) отвечает только на `ping`,
активных endpoint'ов там не осталось. Это важно — иначе пришлось бы
пробрасывать туннель ещё и в обратную сторону.

Бот не ходит ни в ЮKassa напрямую (чеки и суммы уходят в Telegram вместе с
инвойсом), ни на GPU-сервер. Единственные его собеседники — Telegram,
наш API и MySQL.

## Где размещать

Нужна площадка за пределами российской фильтрации: Нидерланды, Германия,
Польша. Казахстан, скорее всего, тоже подойдёт, но он ближе к тем же
маршрутам, и риск повторить проблему выше.

Ресурсы боту нужны скромные: одно ядро, гигабайт памяти, десять гигабайт
диска. Он не считает и не хранит — только принимает апдейты и ходит в базу.

## Доступ к базе

Бот читает и пишет MySQL напрямую: напоминания, стрики, `users.reminder_*`.
Выставлять MySQL в интернет нельзя, поэтому — **SSH-туннель** с
зарубежного хоста на прод. База остаётся слушать только localhost, наружу
не смотрит ничего.

Альтернатива поприличнее — WireGuard между хостами. И совсем правильный
конец истории — убрать у бота прямой доступ к базе и увести все его запросы
за `/api/internal/*`. Это заметный рефакторинг `reminders.py`, поэтому
сейчас туннель.

### На прод-сервере: ключ только для туннеля

```bash
# отдельный пользователь без шелла
sudo useradd -m -s /usr/sbin/nologin tunnel
sudo mkdir -p /home/tunnel/.ssh && sudo chmod 700 /home/tunnel/.ssh
```

Ключ генерируется **на зарубежном хосте** (см. ниже), его публичную часть
кладём сюда с жёсткими ограничениями — этот ключ не сможет ничего, кроме
проброса на MySQL:

```
# /home/tunnel/.ssh/authorized_keys
command="/bin/false",no-agent-forwarding,no-X11-forwarding,no-pty,permitopen="127.0.0.1:3306" ssh-ed25519 AAAA... bot-tunnel
```

**Не копируй эту строку через буфер обмена.** Она длинная и рвётся: у нас
она дважды приехала битой — сначала потерялся тип ключа `ssh-ed25519`,
потом обрезался сам блоб, причём во второй раз `awk` насчитал правильные
четыре поля и подделка выглядела как исправная строка. Заметно это только
по размеру файла (должно быть около 185 байт) и по отпечатку.

Надёжный способ — передать строку одним куском base64, в котором нет
пробелов и переносов. На зарубежном хосте:

```bash
printf 'command="/bin/false",no-agent-forwarding,no-X11-forwarding,no-pty,permitopen="127.0.0.1:3306" %s\n' \
  "$(cat /root/.ssh/et_tunnel.pub)" | base64 -w0; echo
ssh-keygen -lf /root/.ssh/et_tunnel.pub     # запомнить отпечаток
```

На проде:

```bash
echo '<base64-строка>' | base64 -d > /home/tunnel/.ssh/authorized_keys
sudo chown -R tunnel:tunnel /home/tunnel/.ssh
sudo chmod 600 /home/tunnel/.ssh/authorized_keys
sudo chown tunnel:tunnel /home/tunnel && sudo chmod 755 /home/tunnel
ssh-keygen -lf /home/tunnel/.ssh/authorized_keys   # отпечаток должен совпасть
```

Если base64 обрежется при копировании, `base64 -d` выругается сам — молча
битого файла уже не получится. Отпечатки на обеих сторонах обязаны
совпадать.

Ещё одна мина: `useradd` без пароля оставляет учётку в состоянии, которое
`passwd -S` показывает буквой `L`. Само по себе это авторизацию по ключу не
ломает (буква `L` одинаково выводится и для `!`, и для `*`), так что
диагноз по ней ставить нельзя — смотри лог sshd на проде, он называет
причину прямым текстом:

```bash
journalctl -u ssh --since '10 min ago' --no-pager | grep -i tunnel | tail -20
```

### Проверить пользователя MySQL

Через туннель соединение приходит на прод как `localhost`, а не с адреса
docker-сети. Пользователь должен быть заведён под такой хост:

```sql
SELECT user, host FROM mysql.user WHERE user = 'megotim4y2';
```

Если есть только `%` — всё в порядке, он покрывает и localhost. Если только
адрес docker-сети (`172.%`) — нужно добавить:

```sql
CREATE USER 'megotim4y2'@'localhost' IDENTIFIED BY '<пароль>';
GRANT ALL PRIVILEGES ON megotim4y2.* TO 'megotim4y2'@'localhost';
FLUSH PRIVILEGES;
```

### На зарубежном хосте: туннель как служба

```bash
apt update && apt install -y autossh docker.io docker-compose-plugin git
ssh-keygen -t ed25519 -f /root/.ssh/et_tunnel -N ''
cat /root/.ssh/et_tunnel.pub          # это в authorized_keys на проде
```

```ini
# /etc/systemd/system/et-db-tunnel.service
[Unit]
Description=SSH-туннель до MySQL прод-сервера
After=network-online.target
Wants=network-online.target

[Service]
Environment=AUTOSSH_GATETIME=0
ExecStart=/usr/bin/autossh -M 0 -N \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \
  -i /root/.ssh/et_tunnel \
  -L 127.0.0.1:3307:127.0.0.1:3306 \
  tunnel@<IP-ПРОДА>
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now et-db-tunnel
systemctl status et-db-tunnel --no-pager
# проверка: порт слушается и за ним живой MySQL
ss -ltnp | grep 3307
mysql -h 127.0.0.1 -P 3307 -u megotim4y2 -p megotim4y2 -e "SELECT COUNT(*) FROM users;"
```

`ServerAliveInterval` и `Restart=always` закрывают главный риск: если
туннель тихо умрёт, служба поднимет его за пять секунд, а SQLAlchemy у
бота настроен с `pool_pre_ping` и переоткроет соединения сам.

## Файл окружения на новом хосте

Копируем **не весь** `.env`, а только то, что боту нужно: если зарубежный
хост когда-нибудь скомпрометируют, ключи ЮKassa, JWT и админ-токен не
должны там лежать.

```
BOT_TOKEN=...
BOT_USERNAME=kmo_ai_english_bot
ADMIN_IDS=...
MINIAPP_URL=https://englishbot.krichigindocs.ru

BACKEND_URL=https://api-english.krichigindocs.ru
BACKEND_BOT_SECRET=<тот же, что на проде>

DATABASE_URL=mysql+asyncmy://megotim4y2:<пароль>@127.0.0.1:3307/megotim4y2?charset=utf8mb4

FREE_TRIAL_DAYS=1
FREE_DAILY_SECONDS=300
SUBSCRIPTION_PRICE_TRIAL7_RUB=199
SUBSCRIPTION_PRICE_MONTHLY_RUB=999
SUBSCRIPTION_PRICE_YEARLY_RUB=5999
SUBSCRIPTION_PRICE_TWOYEAR_RUB=9999

YOOKASSA_PROVIDER_TOKEN=<токен провайдера Telegram>
YOOKASSA_FISCALIZATION=1
YOOKASSA_VAT_CODE=1

DISCOUNT_PROMO_CODE=SALE20
DISCOUNT_PROMO_PERCENT=20
TRIAL7_UPSELL_PROMO=HABIT30
```

Секретный ключ магазина ЮKassa (`YOOKASSA_SECRET_KEY`), `AUTH_JWT_SECRET`,
`ADMIN_TOKEN`, ключи VAPID — **не копировать**, боту они не нужны.

## Переключение

Порядок важен: два long polling'а на один токен несовместимы. Telegram
отдаёт второму `409 Conflict`, и бот молча перестаёт отвечать. Поэтому
сначала гасим старый, потом поднимаем новый.

**1. Подготовить новый хост целиком, но не запускать бота**

```bash
git clone https://github.com/nutakoe27-commits/englishbot.git /opt/englishbot
cd /opt/englishbot
# положить .env из предыдущего раздела
docker compose -f docker-compose.bot.yml build
```

**2. Погасить бота на проде**

```bash
cd /var/www/englishbot
docker compose stop bot
docker compose rm -f bot
docker compose ps                 # bot исчез, остальное работает
```

Случайно поднять его обратно нельзя: в `docker-compose.yml` у сервиса
стоит `profiles: ["bot"]`, и обычный `docker compose up -d` его не трогает.

**3. Поднять бота за границей**

```bash
cd /opt/englishbot
docker compose -f docker-compose.bot.yml up -d
docker compose -f docker-compose.bot.yml logs -f
```

В логе ждём `Starting bot in long polling mode...` и `Starting reminders
loop (DB ready)`, без трейсбеков и без `409`.

**4. Проверить руками**

Написать боту `/start`, `/profile`, `/invite`. Профиль читается из базы —
если он отвечает корректно, значит туннель работает. Открыть мини-апп из
бота — проверяется `MINIAPP_URL`. Оформить тестовый инвойс — проверяется
связка с Telegram Payments.

## Откат

```bash
# на зарубежном хосте
docker compose -f docker-compose.bot.yml down
# на проде
docker compose --profile bot up -d bot
```

Данные при этом никуда не деваются: база одна и та же, состояние бота
целиком в ней.

## Что стало иначе

**Ответы бота чуть медленнее.** Каждый обработчик делает несколько запросов
в базу, теперь через туннель с задержкой около пятидесяти миллисекунд.
Прибавка — сотни две миллисекунд на сообщение, на глаз незаметно.

**Появилась новая точка отказа** — туннель. Если он ляжет и не поднимется,
бот не сможет читать базу: команды вроде `/profile` будут отвечать ошибкой,
а напоминания не разошлются. Стоит завести на это отдельный мониторинг:
`systemctl is-active et-db-tunnel` и проверку `SELECT 1` через порт 3307.

**Файрвол на новом хосте.** Внутрь нужен только SSH. Внутренний HTTP-сервер
бота при `network_mode: host` слушает 8080 на всех интерфейсах, и хотя
активных endpoint'ов там нет, закрыть порт снаружи всё равно правильно:

```bash
ufw default deny incoming && ufw allow OpenSSH && ufw enable
```
