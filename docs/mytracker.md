# VK MyTracker

Веб-счётчик MyTracker стоит рядом с Яндекс Метрикой и получает **те же
цели с теми же названиями**: обе системы вызываются из одного места,
`miniapp/src/metrika.ts` (`ymHit`, `ymReachGoal`). Новая цель, добавленная
через `ymReachGoal`, автоматически уходит в оба счётчика.

Счётчик покрывает все площадки, потому что все они — один и тот же веб-код:
Telegram Mini App, сайт, установленное приложение (PWA и TWA из RuStore).

## Включение

ID счётчика впечатывается в бандл на этапе сборки:

```ini
# .env прода
VITE_MYTRACKER_ID=1234567
```

```bash
docker compose up -d --build miniapp
```

Пустое значение — MyTracker выключен целиком: скрипт не грузится, события
не копятся. Поэтому на тесте ID не задаём, чтобы не мусорить в боевом проекте.

## Что отправляется

- **pageView** — при открытии и на виртуальных переходах: `/landing`,
  `/level`, `/schools`, `/subscribe`, `/subscribe/thanks`.
- **setUserID** — id аккаунта, один раз после входа. Один человек с
  телефона и ноутбука считается одним пользователем.
- **reachGoal** — цели ниже. В каждой есть параметр `platform`:
  `telegram`, `app` (установленное приложение) или `web`. Остальные
  параметры те же, что в Метрике.
- **Выручка**: у `subscription_paid` сумма `amount_rub` дополнительно
  передаётся как `value`.

## Цели

| Цель | Когда | Параметры |
|---|---|---|
| `landing_view` | открыт лендинг | |
| `landing_scroll_75` | лендинг пролистан на 75 % | |
| `landing_cta_click` | «Попробовать бесплатно» | `location` |
| `landing_buy_click` | «Оплатить сразу» | `location` |
| `level_landing_view` | открыт лендинг теста `/level` | |
| `level_landing_start` | начат анонимный тест | |
| `level_landing_done` | тест пройден | `cefr`, … |
| `level_landing_signup_click` | клик «Зарегистрироваться» после результата | `cefr` |
| `level_landing_claimed` | результат привязан к новому аккаунту | `cefr` |
| `schools_landing_view` | открыт лендинг для школ | |
| `school_cta_click` | CTA на лендинге школ | |
| `school_trial_started` | школа начала пробный период | |
| `school_checkout_started` | школа перешла к оплате | `seats`, `months`, `amount` |
| `school_invoice_opened` | открыта форма счёта | |
| `school_invoice_requested` | запрошен счёт | `seats`, `months` |
| `school_connected` | школа подключена | `seats`, `months` |
| `onboarding_started` | показан гид | |
| `onboarding_completed` | гид пройден | |
| `onboarding_skipped` | гид пропущен | |
| `onboarding_start_talking` | из гида сразу в разговор | |
| `level_test_started` | начат тест в приложении | |
| `level_test_completed` | тест в приложении пройден | `cefr`, … |
| `level_test_applied` | уровень из теста применён | `cefr` |
| `session_started` | начат разговор | `mode` |
| `session_completed` | разговор завершён | `mode`, `seconds` |
| `mic_permission_denied` | нет доступа к микрофону | |
| `push_ask_accept` | согласие на уведомления | |
| `push_ask_decline` | отказ от уведомлений | |
| `subscribe_opened` | открыт экран тарифов | |
| `subscribe_plan_clicked` | выбран тариф | `plan`, `amount_rub` |
| `subscription_paid` | оплата прошла | `plan`, `amount_rub`, `days`; `value` = сумма |

## Цели в интерфейсе MyTracker

События из кода приходят сами, создавать их заранее не нужно: они
появляются в отчётах по событиям после первой отправки. Чтобы
использовать событие как конверсию (например, для оптимизации рекламы
VK Ads), в настройках проекта MyTracker заводится цель с **тем же
названием**, что в таблице. Минимальный набор для рекламы:
`landing_cta_click`, `level_landing_claimed`, `session_started`,
`subscribe_plan_clicked`, `subscription_paid`.

## Android-приложение (кампании VK на установку)

Веб-счётчик не видит установку APK, поэтому для кампаний «Мобильное
приложение» в обёртку TWA встроен SDK MyTracker, а регистрация и оплата
приходят в MyTracker с сервера.

```
SDK в APK ──instanceId──▶ стартовый адрес ?mt_iid=…
                              │
                 сайт (mtInstall.ts) запоминает, после входа
                              ▼
          POST /api/analytics/mt-install ──▶ mytracker_installs
                              │
   бэкенд (mytracker.py) ──S2S──▶ registration / login / customRevenue
                                   + customEvent subscription_paid
```

- **Установки и запуски** считает SDK сам после `MyTracker.initTracker`.
- **registration** — аккаунт создан не раньше чем за 2 часа до первого
  входа с этой установки; иначе **login**. Каждая пара «аккаунт +
  установка» отправляется один раз.
- **Оплата** — `customRevenue` (рубли, id транзакции `yk-<id платежа>`) и
  событие `subscription_paid` с параметром `platform=app`. Только если
  аккаунт хоть раз заходил из приложения; ретрай вебхука не задваивает.
- Серверные события попадают в отчёты MyTracker с задержкой до 4 часов.

### Настройка

1. В проекте MyTracker добавить Android-приложение с пакетом
   `ru.krichigindocs.englishbot.twa` (ссылка на страницу в RuStore).
   Взять **SDK-ключ** приложения и **ID приложения** (числовой).
2. В настройках аккаунта MyTracker создать **S2S API-ключ**.
3. На проде в `.env`:
   ```ini
   MYTRACKER_APP_ID=123456
   MYTRACKER_S2S_TOKEN=...
   ```
   миграция `db/migrations/0038_mytracker_installs.sql`, пересборка backend
   и miniapp.
4. На Mac, в папке проекта Bubblewrap (где `twa-manifest.json`):
   ```bash
   python3 /path/to/englishbot/scripts/twa_mytracker_patch.py . <SDK_KEY>
   bubblewrap build
   ```
   При каждой новой версии: поднять `appVersionCode` → `bubblewrap update`
   → скрипт → `bubblewrap build`. `update` перегенерирует проект и стирает
   правки, поэтому скрипт запускается после него.
