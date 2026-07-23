# ♻️ SpareCycles

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
| `web/` | zero-build vanilla JS UI served at `/` |
| `connector/node_connector.py` | the node agent (stdlib only) |
| `VISION.md` | the vetted vision & roadmap |

Roadmap highlights (see VISION.md): karma credits, GitHub `issue` jobs that
open PRs, pledge scheduling, trusted-donor tiers, ollama runner.
