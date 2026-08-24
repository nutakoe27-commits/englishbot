# Автозапуск vLLM на V100 через systemd

**Состояние на 2026-08-24: юнита нет, vLLM запущен в tmux.** Это дыра:
после перезагрузки сервера `kokoro-tts`, `whisper-stt` и `vllm-tunnel`
поднимутся сами (у них юниты есть), а vLLM — нет. Бот будет отвечать
«не удалось связаться с сервером», пока кто-то не зайдёт руками.

Ниже — как это починить. Пока не сделано, **держите в голове**: любой
`reboot` V100 требует ручного запуска.

---

## Как запущено сейчас (факт, а не пожелание)

```bash
tmux ls
# vllm: 1 windows

ps -eo pid,ppid,args | grep [a]pi_server
# родитель: bash -c ~/1Cat-vLLM/start_vllm.sh 2>&1 | tee ~/vllm.log
```

Ручной рестарт:

```bash
tmux kill-session -t vllm 2>/dev/null; pkill -f "[a]pi_server"; sleep 8
tmux new-session -d -s vllm "~/1Cat-vLLM/start_vllm.sh 2>&1 | tee ~/vllm.log"
tail -f ~/vllm.log
```

Готовность — строка `Application startup complete` (обычно через 90-120 с).

---

## Перевод на systemd

Скрипты запуска уже лежат в `~/1Cat-vLLM/` (см.
[`local_llm_setup.md`](local_llm_setup.md) — там их актуальное содержимое).
Юнит просто оборачивает тот, что нужен.

```bash
sudo tee /etc/systemd/system/vllm.service >/dev/null <<'EOF'
[Unit]
Description=1Cat-vLLM OpenAI-compatible server (V100)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=user
Group=user
WorkingDirectory=/home/user/1Cat-vLLM
# Меняете модель — меняете ExecStart на нужный скрипт и делаете
# daemon-reload. Держать два скрипта проще, чем править один: откат
# сводится к смене одной строки.
ExecStart=/home/user/1Cat-vLLM/start_vllm38.sh
Restart=on-failure
RestartSec=15s
# Загрузка 19 ГБ на две карты + torch.compile — до 3 минут
TimeoutStartSec=600s
StandardOutput=append:/var/log/vllm.log
StandardError=append:/var/log/vllm.log

Environment=CUDA_VISIBLE_DEVICES=0,1
Environment=HF_HOME=/home/user/.cache/huggingface

[Install]
WantedBy=multi-user.target
EOF

sudo touch /var/log/vllm.log && sudo chown user:user /var/log/vllm.log
```

Перед включением обязательно погасить tmux-версию, иначе два процесса
подерутся за GPU:

```bash
tmux kill-session -t vllm 2>/dev/null; pkill -f "[a]pi_server"; sleep 8
pgrep -f "[a]pi_server" && echo "ещё жив — остановите вручную"

sudo systemctl daemon-reload
sudo systemctl enable --now vllm
sudo journalctl -u vllm -f
```

## Управление

```bash
sudo systemctl status vllm
sudo systemctl restart vllm       # модель грузится заново, ~2-3 мин
sudo systemctl stop vllm
sudo journalctl -u vllm -n 200
curl -s http://localhost:23333/v1/models | python3 -m json.tool
```

## Проверка после перезагрузки

Ради этого всё и делается:

```bash
sudo reboot
# после загрузки:
systemctl is-active vllm kokoro-tts whisper-stt vllm-tunnel
curl -s http://localhost:23333/v1/models | head -3
```
