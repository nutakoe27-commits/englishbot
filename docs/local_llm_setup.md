# Local LLM Setup — V100 + SSH Reverse Tunnel

End-to-end guide to switch EnglishBot from YandexGPT to a local
Qwen3.5-35B-A3B-AWQ running on a V100 home server, connected to the
RF VPS via an SSH reverse tunnel (no open ports on the V100 router).

---

## Topology

```
┌──────────────────────┐           ┌────────────────────────────┐
│ V100 server (home)   │           │ VPS 89.111.143.45 (RF)     │
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

Expected: JSON with `QuantTrio/Qwen3.5-35B-A3B-AWQ` and `max_model_len: 262144`.

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

## Part 3 — vLLM on V100 (reference)

The existing 1Cat-vLLM launch command that works with this model:

```bash
python -m vllm.entrypoints.openai.api_server \
  --model QuantTrio/Qwen3.5-35B-A3B-AWQ \
  --quantization awq --dtype float16 \
  --gpu-memory-utilization 0.90 \
  --max-model-len 262144 \
  --tensor-parallel-size 4 \
  --max-num-seqs 8 --max-num-batched-tokens 65536 \
  --attention-backend TRITON_ATTN \
  --skip-mm-profiling \
  --limit-mm-per-prompt '{"image":0,"video":0}' \
  --disable-custom-all-reduce \
  --host 0.0.0.0 --port 23333
```

For a systemd unit that auto-starts vLLM itself, see
[`v100_vllm_systemd.md`](v100_vllm_systemd.md).

### Reasoning suppression

Qwen3.5-35B-A3B is a reasoning model. Without suppression it emits
chain-of-thought into `content` (`Thinking Process:...` /
`<think>...</think>`), which is fatal for a voice bot (TTS would
vocalize the thinking).

The quantized QuantTrio build **ignores the `/no_think` soft switch**.
The working solution is the strict switch via request payload:

```json
{
  "chat_template_kwargs": {"enable_thinking": false}
}
```

The `VLLMProvider` in `backend/app/llm_providers.py` sends this on
every request. As defence-in-depth it also injects `/no_think` and
strips `<think>...</think>` tags from the response.

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

## Rollback

If anything breaks, flip the provider back — no code deploy needed:

```bash
# on VPS
sed -i 's/^LLM_PROVIDER=vllm/LLM_PROVIDER=yandex/' /var/www/englishbot/.env
docker compose up -d backend
```

`YANDEXGPT_API_KEY` remains in `.env` and is used immediately.

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
- Confirm `VLLMProvider` is deployed (env `LLM_PROVIDER=vllm` active).
