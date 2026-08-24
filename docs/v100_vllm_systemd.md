# Автозапуск vLLM на V100 через systemd

**Состояние на 2026-08-24: юнит `vllm.service` заведён и включён.**
После перезагрузки сервера vLLM поднимается сам, наравне с
`kokoro-tts`, `whisper-stt` и `vllm-tunnel`.

До этого vLLM жил в tmux и после `reboot` не возвращался — бот отвечал
«не удалось связаться с сервером», пока кто-то не заходил руками.

---

## Ежедневное управление

```bash
sudo systemctl status vllm
sudo systemctl restart vllm       # модель грузится заново, ~2-3 мин
sudo systemctl stop vllm
sudo journalctl -u vllm -f        # следить за прогревом
sudo journalctl -u vllm -n 200    # последние 200 строк
tail -f /var/log/vllm.log

curl -s http://localhost:23333/v1/models | python3 -m json.tool
```

Готовность — строка `Application startup complete` в логе.
Поле `root` в ответе `/v1/models` показывает, какая модель реально
загружена; `id` — постоянный алиас для VPS.

---

## Смена модели и откат

`ExecStart` указывает на конкретный скрипт запуска. Два скрипта рядом —
это и есть механизм отката:

```bash
# ~/1Cat-vLLM/start_vllm.sh    — Qwen3.6 (откат)
# ~/1Cat-vLLM/start_vllm38.sh  — Qwen3.8 (текущая)

sudo sed -i 's|start_vllm38.sh|start_vllm.sh|' /etc/systemd/system/vllm.service
sudo systemctl daemon-reload && sudo systemctl restart vllm
sudo journalctl -u vllm -f
```

Обратно — та же команда с заменой в другую сторону. На VPS при этом
не меняется ничего: `--served-model-name` держит имя постоянным.

---

## Как юнит устроен (справочно)

Скрипты запуска лежат в `~/1Cat-vLLM/` (см.
[`local_llm_setup.md`](local_llm_setup.md) — там их актуальное
содержимое). Юнит просто оборачивает тот, что нужен.

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

Включение (уже сделано, оставлено для воспроизведения на новой машине):

```bash
# Никаких ручных процессов быть не должно — иначе подерутся за GPU
pgrep -f "[a]pi_server" && echo "ещё жив — остановите вручную"

sudo systemctl daemon-reload
sudo systemctl enable --now vllm
sudo journalctl -u vllm -f
```

## Ручной запуск — только для отладки

Если нужно поднять vLLM в обход systemd (посмотреть вывод вживую,
поиграть с флагами), сначала остановите сервис, иначе два процесса
подерутся за GPU:

```bash
sudo systemctl stop vllm
~/1Cat-vLLM/start_vllm38.sh 2>&1 | tee ~/vllm-debug.log
# закончили — Ctrl+C и обратно:
sudo systemctl start vllm
```

## Проверка после перезагрузки

Ради этого всё и делалось:

```bash
sudo reboot
# после загрузки:
systemctl is-active vllm kokoro-tts whisper-stt vllm-tunnel
curl -s http://localhost:23333/v1/models | head -3
```
