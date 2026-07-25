# ♻️ SpareCycles

![CI](https://github.com/stevologic/spare-cycles/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-5b5bd6.svg)

**Website: [stevologic.github.io/spare-cycles](https://stevologic.github.io/spare-cycles/)**

**Some devs have tokens and no ideas. Some have ideas and no tokens.**

SpareCycles is a marketplace of vibe-coding projects and a pool of donated AI
compute. Project owners get a queue and an OpenAI-compatible endpoint backed
by volunteers. Donors point their idle AI subscriptions / API keys at projects
they believe in and get credited as **AI Donors** on the project page —
GoFundMe, but the currency is tokens.

> Read [VISION.md](VISION.md) for the full vetting: what's strong, what's
> hard (trust, ToS, privacy, cold start), and how the design answers it.

```
   idea-rich, token-poor                        token-rich, idea-poor
 ┌──────────────────────┐                     ┌──────────────────────┐
 │ Hermes / OpenClaw /  │  /v1/chat/…  ┌────┐ │  node_connector.py   │
 │ aider / curl / any   ├─────────────►│    │◄┤  polls for work      │
 │ OpenAI-style client  │◄─────────────┤pool│─┤  runs claude/codex/  │
 └──────────────────────┘   answer     │    │ │  gemini/API runners  │
 ┌──────────────────────┐              │    │ │  KEYS STAY LOCAL     │
 │ project batch queue  ├─────────────►│    │ └──────────────────────┘
 │ (web UI, public feed)│              └────┘   credited as AI Donor
 └──────────────────────┘             FastAPI+SQLite, one process
```

## Run the server

```bash
pip install -r server/requirements.txt
uvicorn server.main:app --port 8377
```

Open http://localhost:8377 — create an account (you get an API key **plus
five one-time recovery codes**, shown once, stored hashed), create a project
(you get an inference key for its realtime endpoint), and browse the
marketplace.

**Lost your account key?** Two ways back in, both of which invalidate the old
key: trade a recovery code on the Account page (`POST /api/recover`), or run
`python connector/node_connector.py --recover` on any machine with a paired
node — possession of a paired node proves account ownership.

## Become a donor — three ways

Every path starts the same: sign in on the pool's web UI → **Account** →
*Generate pairing code*. Your provider keys **never leave your machine** —
only prompts and answers move. Then pick whichever fits:

| | Best for |
|---|---|
| [1 · Your own machine](#1--your-own-machine) | laptops & desktops with AI CLIs, keys, or Ollama/LM Studio |
| [2 · Docker](#2--docker) | servers, NAS boxes, "just keep it running" |
| [3 · GitHub Action](#3--github-action-donate-your-ci-minutes) | donating your repo's spare CI minutes on a schedule |

### 1 · Your own machine

Needs only Python 3.11+ — the connector is one file, zero pip installs.

```bash
# pair once — after this it remembers you (~/.sparecycles/node.json)
python connector/node_connector.py --server https://your-pool.example.com --code AB12-CD34
```

```bash
# every time after that
python connector/node_connector.py
```

It auto-detects what you can donate with — `claude` / `codex` / `gemini` /
`grok` / `cursor-agent` CLIs, metered keys in env (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `XAI_API_KEY`), or a local **Ollama** (`:11434`) /
**LM Studio** (`:1234`) — and advertises exactly those models to the pool.

```bash
python connector/node_connector.py --status        # am I online? what am I serving?
python connector/node_connector.py --runners echo  # test the loop, spend nothing
python connector/node_connector.py --max-seconds 1200 --idle-exit 120  # a bounded shift
```

Run it in the background with `nohup … &`, a systemd unit
([DEPLOY.md](DEPLOY.md) has one), or Task Scheduler — it reconnects on its
own and exits cleanly on SIGTERM.

### 2 · Docker

The image is published at **`ghcr.io/stevologic/spare-cycles`** (amd64 + arm64,
so a Raspberry Pi works).

```bash
# pair once — the node identity is saved into the named volume
docker run -it -v sparecycles:/data ghcr.io/stevologic/spare-cycles \
  --server https://your-pool.example.com --code AB12-CD34
```

```bash
# serve forever — pass whichever provider keys you donate with
docker run -d --name sparecycles-node --restart unless-stopped \
  -v sparecycles:/data \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  ghcr.io/stevologic/spare-cycles
```

Donating a local Ollama through the container:

```bash
docker run -d --restart unless-stopped -v sparecycles:/data \
  --add-host=host.docker.internal:host-gateway \
  -e OLLAMA_HOST=http://host.docker.internal:11434 \
  ghcr.io/stevologic/spare-cycles
```

Watch it work with `docker logs -f sparecycles-node`; check health with
`docker run --rm -v sparecycles:/data ghcr.io/stevologic/spare-cycles --status`.

### 3 · GitHub Action (donate your CI minutes)

A scheduled workflow checks in, serves the queue for N minutes, and leaves —
quitting early if there's no work.

**Step 1 — get a node token.** Pair once on any machine (either command from
options 1–2), then copy `node_token` (`scn_…`) out of
`~/.sparecycles/node.json` (Docker: `docker run --rm -v sparecycles:/data
--entrypoint cat ghcr.io/stevologic/spare-cycles /data/node.json`).

**Step 2 — add repo secrets** (Settings → Secrets → Actions):
`SPARECYCLES_NODE_TOKEN`, plus the provider keys you donate with
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY` — any subset).

**Step 3 — add the workflow.** Copy
[examples/donate.yml](examples/donate.yml) to
`.github/workflows/donate.yml`, or write your own:

```yaml
- uses: stevologic/spare-cycles@main
  with:
    server: https://your-pool.example.com
    node-token: ${{ secrets.SPARECYCLES_NODE_TOKEN }}
    models: "claude*,gpt*"   # what you're willing to serve
    minutes: 20
    idle-exit: 120           # quit early if the queue stays empty
  env:                       # provider keys = which runners you donate with
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
```

CI runners have no AI CLIs, so Action donations flow through the direct API
runners — metered keys, the ToS-clean way to give.

## Use the pool from your tools

Any OpenAI-compatible client works — set the base URL and use the project's
inference key:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8377/v1", api_key="sci_...")
r = client.chat.completions.create(
    model="default",  # or override the project's model preference
    messages=[{"role": "user", "content": "Plan the refactor of miner.py"}],
)
print(r.choices[0].message.content)
```

Jobs carry the project's **model preference + fallback**, **temperature**,
and **max_tokens**; nodes only claim jobs whose model they declared they can
serve. CLI runners apply what their CLI supports; the direct-API runners
honor temperature/max_tokens exactly.

**Karma (v1):** projects whose owners donate get a modest queue boost — at
equal priority, a donor's job is claimed before a non-donor's, FIFO otherwise.
Boolean, not proportional, so newcomers queue behind donors but are never
starved. Donating is how you cut your own queue time.

## Tests

```bash
pip install -r server/requirements.txt -r requirements-dev.txt
pytest
```

52 tests cover the API surface, queue protocol, recovery paths, karma
ordering, and a full realtime round-trip through a live server and the real
connector script; CI runs them on Linux and Windows.

## Deploy to the internet (DigitalOcean droplet)

The pool never runs inference, so the **cheapest droplet works** ($6/mo,
1 GB RAM). Everything below also applies to any Ubuntu VPS.

### 1 · Create the droplet

Ubuntu 24.04 (or 22.04), any size, SSH key auth. Note its public IP.

### 2 · Point DNS at it

Add an **A record** — e.g. `pool.yourdomain.com → <droplet IP>`. This must
resolve before HTTPS can be issued; if you want to try things first, skip it
and use plain HTTP on the IP (step 3 works either way).

### 3 · One command on the droplet

```bash
curl -fsSL https://raw.githubusercontent.com/stevologic/spare-cycles/main/deploy/setup-droplet.sh \
  | sudo DOMAIN=pool.yourdomain.com bash
```

(No domain yet? Leave `DOMAIN=` off — it serves HTTP on the IP. Re-run the
same command with the domain later; it's idempotent.)

That installs Docker, opens the firewall (SSH/80/443 only), and starts the
stack in `/opt/sparecycles`:

| Piece | Job |
|---|---|
| `server` | `ghcr.io/stevologic/spare-cycles-server` — API + web UI, SQLite in the `sc-data` volume |
| `caddy` | automatic Let's Encrypt HTTPS, long-poll-safe proxying |
| `watchtower` | pulls the latest server image every 5 min, restarts cleanly |
| `restart: unless-stopped` + Docker on boot | survives crashes and reboots |

### 4 · First run

1. Open `https://pool.yourdomain.com` → **Account** → create your account.
   **Save the API key and the five recovery codes** — they are shown once.
2. Create your first project (you'll get its `sci_` inference key — save it).
3. **Account → Generate pairing code**, then connect your first node from any
   machine with tokens to spare:
   ```bash
   python connector/node_connector.py --server https://pool.yourdomain.com --code AB12-CD34
   ```
4. Optional — live model catalogs in the New-project form: put provider keys
   in `/opt/sparecycles/.env` (`ANTHROPIC_API_KEY=` etc., metadata only,
   never used for inference), then `cd /opt/sparecycles && docker compose up -d`.

### Day-2 operations

```bash
cd /opt/sparecycles
docker compose ps                  # is everything up?
docker compose logs -f server      # watch the pool work
curl -s localhost/api/health       # {"ok":true,...} — point your uptime monitor here
bash update.sh                     # manual update (Watchtower does it automatically)
```

Backup (cron-friendly — everything lives in one SQLite file in the
`sc-data` volume):

```bash
docker compose exec -T server python -c \
  "import sqlite3; sqlite3.connect('/data/sparecycles.db').execute(\"VACUUM INTO '/data/backup.db'\")" \
  && docker cp "$(docker compose ps -q server)":/data/backup.db ./sparecycles-$(date +%F).db
```

Restore: copy a backup over `/data/sparecycles.db` in the volume and
`docker compose restart server`.

### If something's off

- **No HTTPS certificate** → DNS isn't resolving yet (`dig pool.yourdomain.com`).
  Caddy retries on its own once the record propagates.
- **Site unreachable** → `docker compose ps` (all three services should be
  `running`), then `docker compose logs caddy`.
- Port `8377` is deliberately **not** public — all traffic goes through Caddy.
- More (bare-metal systemd path, Cloudflare tunnel, nginx timeouts):
  [DEPLOY.md](DEPLOY.md).

## Safety model (short version)

- **Keys never leave the node.** The server stores only salted-hash
  credentials it issued itself; provider keys live in node env vars.
- **Everything is public by default.** Job prompts and outputs are visible on
  the project page — sunlight is the anti-abuse mechanism. Never submit secrets.
- **Nodes opt in per project.** Nothing is served that you didn't choose.
- **CLI runners execute in an empty scratch dir** with agentic turns capped;
  API runners have no tool access at all.
- **Donation-only.** No payments, no token resale. Node operators are
  responsible for their own provider terms; metered API keys (or, soon, local
  models via ollama) are the recommended way to donate.

## Repo map

| Path | What |
|---|---|
| `server/` | FastAPI app + SQLite (`server.main:app`) |
| `web/` | zero-build vanilla JS UI served at `/`, plus generated brand assets |
| `connector/node_connector.py` | the node agent (stdlib only) |
| `deploy/` | droplet stack: compose + Caddy + Watchtower + bootstrap script |
| `tools/generate_brand_assets.py` | regenerates favicons, app icons & the link-preview card |
| `VISION.md` | the vetted vision & roadmap |

Brand assets (favicon, iOS home-screen icon, OG link-preview card) are
generated — edit `tools/generate_brand_assets.py` and re-run it
(`pip install pillow`) rather than hand-editing the PNGs. Link previews need
an absolute URL: the server derives it from the request, or set
`SPARECYCLES_PUBLIC_URL=https://your.domain` when behind a proxy or tunnel.

Roadmap highlights (see VISION.md): spendable karma credits, GitHub `issue`
jobs that open PRs, pledge scheduling, trusted-donor tiers.

## License

[MIT](LICENSE) — run your own pool, fork it, embed it. Contributions welcome;
CI runs the full suite on every PR.
