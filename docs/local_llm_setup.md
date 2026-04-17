# Подключение локального vLLM (V100) вместо YandexGPT

Этот гайд описывает Этап 1 миграции: LLM уезжает на ваш домашний V100,
STT и TTS остаются в Yandex. Переключение — через переменную окружения
`LLM_PROVIDER` на VPS.

## Архитектура

```
Browser → VPS (FastAPI) → Yandex STT (audio→text)
                       → [LLM_PROVIDER]:
                            yandex → YandexGPT (как было)
                            vllm   → https://*.cfargotunnel.com/v1 → V100 дома
                       → Yandex TTS (text→audio) → Browser
```

---

## Шаг 1. На V100 — подтвердить, что vLLM отвечает по OpenAI API

1Cat-vLLM поднимает OpenAI-совместимый сервер, обычно на `:8000`.
На самом сервере V100 выполните:

```bash
# Список моделей
curl http://localhost:8000/v1/models

# Тест chat completions
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.5-35B-A3B-AWQ",
    "messages": [{"role": "user", "content": "Say hi"}],
    "max_tokens": 20
  }'
```

Из `/v1/models` возьмите точное значение поля `id` — это будет
`VLLM_MODEL_NAME`. Если в 1Cat-vLLM стоит нестандартный порт — запомните его.

---

## Шаг 2. На V100 — установить cloudflared и создать туннель

### 2.1. Установка

```bash
# Ubuntu (универсально для 20.04/22.04/24.04)
wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb
cloudflared --version
```

### 2.2. Регистрация (один раз)

```bash
cloudflared tunnel login
```

Команда откроет ссылку — откройте её в браузере, войдите в Cloudflare
(зарегистрируйтесь, если аккаунта нет — бесплатно, без карты).
Подтвердите доступ любому домену (даже если у вас нет домена — просто нажмите
кнопку, это не привязывает доменную зону).

### 2.3. Создать туннель

```bash
cloudflared tunnel create englishbot-llm
```

Команда выведет `Tunnel ID` вида `abc12345-6789-...` и путь к файлу credentials
(`~/.cloudflared/<ID>.json`). Запомните оба.

### 2.4. Создать конфиг туннеля

Создайте `~/.cloudflared/config.yml`:

```yaml
tunnel: englishbot-llm
credentials-file: /root/.cloudflared/<ID>.json   # замените <ID>

ingress:
  - hostname: <ID>.cfargotunnel.com              # замените <ID>
    service: http://localhost:8000               # порт 1Cat-vLLM
  - service: http_status:404
```

Примечание: `<ID>.cfargotunnel.com` — это служебный адрес Cloudflare для
туннеля без привязки к домену. Подставьте сюда свой `Tunnel ID` из шага 2.3.

### 2.5. Запустить как systemd-сервис

```bash
sudo cloudflared service install
sudo systemctl start cloudflared
sudo systemctl enable cloudflared
sudo systemctl status cloudflared
```

### 2.6. Проверка снаружи

С любого компьютера (не с V100):

```bash
curl https://<TUNNEL_ID>.cfargotunnel.com/v1/models
```

Должен вернуться тот же JSON, что в шаге 1.

---

## Шаг 3. На VPS — переключить backend на vLLM

### 3.1. Обновить `.env` (файл `/var/www/englishbot/backend/.env`)

Добавьте три новые строки, остальное не трогайте:

```bash
LLM_PROVIDER=vllm
VLLM_BASE_URL=https://<TUNNEL_ID>.cfargotunnel.com/v1
VLLM_MODEL_NAME=Qwen3.5-35B-A3B-AWQ
# VLLM_API_KEY=not-needed   # раскомментируйте и поставьте токен, если в 1Cat включена авторизация
```

Важно: в `VLLM_BASE_URL` обязательно добавить `/v1` в конце.
`VLLM_MODEL_NAME` — точно такое же значение, как в `/v1/models` (см. шаг 1).

### 3.2. Задеплоить свежий код

```bash
cd /var/www/englishbot
git pull
cd backend
docker compose build backend
docker compose up -d backend
docker compose logs -f backend | head -40
```

В логах при первом подключении клиента увидите:
```
[LLM] провайдер=vllm base_url=https://...cfargotunnel.com/v1 model=Qwen3.5-35B-A3B-AWQ
```

Если вдруг увидите `провайдер=yandex` — значит env-переменные не подхватились,
проверьте `.env` и перезапустите контейнер.

### 3.3. Живой тест

Откройте Mini App `@kmo_ai_english_bot`, нажмите кнопку записи, скажите фразу,
отпустите кнопку. Ответ должен прийти от Qwen, а не от YandexGPT. По стилю
заметите разницу: Qwen обычно более многословный и креативный чем
yandexgpt-lite.

---

## Откат

В `.env` вернуть `LLM_PROVIDER=yandex`, `docker compose up -d backend` —
и всё работает как раньше. Можно держать `VLLM_*` переменные заполненными:
они игнорируются при `LLM_PROVIDER=yandex`.

---

## Частые проблемы

1. **`cloudflared: connection refused`** — 1Cat-vLLM слушает не на
   `localhost:8000`. Проверьте `ss -tlnp | grep 8000` на V100 и поправьте
   `service:` в `config.yml`.

2. **`400 Bad Request` от vLLM** — неверное `VLLM_MODEL_NAME`. Должно точно
   совпадать с `id` из `/v1/models`.

3. **Долгий отклик (>5 сек)** — проверьте, что V100 не крутит одновременно
   другую задачу. `nvidia-smi` покажет утилизацию. Первый запрос после
   простоя может быть медленным (прогрев модели).

4. **Туннель отваливается после перезагрузки V100** — `sudo systemctl enable
   cloudflared` не выполнили (см. шаг 2.5).
