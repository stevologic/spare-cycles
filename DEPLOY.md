# Deploying SpareCycles

The whole coordinator is one Python process with a SQLite file — a $6/month
droplet (or a spare machine + tunnel) runs it comfortably. The server never
performs inference, so it needs no GPU and barely any CPU: it shuffles small
JSON between submitters and donor nodes.

## Option A — Docker Compose on a droplet (recommended)

One command on a fresh Ubuntu droplet:

```bash
curl -fsSL https://raw.githubusercontent.com/stevologic/spare-cycles/main/deploy/setup-droplet.sh \
  | sudo DOMAIN=pool.example.com bash
```

(Leave `DOMAIN=` off to serve plain HTTP on the droplet's IP while you try
things out; re-run with it later for HTTPS.)

That installs Docker, opens the firewall (SSH/80/443), and starts the stack
in `/opt/sparecycles` — after which the pool stays online and current by
itself:

| Piece | Job |
|---|---|
| `server` | `ghcr.io/stevologic/spare-cycles-server` — API + web UI, SQLite in the `sc-data` volume |
| `caddy` | automatic Let's Encrypt HTTPS when `DOMAIN` is set; long-poll-safe proxying |
| `watchtower` | **pulls the latest server image every 5 min** and restarts it cleanly |
| `restart: unless-stopped` + Docker's systemd unit | everything comes back after reboots and crashes |

Prefer doing it by hand? The same stack is three files:

```bash
sudo mkdir -p /opt/sparecycles && cd /opt/sparecycles
sudo curl -fsSLO https://raw.githubusercontent.com/stevologic/spare-cycles/main/deploy/docker-compose.yml
sudo curl -fsSLO https://raw.githubusercontent.com/stevologic/spare-cycles/main/deploy/Caddyfile
echo "DOMAIN=pool.example.com" | sudo tee .env
sudo docker compose up -d
```

Manual update any time (Watchtower normally handles it):
`bash /opt/sparecycles/update.sh` — a `docker compose pull && docker compose up -d`.

## Option B — bare metal (venv + systemd)

```bash
sudo apt update && sudo apt install -y python3-venv git
sudo useradd -r -m -d /opt/sparecycles -s /usr/sbin/nologin sparecycles
sudo -u sparecycles git clone https://github.com/stevologic/spare-cycles.git /opt/sparecycles/app
cd /opt/sparecycles/app
sudo -u sparecycles python3 -m venv .venv
sudo -u sparecycles .venv/bin/pip install -r server/requirements.txt
```

`/etc/systemd/system/sparecycles.service`:

```ini
[Unit]
Description=SpareCycles coordinator
After=network.target

[Service]
User=sparecycles
WorkingDirectory=/opt/sparecycles/app
Environment=SPARECYCLES_PUBLIC_URL=https://your.domain
ExecStart=/opt/sparecycles/app/.venv/bin/uvicorn server.main:app --host 127.0.0.1 --port 8377
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now sparecycles
curl -s localhost:8377/api/health   # {"ok": true, ...}
```

## 2. HTTPS in front

**Caddy** (automatic certificates, sane long-poll defaults) — `/etc/caddy/Caddyfile`:

```
your.domain {
    reverse_proxy 127.0.0.1:8377
}
```

**nginx** users: raise the read timeout above the long-poll windows or node
polls (25 s) and realtime waits (up to 180 s) will be cut off mid-request:

```nginx
location / {
    proxy_pass http://127.0.0.1:8377;
    proxy_read_timeout 300s;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
}
```

The `X-Forwarded-*` headers (or `SPARECYCLES_PUBLIC_URL`) are what make link
previews carry absolute URLs.

## 3. Environment variables

| Variable | Purpose |
|---|---|
| `SPARECYCLES_PUBLIC_URL` | Absolute origin for link previews when the public hostname differs from what reaches the app |
| `SPARECYCLES_DATA` | Data directory (default `./data`) — the SQLite DB lives here |
| `SPARECYCLES_RATELIMIT` | Set to `off` to disable the built-in register/recover throttle (tests, trusted LANs) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `XAI_API_KEY` | Optional: light up live model catalogs in the New-project form. Used **only** to list models, never for inference |

## 4. Backups

Everything lives in one SQLite file.

Docker stack (Option A) — safe online backup from cron:

```bash
docker compose -f /opt/sparecycles/docker-compose.yml exec -T server \
  python -c "import sqlite3; sqlite3.connect('/data/sparecycles.db').execute(\"VACUUM INTO '/data/backup.db'\")" \
  && docker cp "$(docker compose -f /opt/sparecycles/docker-compose.yml ps -q server)":/data/backup.db \
       /backups/sparecycles-$(date +%F).db
```

Bare metal (Option B):

```bash
sqlite3 /opt/sparecycles/app/data/sparecycles.db ".backup /backups/sparecycles-$(date +%F).db"
```

## 5. Monitoring

`GET /api/health` returns `{"ok": true, "uptime_seconds": ..., "accounts": ...}`
with a real DB round-trip — point any uptime checker at it.

## 6. No VPS? Tunnel out

The server runs fine on a desktop behind NAT:

```bash
uvicorn server.main:app --port 8377
cloudflared tunnel --url http://localhost:8377   # or `ngrok http 8377`
```

Set `SPARECYCLES_PUBLIC_URL` to the tunnel URL so shared links render previews.

## 7. A donor node as a service

On any machine that should donate around the clock —
`/etc/systemd/system/sparecycles-node.service`:

```ini
[Unit]
Description=SpareCycles donor node
After=network-online.target

[Service]
User=youruser
ExecStart=/usr/bin/python3 /home/youruser/spare-cycles/connector/node_connector.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Pair it once interactively (`--server ... --code ...`), then enable the unit.
The connector exits cleanly on SIGTERM and reconnects after network blips;
check on it any time with `node_connector.py --status`.
