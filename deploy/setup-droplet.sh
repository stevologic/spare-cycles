#!/usr/bin/env bash
# SpareCycles droplet bootstrap — fresh Ubuntu 22.04/24.04 (DigitalOcean etc.)
#
#   curl -fsSL https://raw.githubusercontent.com/stevologic/spare-cycles/main/deploy/setup-droplet.sh \
#     | sudo DOMAIN=pool.example.com bash
#
# Idempotent: safe to re-run. What it does:
#   1. installs Docker (with the compose plugin) if missing
#   2. opens the firewall for SSH/HTTP/HTTPS (ufw)
#   3. drops the compose stack into /opt/sparecycles
#   4. writes .env (DOMAIN from the environment, if given)
#   5. docker compose pull && docker compose up -d
#
# After that the stack keeps itself alive and current:
#   - restart: unless-stopped + the Docker service -> survives reboots
#   - Watchtower pulls new server images every 5 min and restarts cleanly
#   - Caddy fetches/renews Let's Encrypt certificates when DOMAIN is set

set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/stevologic/spare-cycles/main/deploy"
DIR="/opt/sparecycles"
DOMAIN="${DOMAIN:-}"

say() { printf '\n\033[1;35m♻️  %s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (or via sudo)." >&2
  exit 1
fi

# 1 · Docker ------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker…"
  curl -fsSL https://get.docker.com | sh
else
  say "Docker already installed."
fi
systemctl enable --now docker

# 2 · Firewall ----------------------------------------------------------------
if command -v ufw >/dev/null 2>&1; then
  say "Opening firewall: SSH, 80, 443…"
  ufw allow OpenSSH >/dev/null
  ufw allow 80/tcp  >/dev/null
  ufw allow 443/tcp >/dev/null
  ufw --force enable >/dev/null
fi

# 3 · Stack files -------------------------------------------------------------
say "Installing the stack into $DIR…"
mkdir -p "$DIR"
cd "$DIR"
for f in docker-compose.yml Caddyfile; do
  # Local copy wins (running from a git checkout); otherwise fetch from main.
  if [ -f "$(dirname "$0")/$f" ] 2>/dev/null && [ "$(dirname "$0")" != "$DIR" ]; then
    cp "$(dirname "$0")/$f" "$DIR/$f"
  else
    curl -fsSL "$REPO_RAW/$f" -o "$DIR/$f"
  fi
done

# 4 · Environment -------------------------------------------------------------
if [ ! -f .env ]; then
  say "Writing .env…"
  {
    echo "DOMAIN=$DOMAIN"
    echo "SPARECYCLES_PUBLIC_URL="
    echo "ANTHROPIC_API_KEY="
    echo "OPENAI_API_KEY="
    echo "XAI_API_KEY="
  } > .env
elif [ -n "$DOMAIN" ]; then
  say "Updating DOMAIN in existing .env…"
  sed -i "s/^DOMAIN=.*/DOMAIN=$DOMAIN/" .env
fi

# 5 · Up ----------------------------------------------------------------------
say "Pulling images and starting the stack…"
docker compose pull
docker compose up -d
docker compose ps

say "Done."
if [ -n "$DOMAIN" ]; then
  echo "  → point a DNS A record for $DOMAIN at this droplet, then open https://$DOMAIN"
else
  echo "  → open http://<this-droplet-ip>  (set DOMAIN in $DIR/.env and re-run for HTTPS)"
fi
echo "  → health:  curl -s localhost/api/health"
echo "  → logs:    docker compose -f $DIR/docker-compose.yml logs -f server"
echo "  → update:  automatic (Watchtower); manual: bash $DIR/update.sh"

# Drop the manual updater next to the stack for convenience.
if [ ! -f update.sh ]; then
  curl -fsSL "$REPO_RAW/update.sh" -o update.sh 2>/dev/null || true
  chmod +x update.sh 2>/dev/null || true
fi
