#!/usr/bin/env bash
# Manual update of the SpareCycles stack (Watchtower normally does this for
# you every 5 minutes): pull the latest images, restart what changed, prune.
set -euo pipefail
cd "$(dirname "$0")"
docker compose pull
docker compose up -d
docker image prune -f
docker compose ps
