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

## Connect a node (become a donor)

On any machine that has an AI CLI (`claude`, `codex`, `gemini`, `grok`,
`cursor-agent`), a metered provider key in the environment
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`), **or a local model
server** — Ollama (`:11434`) and LM Studio (`:1234`) are auto-detected and
your installed models are advertised to the pool by name:

1. Web UI → **Account** → *Generate pairing code*
2. ```bash
   python connector/node_connector.py --server http://localhost:8377 --code XXXX-XXXX
   ```
3. **Support** projects in the marketplace. Your node serves only queues you
   opted into, and every completed job credits you on the project page.

The connector is stdlib-only Python — nothing to install. It re-runs with no
arguments after the first pairing (`~/.sparecycles/node.json` holds the node
token; never your provider keys). A built-in `echo` runner
(`--runners echo`) lets you test the full loop without spending a token.

Once it's running in the background, check on it any time — read-only, so it
is safe alongside a polling connector:

```bash
python connector/node_connector.py --status
```

It prints the runners this machine can serve, whether the pool sees your node
as online, which queues it serves, and its last few jobs.

## Donate your CI minutes (GitHub Action)

Your repo's Actions runners can check in as donor nodes too — a scheduled
workflow that serves the queue for N minutes, then leaves:

```yaml
- uses: stevologic/spare-cycles@main
  with:
    server: https://your-pool.example.com
    node-token: ${{ secrets.SPARECYCLES_NODE_TOKEN }}
    models: "claude*,gpt*"
    minutes: 20
    idle-exit: 120        # quit early if the queue stays empty
  env:                     # provider keys = which runners you donate with
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    XAI_API_KEY: ${{ secrets.XAI_API_KEY }}
```

Full copy-paste workflow (nightly cron + manual trigger) in
[examples/donate.yml](examples/donate.yml). The node token comes from pairing
once anywhere (`node_connector.py --server … --code …` → copy `node_token`
out of `~/.sparecycles/node.json` into a repo secret). CI hosts have no AI
CLIs, so donations flow through the direct API runners — metered keys, the
ToS-clean way to give. Time-boxed check-ins also work anywhere else:
`node_connector.py --max-seconds 1200 --idle-exit 120`.

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

## Tests & deploying

```bash
pip install -r server/requirements.txt -r requirements-dev.txt
pytest
```

52 tests cover the API surface, queue protocol, recovery paths, karma
ordering, and a full realtime round-trip through a live server and the real
connector script; CI runs them on Linux and Windows. See
[DEPLOY.md](DEPLOY.md) for the droplet/systemd/Caddy production guide —
the short version is `uvicorn server.main:app` behind any HTTPS proxy, with
`GET /api/health` for your uptime monitor.

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
