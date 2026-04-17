# Автозапуск 1Cat-vLLM через systemd

Чтобы после перезагрузки V100 (или случайного `Ctrl+C`) сервер автоматически
поднимался — оформим запуск как systemd-сервис. Без этого Cloudflare Tunnel
будет «отлично работать», но отдавать 502 пока vLLM не запустят вручную.

## 1. Создать start-скрипт (если ещё не создан)

```bash
cat > ~/1Cat-vLLM/start_vllm.sh <<'EOF'
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
source venv/bin/activate

exec python -m vllm.entrypoints.openai.api_server \
    --model QuantTrio/Qwen3.5-35B-A3B-AWQ \
    --quantization awq \
    --dtype float16 \
    --gpu-memory-utilization 0.90 \
    --max-model-len 262144 \
    --tensor-parallel-size 4 \
    --max-num-seqs 8 \
    --max-num-batched-tokens 65536 \
    --attention-backend TRITON_ATTN \
    --skip-mm-profiling \
    --limit-mm-per-prompt '{"image":0,"video":0}' \
    --compilation-config '{"cudagraph_mode":"full_and_piecewise","cudagraph_capture_sizes":[1,2,4,8,16,32]}' \
    --disable-custom-all-reduce \
    --reasoning-parser qwen3 \
    --host 0.0.0.0 \
    --port 23333
EOF
chmod +x ~/1Cat-vLLM/start_vllm.sh
```

## 2. Создать systemd-юнит

```bash
sudo tee /etc/systemd/system/vllm.service >/dev/null <<'EOF'
[Unit]
Description=1Cat-vLLM OpenAI-compatible server (Qwen3.5-35B-A3B-AWQ)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=user
Group=user
WorkingDirectory=/home/user/1Cat-vLLM
ExecStart=/home/user/1Cat-vLLM/start_vllm.sh
Restart=on-failure
RestartSec=10s
# Модель грузится несколько минут — даём запас
TimeoutStartSec=600s
StandardOutput=append:/var/log/vllm.log
StandardError=append:/var/log/vllm.log

# CUDA нужна переменная, иначе процесс не увидит GPU
Environment=CUDA_VISIBLE_DEVICES=0,1,2,3
# HuggingFace cache — если модель не локально
Environment=HF_HOME=/home/user/.cache/huggingface

[Install]
WantedBy=multi-user.target
EOF

sudo touch /var/log/vllm.log
sudo chown user:user /var/log/vllm.log
```

Проверьте, что user/group в юните совпадают с вашими (у нас из `ps -ef` —
`user`). Если не совпадают — отредактируйте строки `User=` и `Group=`.

## 3. Включить и запустить

```bash
# Сначала убедитесь, что ручной процесс не запущен (pgrep покажет PID):
pgrep -f "vllm.entrypoints.openai" && echo "Процесс ещё жив — остановите"

sudo systemctl daemon-reload
sudo systemctl enable vllm
sudo systemctl start vllm

# Следить за прогревом модели (2-4 минуты):
sudo journalctl -u vllm -f
# или:
tail -f /var/log/vllm.log
```

Как только в логе появится `Uvicorn running on http://0.0.0.0:23333` —
сервер готов.

## 4. Проверка

```bash
curl http://localhost:23333/v1/models
```

## Управление

```bash
sudo systemctl status vllm       # статус
sudo systemctl restart vllm      # рестарт (модель грузится заново)
sudo systemctl stop vllm         # остановка
sudo journalctl -u vllm -n 200   # последние 200 строк лога
```
