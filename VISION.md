# PromptPool — Vision & Vetting

> **Some devs have tokens and no ideas. Some have ideas and no tokens.**
> PromptPool is the place where they trade — a marketplace of vibe-coding
> projects, and a pool of donated AI compute that keeps them moving.

This document is the honest vetting of the idea: what's genuinely strong,
what's genuinely hard, and what we added to the vision to make it something
every vibe coder would actually want to use.

---

## 1. What's strong about the original idea

**The pain is real and it is daily.** Every serious AI-assisted developer has
hit "you're out of usage until 7pm" mid-flow, and every one of them has also
had nights where a Max subscription or an API budget sat completely idle.
Supply and demand exist *inside the same community*, often inside the same
person on different days. That's the best possible market condition.

**The GoFundMe framing beats the "GPU rental" framing.** Paid compute
marketplaces (vast.ai, RunPod) already exist and are commodity businesses.
Nobody has built the *patronage* version for LLM work: "I believe in your
open-source project, so my idle agent works your issue queue tonight."
Donation sidesteps payments, pricing, and most legal weight — and it matches
how open source actually runs (sponsors, stars, contributors).

**The OpenAI-compatible proxy is the killer feature.** If submitting work is
`POST /v1/chat/completions` with a project key, then *every existing tool* —
Hermes Desktop, OpenClaw, Cline, aider, LangChain, a raw `openai` client —
can use the pool by changing one base URL. Zero integration cost is what
makes adoption plausible.

**GitHub-centricity gives the work provenance.** A donated answer in a chat
window is ephemeral. A donated answer that becomes a branch and a PR on the
project's repo is a *contribution* — reviewable, attributable, and visible on
a contributor graph. That's the difference between charity and community.

**Keys stay on the node.** Never custodying API keys is the single most
important architectural decision in the original idea, and it's correct.
The service moves *prompts and answers*, never credentials.

---

## 2. The hard problems (and what we do about them)

An idea this social has failure modes that are social, not technical.
Ignoring them is how you build a spam funnel with a leaderboard. Facing them:

### 2.1 Trust runs in both directions

*A node operator is executing text written by a stranger.* If the runner has
tool access, a hostile prompt can read the operator's filesystem or exfiltrate
env vars — including the very API keys we promised never to move.

*A submitter is accepting text written by a stranger's model.* It can be
wrong, lazy, or malicious (poisoned code suggestions).

**Mitigations built into the design:**
- The connector runs CLI agents **in an empty scratch working directory**,
  non-interactive, with agentic turns capped — not in the operator's repos.
- Direct-API runners (Anthropic/OpenAI over HTTPS) have **no tool access at
  all** and are the recommended default for serving strangers.
- Donated code lands as **a PR, never a merge**. Human review is the trust
  boundary on the receiving side, exactly as it is for human contributors.
- **Everything is public by default.** Every job's prompt and output is
  visible on the project page. Sunlight is the anti-abuse mechanism: nobody
  routes their private chatbot traffic through a queue the whole internet
  can read.
- Operators choose **which projects they serve**, per node. Nothing is
  opt-out; everything is opt-in.

### 2.2 Provider terms of service

This must be said plainly: **flat-rate subscription seats (Claude Pro/Max,
ChatGPT Plus) generally prohibit resharing access as a service.** Metered
API keys are different — that's your own pay-per-use spend, and donating
*outputs you paid for* is the clean model.

So the platform's stance is:
- PromptPool is **donation-only. No money moves. Tokens are never sold.**
  This keeps the platform out of "reselling API access" territory.
- Each node operator is responsible for their own provider agreement. The
  connector documentation says so, and the direct-API runners (metered keys,
  clearly compliant) are the recommended path.
- **Local models close the loop entirely.** An `ollama` runner (roadmap)
  makes "donating compute" literal — your GPU, your electricity, zero ToS
  questions. This is the long-term center of gravity for the donation pool.

### 2.3 Privacy

Whoever processes your prompt reads your prompt. There is no way around
this, so the product must never pretend otherwise:
- Positioning is **for open-source projects** — work that's already public.
- The UI warns at submission: *prompts are public and processed by
  volunteers; never include secrets.*
- Roadmap: **trusted-donor tiers** — maintainers can mark specific donors
  trusted and route sensitive queues only to them (web of trust, like
  maintainer bits in package ecosystems).

### 2.4 Quality and verification

You cannot cryptographically prove a volunteer used the requested model or
gave an honest answer. Don't build fake assurances; build reputation:
- Every completion is attributed to a node and an account. Bad output is
  visible in public job logs next to the donor's name.
- Model preference + fallback is **matched at claim time** — a node only
  receives jobs it declared it can serve.
- PR-based work is **self-verifying**: CI runs it, a maintainer reviews it.
- Roadmap: spot-check jobs (same prompt to two donors, diff), and donor
  reputation scores derived from accepted-vs-rejected PRs.

### 2.5 Cold start

A two-sided marketplace with zero donors is a static website. The design
dodges this because **PromptPool is useful at N=1 account**: connect your
own three machines (work box with Claude, home box with a metered key,
laptop with codex) and you have a personal compute pool — one URL that
fans out to whichever of your own runners is alive. Donation is a *layer*
on top of a tool that's already worth running selfishly. That's the same
bootstrap that made BitTorrent work: seeding is a side effect of using it.

### 2.6 Realtime latency honesty

Volunteer nodes on long-poll will answer in seconds-to-minutes, not
milliseconds. That's fine for agent workflows and batch queues; it's not a
snappy chatbot. The proxy sets honest timeouts, supports fallback models,
and the docs say: realtime is *best-effort*; batch is the reliable core.

---

## 3. Additions to the vision

Things the original idea implied but didn't name — these are what make it
sticky for every vibe coder, not just the altruistic ones:

**Karma credits (non-monetary reciprocity).** Donating processing earns
credits; credits give *your* projects priority in the queue when you're the
one out of tokens. Tit-for-tat turns one-way charity into a flywheel:
tonight my idle Max serves your issue queue, next week your nodes carry me
past my rate limit. (Credits are never purchasable — that would re-import
every payment/ToS problem we just avoided.)

**AI Donors as a first-class identity.** Public donor leaderboards per
project and globally; "Powered by 12 AI Donors" badges for READMEs; donor
profiles listing the projects they've carried. GitHub made a green graph a
status symbol; PromptPool makes donated tokens one.

**Pledges, not just polling.** "My node serves `doge-miner` nightly,
11pm–7am, cap 200k tokens/night." Scheduled, capped, recurring patronage —
the GoFundMe recurring-donation model, but for compute.

**Runner adapters as plugins.** CLI runners (claude / codex / gemini /
cursor-agent), direct-API runners (which honor temperature and max_tokens
*exactly*, where CLIs can't), a builtin `echo` runner for testing, and
roadmap `ollama` for local models. One JSON template per runner; adding a
new CLI is config, not code.

**Three job kinds, one pipeline.**
1. `realtime` — the OpenAI-compatible proxy call; a tool any agent can call.
2. `batch` — queued prompts; results collected on the project page.
3. `issue` (roadmap) — node clones the linked repo in a sandbox, works the
   GitHub issue on a branch, opens a PR credited to the donor.

**Transparency as a feature.** The public job feed doubles as the project's
"AI activity" page — proof of life for the project, provenance for answers,
and the strongest spam deterrent available.

---

## 4. What ships in this MVP

| Piece | Status |
|---|---|
| FastAPI server + SQLite, single process | ✅ |
| Accounts with API keys (hashed at rest, shown once) | ✅ |
| Project marketplace: create, browse, support, donor leaderboards | ✅ |
| Model preference + fallback, temperature, max_tokens per project/job | ✅ |
| Batch prompt queue with public job feed | ✅ |
| OpenAI-compatible `/v1/chat/completions` realtime proxy (incl. streaming) | ✅ |
| `node-connector.py` — stdlib-only, pairing-code onboarding, auto-detects runners | ✅ |
| Runners: claude CLI, codex CLI, gemini CLI, cursor-agent, direct Anthropic API, direct OpenAI API, echo | ✅ |
| Keys stay in node env vars; never transmitted, never stored server-side | ✅ |
| Karma credits ledger | roadmap |
| GitHub `issue` job kind → branch → PR | roadmap |
| Pledge scheduling, trusted-donor tiers, ollama runner, spot-checks | roadmap |

The MVP is deliberately one `pip install` + one script, because the entire
thesis is that this must be *clean and simple to run* — a vibe coder should
go from "found the repo" to "my node is serving a project" in five minutes.
