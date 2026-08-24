# Local LLM Setup — V100 + SSH Reverse Tunnel

End-to-end guide for running EnglishBot on a local LLM: два Tesla
V100-SXM2-32GB на домашнем сервере, подключённые к RF VPS через SSH
reverse tunnel (портов на роутере V100 открывать не нужно).

Актуальная модель на 2026-08-24 — `mattbucci/Qwen3.8-27B-AWQ`, отдаётся
под старым именем `QuantTrio/Qwen3.5-35B-A3B-AWQ` (см. «Смена модели»).

---

## Topology

```
┌──────────────────────┐           ┌────────────────────────────┐
│ V100 server (2×32GB) │           │ VPS 89.111.143.45 (RF)     │
│                      │           │                            │
│  1Cat-vLLM           │           │  englishbot backend        │
│  OpenAI API :23333   │◀──────────│  docker, host net          │
│                      │   reverse │  uses http://localhost:    │
│  autossh -R 23333    │    SSH    │        23333/v1            │
│  ─────────────────▶  │  tunnel   │                            │
│                      │           │  sshd :22 (user tunnel)    │
└──────────────────────┘           └────────────────────────────┘
```

The V100 opens an outbound SSH connection to the VPS. That SSH
connection carries a reverse port forward: `VPS:23333 → V100:23333`.
Backend on the VPS sees the vLLM server as if it were on localhost.

**Why SSH tunnel instead of Cloudflare / Caddy:**
- No inbound ports need to be opened on the V100 router (only outbound SSH).
- No DNS records, no TLS certificates to manage.
- Reuses existing sshd on the VPS.
- autossh handles reconnects automatically.

---

## Part 1 — VPS: create dedicated `tunnel` user

Create a restricted user that can only hold the reverse tunnel — no
shell, no other ports, no X11/agent forwarding.

```bash
# On VPS as root / sudo user
sudo useradd -M -s /bin/bash tunnel
sudo passwd -l tunnel   # block password login, key-only
id tunnel               # confirm: uid=..., gid=...
```

### Install the V100 public key with restrictions

```bash
sudo mkdir -p /home/tunnel/.ssh
sudo chmod 700 /home/tunnel/.ssh

# Paste the actual public key from V100 (~/.ssh/vps_tunnel.pub)
echo 'command="/bin/false",no-agent-forwarding,no-X11-forwarding,no-pty,permitopen="localhost:23333" ssh-ed25519 AAAA...KEY... v100-to-vps-tunnel' \
  | sudo tee /home/tunnel/.ssh/authorized_keys

sudo chmod 600 /home/tunnel/.ssh/authorized_keys
sudo chown -R tunnel:tunnel /home/tunnel/.ssh
```

The `authorized_keys` options mean:
- `command="/bin/false"` — no shell, only the tunnel
- `no-pty` — no terminal
- `permitopen="localhost:23333"` — can only forward this single port

### sshd_config tweaks

Add to `/etc/ssh/sshd_config`:

```
# Tunnel settings for V100
AllowTcpForwarding yes
ClientAliveInterval 30
ClientAliveCountMax 3
```

Reload sshd:

```bash
sudo systemctl reload ssh
sudo systemctl status ssh --no-pager | head -5
```

---

## Part 2 — V100: generate key & start tunnel

### Generate key pair

```bash
# On V100 as the user that runs vLLM (e.g. "user")
ssh-keygen -t ed25519 -f ~/.ssh/vps_tunnel -N "" -C "v100-to-vps-tunnel"
cat ~/.ssh/vps_tunnel.pub   # copy this line to the VPS step above
```

### Manual test (interactive)

```bash
ssh -i ~/.ssh/vps_tunnel \
    -o StrictHostKeyChecking=accept-new \
    -N -T \
    -R 23333:localhost:23333 \
    tunnel@89.111.143.45
```

The command hangs — that is correct, the tunnel is alive. In a
separate VPS shell:

```bash
curl -s http://localhost:23333/v1/models | python3 -m json.tool
```

Expected: JSON с `id: QuantTrio/Qwen3.5-35B-A3B-AWQ`, `root: mattbucci/Qwen3.8-27B-AWQ` и `max_model_len: 32768`.

Close the manual tunnel with Ctrl+C before continuing.

### Permanent tunnel via systemd + autossh

```bash
sudo apt update && sudo apt install -y autossh

sudo tee /etc/systemd/system/vllm-tunnel.service > /dev/null <<'EOF'
[Unit]
Description=Reverse SSH tunnel V100 -> VPS (vLLM :23333)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=user
Environment="AUTOSSH_GATETIME=0"
Environment="AUTOSSH_PORT=0"
ExecStart=/usr/bin/autossh -M 0 -N -T \
  -o "ServerAliveInterval=30" \
  -o "ServerAliveCountMax=3" \
  -o "ExitOnForwardFailure=yes" \
  -o "StrictHostKeyChecking=accept-new" \
  -o "TCPKeepAlive=yes" \
  -i /home/user/.ssh/vps_tunnel \
  -R 23333:localhost:23333 \
  tunnel@89.111.143.45
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now vllm-tunnel.service
sudo systemctl status vllm-tunnel.service --no-pager
```

Key flags:
- `AUTOSSH_GATETIME=0` — connect immediately on start
- `AUTOSSH_PORT=0` — use built-in `ServerAliveInterval` heartbeat
- `ExitOnForwardFailure=yes` — die loudly if VPS port is busy (systemd restarts)

Logs:
```bash
sudo journalctl -u vllm-tunnel.service -f
```

---

## Part 3 — vLLM on V100 (факт, а не пожелание)

Железо: **2× Tesla V100-SXM2-32GB** (sm70, Volta), драйвер 580.173.02.
На тех же картах живут Kokoro (~4.0 ГБ) и Whisper (~2.4 ГБ) — обе на
GPU0. Поэтому именно GPU0 является узким местом по памяти, а не GPU1.

Запуск лежит в `~/1Cat-vLLM/start_vllm.sh` (3.6, откат) и
`~/1Cat-vLLM/start_vllm38.sh` (3.8, текущая); какой из них поднимается
автоматически — задаёт `ExecStart` в `vllm.service`, см.
[`v100_vllm_systemd.md`](v100_vllm_systemd.md). Актуальная команда:

```bash
python -m vllm.entrypoints.openai.api_server \
  --model mattbucci/Qwen3.8-27B-AWQ \
  --tokenizer QuantTrio/Qwen3.6-27B-AWQ \
  --chat-template ~/.cache/huggingface/hub/models--mattbucci--Qwen3.8-27B-AWQ/snapshots/<hash>/chat_template.jinja \
  --served-model-name QuantTrio/Qwen3.5-35B-A3B-AWQ \
  --quantization awq --dtype float16 \
  --gpu-memory-utilization 0.82 \
  --max-model-len 32768 \
  --tensor-parallel-size 2 \
  --max-num-seqs 4 --max-num-batched-tokens 8192 \
  --attention-backend FLASH_ATTN_V100 \
  --skip-mm-profiling \
  --limit-mm-per-prompt '{"image":0,"video":0}' \
  --compilation-config '{"cudagraph_mode":"piecewise","cudagraph_capture_sizes":[1,2,4]}' \
  --disable-custom-all-reduce \
  --host 0.0.0.0 --port 23333
```

Почему именно так:

- **`--dtype float16`** — V100 не умеет bfloat16 аппаратно. vLLM сам
  кастует и пишет `Casting torch.bfloat16 to torch.float16`.
- **`--attention-backend FLASH_ATTN_V100`** — бэкенд из форка 1Cat-vLLM,
  специально под sm70. Стоковые FA2-ядра на Volta не работают.
- **`--quantization awq`** — только классический AWQ (`version: gemm`).
  `compressed-tensors` и `quark` уходят в Marlin-ядра, а Marlin требует
  sm80+. Это отсекает большинство современных сборок с HF.
- **`--tensor-parallel-size 2`** — карт две. В старой версии этого
  документа стояло 4, и это было неправдой.
- **`--served-model-name`** — держит старое имя, чтобы `.env` на VPS не
  трогать при смене модели. См. следующий раздел.
- **`--limit-mm-per-prompt` в нули** — модель мультимодальная, но нам
  нужен только текст. В логе появляется `running in text-only mode`.
  Визуальная башня при этом всё равно **строится** при инициализации,
  поэтому её квантование должно быть корректно описано в конфиге.

Форк: `1Cat-vLLM`, версия `0.0.3.dev9+gfeb8402e9` (в баннере рисует
`1.0.0`), venv в `~/1Cat-vLLM/venv`. Это **не апстрим** — `pip install -U
vllm` сломает V100-патчи. Поддержка новых архитектур зависит от форка:

```bash
python -c "from vllm.model_executor.models.registry import ModelRegistry; \
print([a for a in sorted(ModelRegistry.get_supported_archs()) if 'wen' in a])"
```

---

## Смена модели

Меняется **только на V100**. На VPS не трогается ничего: благодаря
`--served-model-name` бэкенд продолжает видеть прежнее имя.

### Порядок

1. **Проверить пригодность до скачивания.** У кандидата в `config.json`
   должно быть `architectures`, которое есть в реестре форка, и
   `quantization_config.quant_method == "awq"` с `version: gemm`.
   Всё остальное (compressed-tensors, quark, NVFP4, FP8, GGUF, MLX) на
   V100 не заведётся.
2. **Скачать заранее**, не в даунтайме: `hf download <repo>` (~25 мин на
   19 ГБ).
3. **Снять эталон на текущей модели** — `~/bench_llm.py`. После
   переключения сравнивать будет не с чем.
4. **Новый скрипт запуска отдельным файлом.** Старый не трогать —
   он и есть откат.
5. Включить режим техработ в админке, переключиться, прогнать
   `~/accept38.py` и `~/quality38.py`, снять техработы.

### Пороги приёмки

| Метрика | Порог | 3.6 | 3.8 |
|---|---|---|---|
| TTFT (короткий ответ) | ≤ 0.40 с | 0.221 | 0.215 |
| tok/s (длинная генерация) | ≥ 20 | 28.5 | 31.0 |
| утечки reasoning | 0 | 0 | 0 |
| валидный JSON (грамматика) | 5/5 | — | 5/5 |
| свободно на GPU0 | ≥ 1.5 ГБ | 5.7 ГБ | 7.4 ГБ |

### Грабли, на которые мы уже наступили

**Токенизатор.** Сборки, сохранённые новым `transformers`, пишут
`tokenizer_class: "TokenizersBackend"`, которого нет в `transformers
4.57.6` из venv. Симптом: `ValueError: Tokenizer class TokenizersBackend
does not exist`. Лечится флагом `--tokenizer` с указанием на репозиторий
рабочей модели — если `vocab.json` и `merges.txt` у них побайтово
совпадают (проверять md5) и совпадают id спецтокенов `<|im_start|>` /
`<|im_end|>`. У 3.6 и 3.8 они совпадают.

**Пустой `modules_to_not_convert`.** Симптом: `ValueError: The input size
is not aligned with the quantized weight shape` внутри
`Qwen3_VisionTransformer`. Причина: в конфиге сборки список исключений
пуст, хотя визуальная башня в файле лежит **неквантованной**, и vLLM
пытается применить к ней AWQ. Лечится приведением метаданных к правде —
скрипт ниже.

> **Важно:** патч правит `config.json` **внутри HF-кэша**. Любой
> повторный `hf download` или чистка кэша его сотрёт, и vLLM перестанет
> стартовать с той же ошибкой. Скрипт идемпотентный — прогоняйте после
> любых манипуляций с кэшем.

```bash
cat > ~/fix_model_config.py <<'EOF'
#!/usr/bin/env python3
"""Приводит modules_to_not_convert в соответствие с содержимым весов.
Идемпотентно. Запускать после любого hf download этой модели."""
import json, os, shutil, sys
from huggingface_hub import snapshot_download
from safetensors import safe_open

REPO = sys.argv[1] if len(sys.argv) > 1 else "mattbucci/Qwen3.8-27B-AWQ"
p = snapshot_download(REPO, local_files_only=True)

idx = os.path.join(p, "model.safetensors.index.json")
if os.path.exists(idx):
    ks = list(json.load(open(idx))["weight_map"].keys())
else:
    ks = []
    for f in sorted(os.listdir(p)):
        if f.endswith(".safetensors"):
            with safe_open(os.path.join(p, f), framework="pt") as fh:
                ks += list(fh.keys())

quant = {k[:-8] for k in ks if k.endswith(".qweight")}
plain = {k[:-7] for k in ks if k.endswith(".weight")} - quant
CAND = ["visual", "linear_attn.in_proj_a", "linear_attn.in_proj_b",
        "self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj",
        "layers.0.", "mtp", "lm_head", "embed_tokens"]
need = [c for c in CAND
        if any(c in x for x in plain) and not any(c in x for x in quant)]

cfg = os.path.join(p, "config.json")
if not os.path.exists(cfg + ".orig"):
    shutil.copy(cfg, cfg + ".orig")
c = json.load(open(cfg))
cur = c.get("quantization_config", {}).get("modules_to_not_convert")
if cur == need:
    print("уже верно:", need)
else:
    c["quantization_config"]["modules_to_not_convert"] = need
    json.dump(c, open(cfg, "w"), ensure_ascii=False, indent=2)
    print(f"было {cur} → стало {need}")
EOF
chmod +x ~/fix_model_config.py
```

### Откат

Один запуск старого скрипта, копировать ничего не надо:

```bash
sudo sed -i 's|start_vllm38.sh|start_vllm.sh|' /etc/systemd/system/vllm.service
sudo systemctl daemon-reload && sudo systemctl restart vllm
sudo journalctl -u vllm -f
```

Старая модель остаётся в HF-кэше — не удаляйте её, пока новая не
отработает хотя бы неделю.

---

## Подавление reasoning

Вся линейка Qwen3.x — reasoning-модели. Без подавления они пишут
chain-of-thought прямо в `content` (`Thinking Process:...` /
`<think>...</think>`), а для голосового бота это катастрофа: TTS
озвучит размышления вслух.

Квантованные сборки **игнорируют мягкий переключатель `/no_think`**.
Работает строгий — через payload запроса:

```json
{
  "chat_template_kwargs": {"enable_thinking": false}
}
```

`VLLMProvider` в `backend/app/llm_providers.py` шлёт это на каждом
запросе; то же делают `grammar.py` и `listening.py`. Вторым эшелоном
идёт `/no_think` в user-реплике и вырезание `<think>...</think>`
регулярками из ответа.

**Обязательно проверять при смене модели:** в `chat_template.jinja`
новой сборки должно присутствовать `enable_thinking`, иначе строгий
переключатель молча ничего не сделает. Приёмочный тест `~/accept38.py`
считает утечки — порог нулевой. На 3.8 проверено: 0 из 15.

---

## Part 4 — VPS: switch backend to vLLM

Edit `/var/www/englishbot/.env` and append:

```bash
# Local LLM via SSH reverse tunnel from V100
LLM_PROVIDER=vllm
# host.docker.internal resolves to the VPS host gateway from inside the
# backend container (see extra_hosts in docker-compose.yml). The SSH
# tunnel exposes vLLM on the VPS host at port 23333.
VLLM_BASE_URL=http://host.docker.internal:23333/v1
# Это НЕ имя файла модели, а --served-model-name: постоянный алиас,
# чтобы менять модель на V100, не трогая VPS. Реальную модель показывает
# поле "root" в ответе /v1/models.
VLLM_MODEL_NAME=QuantTrio/Qwen3.5-35B-A3B-AWQ
```

Note: plain `http://` is fine — the tunnel wraps everything in SSH.

Sanity check before restart:
```bash
curl -s http://localhost:23333/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "QuantTrio/Qwen3.5-35B-A3B-AWQ",
    "messages": [
      {"role": "system", "content": "You are a friendly English tutor. Reply briefly."},
      {"role": "user", "content": "Yesterday I goed to the shop and buyed some apples."}
    ],
    "max_tokens": 200,
    "temperature": 0.6,
    "chat_template_kwargs": {"enable_thinking": false}
  }' | python3 -m json.tool
```

Expected: clean tutor correction, `finish_reason: stop`, no
`Thinking Process` prefix in content.

Restart backend:
```bash
cd /var/www/englishbot
docker compose up -d backend
docker compose logs --tail 50 backend
```

Live test via Telegram Mini App: open `@kmo_ai_english_bot`, record a
sentence with grammar mistakes, verify the reply is a friendly
correction.

---

## Troubleshooting

**From inside backend container `curl http://host.docker.internal:23333/v1/models` fails**
- Docker Compose file is missing `extra_hosts: ["host.docker.internal:host-gateway"]` under the `backend` service.
- Re-create the container: `docker compose up -d backend` (not just restart — needs recreation to apply extra_hosts).

**`curl http://localhost:23333/v1/models` on VPS host returns connection refused**
- Tunnel is down. Check `sudo systemctl status vllm-tunnel.service` on V100.
- Check `sudo journalctl -u vllm-tunnel.service -n 50 --no-pager` for errors.
- Confirm VPS can be reached from V100: `ssh -i ~/.ssh/vps_tunnel tunnel@89.111.143.45` (should disconnect immediately with `/bin/false`, meaning auth OK).

**`Permission denied (publickey)` from V100**
- Public key not in `/home/tunnel/.ssh/authorized_keys` on VPS, or wrong permissions.
- On VPS: `sudo ls -la /home/tunnel/.ssh/` — dir 700, file 600, owned by `tunnel:tunnel`.

**Tunnel stays up but requests time out**
- vLLM itself is down on V100. `ps -ef | grep vllm` on V100.
- Check vLLM log for OOM / CUDA errors.

**Model returns "Thinking Process" in content**
- Request is missing `chat_template_kwargs.enable_thinking=false`.
- Confirm `VLLMProvider` is deployed and `VLLM_BASE_URL`/`VLLM_MODEL_NAME` are set in `.env`.
