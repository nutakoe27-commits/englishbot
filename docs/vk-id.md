# Вход через VK ID: ВКонтакте, Одноклассники, Mail.ru

## Как устроено

Все три сервиса работают через **одно приложение VK ID** (id.vk.ru).
Одноклассники и Mail.ru — это способы входа внутри VK ID, а не отдельные
провайдеры: VK ID возвращает пользователя VK ID в любом случае. Поэтому у
нас одна личность `provider='vk'`, `provider_uid` = VK ID `user_id`, а через
какой сервис пришли — в `user_identities.via` (`vk` | `ok` | `mail`,
миграция 0040). Первый `via` не перезаписывается: это способ регистрации.

Протокол — OAuth 2.1 с PKCE, секрет приложения для входа не нужен:

1. `POST /api/auth/vk/start {mode: login|link, via: vk|ok|mail}` → `{url}`.
   В URL: `id.vk.ru/authorize`, `code_challenge` (S256), `state` (32+
   символа, одноразовый токен из `auth_actions`), `scope`, для OK и Mail —
   `provider=ok_ru|mail_ru`.
2. VK ID возвращает на `GET /api/auth/vk/callback?code&state&device_id`.
3. Бэкенд меняет код на токен (`POST id.vk.ru/oauth2/auth`), берёт профиль
   (`POST id.vk.ru/oauth2/user_info`), входит/создаёт аккаунт или
   привязывает к текущему (`link_or_merge`, как у Яндекса).
4. Редирект на сайт с `#oauth_jwt=…&oauth_provider=vk&via=…&mode=…` или
   `#oauth_error=…`.

`code_verifier` не хранится: он выводится из `state` и `AUTH_JWT_SECRET`
(HMAC-SHA256), колбэк пересчитывает его тем же способом.

## Интерфейс

- Экран входа: кнопка «Войти через Яндекс ID», под ней «или через» и три
  круглые иконки (ВКонтакте, Одноклассники, Mail.ru), ниже кнопка «Войти
  по email и паролю», которая раскрывает прежнюю форму входа/регистрации.
- Профиль: в «Способах входа» строка VK ID с названием сервиса, через
  который вошли; если не привязан — ряд из трёх иконок для привязки.
- Админка: у пользователей значки 🔵 ВК, 🟠 OK, 📧 Mail.ru; на дашборде
  карточка «Способы входа» — регистрации за 7/30/90 дней по первому способу
  и сколько всего привязок каждого.

## Настройка

1. id.vk.ru → кабинет VK ID для бизнеса → создать приложение «Веб».
   - Доверенный redirect URL: `<API_PUBLIC_URL>/api/auth/vk/callback`.
   - Базовый домен: домен сайта.
   - Способы входа: включить **Одноклассники** и **Mail**.
   - Доступы: имя, фамилия, **почта**.
2. В `.env` бэкенда:
   ```ini
   VK_ID_CLIENT_ID=<ID приложения>
   # необязательно:
   # VK_ID_REDIRECT_URI=https://api-…/api/auth/vk/callback
   # VK_ID_BASE_URL=https://id.vk.ru
   # VK_ID_SCOPE=vkid.personal_info email
   ```
3. Миграция `db/migrations/0040_vk_id.sql`, пересборка backend, miniapp,
   admin.

Без `VK_ID_CLIENT_ID` старт входа отвечает 503, иконки на экране входа
остаются, но вход через них не запустится — включать вместе с настройкой.

## Проверка

```bash
docker compose logs backend --since 30m | grep -i "auth\\] vk"
```

```sql
SELECT via, COUNT(*) FROM user_identities WHERE provider = 'vk' GROUP BY via;
```
