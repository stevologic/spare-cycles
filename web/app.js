/* PromptPool web UI — vanilla JS hash router, no build step. */

const $app = document.getElementById("app");
let refreshTimer = null;

const key = () => localStorage.getItem("pp_key") || "";
const setKey = (k) => k ? localStorage.setItem("pp_key", k) : localStorage.removeItem("pp_key");

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (key() && !headers.Authorization) headers.Authorization = "Bearer " + key();
  const res = await fetch(path, { ...opts, headers });
  let body = null;
  try { body = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new Error(body?.detail || body?.error?.message || res.statusText);
  return body;
}

const fmtN = (n) => {
  n = n || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
};
const ago = (t) => {
  if (!t) return "";
  const s = Math.max(1, Math.floor(Date.now() / 1000 - t));
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
};

function render(html) {
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
  $app.innerHTML = html;
}

// ------------------------------------------------------------------ home

async function viewHome() {
  render(`<p class="muted">Loading marketplace…</p>`);
  const [stats, list] = await Promise.all([api("/api/stats"), api("/api/projects")]);
  const cards = list.projects.map((p) => `
    <a class="card" href="#/p/${esc(p.slug)}" style="color:inherit;text-decoration:none">
      <h3>${esc(p.name)}</h3>
      <div class="tagline">${esc(p.tagline || p.description.slice(0, 120) || "A vibe-coding project looking for AI donors.")}</div>
      <div class="meta">
        <span>by <b>${esc(p.owner)}</b></span>
        <span><b>${fmtN(p.jobs_done)}</b> jobs done</span>
        <span><b>${fmtN(p.tokens_donated)}</b> tokens donated</span>
        <span><b>${p.supporters}</b> ${p.supporters === 1 ? "supporter" : "supporters"}</span>
        ${p.jobs_open ? `<span class="badge queued">${p.jobs_open} open</span>` : ""}
      </div>
    </a>`).join("");
  render(`
    <div class="hero">
      <h1>Some devs have tokens and no ideas.<br>Some have ideas and no tokens.</h1>
      <p class="tag">PromptPool is a marketplace of vibe-coding projects and a pool of donated
      AI compute. Browse projects, point your idle agent at one, and become an
      <b>AI Donor</b> — or bring your project and let the pool carry it.</p>
      <div class="statrow">
        <span class="chip"><b>${stats.projects}</b> projects</span>
        <span class="chip"><b>${stats.nodes_online}</b> nodes online</span>
        <span class="chip"><b>${fmtN(stats.jobs_done)}</b> jobs completed</span>
        <span class="chip"><b>${fmtN(stats.tokens_donated)}</b> tokens donated</span>
      </div>
    </div>
    <div class="grid">${cards || `<div class="panel">No projects yet — <a href="#/new">create the first one</a>.</div>`}</div>
  `);
}

// ------------------------------------------------------------------ project

function jobDetails(j) {
  return `
    <details>
      <summary>
        <span class="badge ${esc(j.status)}">${esc(j.status)}</span>
        <span class="grow">${esc(j.title || j.prompt.slice(0, 80))}</span>
        <span class="muted">${esc(j.model)}${j.model_used && j.model_used !== j.model ? " → " + esc(j.model_used) : ""}</span>
        ${j.donor ? `<span class="muted">🤝 ${esc(j.donor)}</span>` : ""}
        <span class="muted">${ago(j.created_at)}</span>
      </summary>
      <div class="jobbody">
        <h4>Prompt</h4><pre>${esc(j.prompt)}</pre>
        ${j.output ? `<h4>Output ${j.runner ? `<span class="muted">(runner: ${esc(j.runner)}, ${fmtN(j.tokens_out)} tokens out)</span>` : ""}</h4><pre>${esc(j.output)}</pre>` : ""}
        ${j.error && j.status !== "done" ? `<h4>Error</h4><pre>${esc(j.error)}</pre>` : ""}
      </div>
    </details>`;
}

async function viewProject(slug) {
  render(`<p class="muted">Loading project…</p>`);
  let me = null;
  if (key()) { try { me = await api("/api/me"); } catch { setKey(""); } }
  const [p, jobs] = await Promise.all([
    api(`/api/projects/${slug}`),
    api(`/api/projects/${slug}/jobs`),
  ]);
  const isOwner = me?.projects?.some((x) => x.slug === slug);
  const supporting = me?.supports?.some((x) => x.slug === slug);
  const origin = window.location.origin;
  const donors = p.donors.length
    ? `<table class="donors"><tr><th></th><th>AI Donor</th><th>Jobs</th><th>Tokens</th></tr>
       ${p.donors.map((d, i) => `<tr>
          <td>${["🥇", "🥈", "🥉"][i] || i + 1}</td>
          <td>${esc(d.display_name)} ${d.is_maintainer ? '<span class="badge owner">maintainer</span>' : ""}</td>
          <td>${fmtN(d.jobs)}</td><td>${fmtN(d.tokens)}</td></tr>`).join("")}
       </table>`
    : `<p class="muted small">No completed donations yet. Be the first AI Donor.</p>`;

  render(`
    <div class="panel projhead">
      <h2 class="pagetitle">${esc(p.name)}</h2>
      <div class="row">
        <span class="muted">by ${esc(p.owner)}</span>
        ${p.repo_url ? `<a href="${esc(p.repo_url)}" target="_blank" rel="noopener">↗ repository</a>` : ""}
        <span class="grow"></span>
        ${me && !isOwner ? `<button class="btn small ${supporting ? "ghost" : ""}" id="btn-support">
            ${supporting ? "✓ Supporting — stop" : "🤝 Support this project"}</button>` : ""}
        ${!me ? `<a class="btn small ghost" href="#/account">Sign in to support</a>` : ""}
      </div>
      ${p.tagline ? `<p>${esc(p.tagline)}</p>` : ""}
      ${p.description ? `<p class="muted small">${esc(p.description)}</p>` : ""}
      <div class="statrow" style="justify-content:flex-start">
        <span class="chip"><b>${fmtN(p.jobs_done)}</b> jobs done</span>
        <span class="chip"><b>${fmtN(p.tokens_donated)}</b> tokens donated</span>
        <span class="chip"><b>${p.supporters}</b> supporters</span>
        <span class="chip">model <b>${esc(p.model)}</b>${p.fallback_model ? ` → <b>${esc(p.fallback_model)}</b>` : ""}</span>
        ${p.temperature != null ? `<span class="chip">temp <b>${p.temperature}</b></span>` : ""}
        ${p.max_tokens ? `<span class="chip">max <b>${fmtN(p.max_tokens)}</b> tok</span>` : ""}
      </div>
    </div>

    <div class="panel">
      <h2>🏆 AI Donors</h2>
      ${donors}
      <p class="muted small mt">Supporting a project means your connected nodes volunteer to
      process its queue with <i>your</i> tokens. Every completed job credits you here.</p>
    </div>

    ${isOwner ? `
    <div class="panel">
      <h2>Queue a batch prompt</h2>
      <label>Title (optional)</label><input id="job-title" placeholder="Summarize open issues">
      <label>Prompt</label><textarea id="job-prompt" placeholder="Prompts are public and processed by volunteer nodes — never include secrets."></textarea>
      <div class="mt"><button class="btn" id="btn-queue">Queue job</button></div>
      <div id="queue-msg"></div>
    </div>` : ""}

    <div class="panel">
      <h2>Realtime endpoint</h2>
      <p class="muted small">Any OpenAI-compatible client can send this project's realtime
      work to the pool — Hermes Desktop, OpenClaw, aider, or plain curl. Use the project's
      inference key (shown once at creation) as the Bearer token.</p>
      <pre class="code">curl ${esc(origin)}/v1/chat/completions \\
  -H "Authorization: Bearer ppi_..." -H "Content-Type: application/json" \\
  -d '{"messages": [{"role": "user", "content": "hello pool"}]}'</pre>
    </div>

    <div class="panel">
      <h2>Job feed <span class="muted small">(public, refreshes live)</span></h2>
      <div class="joblist" id="joblist">${jobs.jobs.map(jobDetails).join("") || `<p class="muted small">Nothing queued yet.</p>`}</div>
    </div>
  `);

  document.getElementById("btn-support")?.addEventListener("click", async () => {
    try {
      await api(`/api/projects/${slug}/support`, { method: supporting ? "DELETE" : "POST" });
      viewProject(slug);
    } catch (e) { alert(e.message); }
  });
  document.getElementById("btn-queue")?.addEventListener("click", async () => {
    const msg = document.getElementById("queue-msg");
    try {
      const r = await api(`/api/projects/${slug}/jobs`, {
        method: "POST",
        body: JSON.stringify({
          title: document.getElementById("job-title").value,
          prompt: document.getElementById("job-prompt").value,
        }),
      });
      msg.innerHTML = `<p class="ok">Queued as job #${r.job_id} — a supporting node will pick it up.</p>`;
      document.getElementById("job-prompt").value = "";
      refreshJobs(slug);
    } catch (e) { msg.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
  });

  refreshTimer = setInterval(() => refreshJobs(slug), 5000);
}

async function refreshJobs(slug) {
  try {
    const jobs = await api(`/api/projects/${slug}/jobs`);
    const el = document.getElementById("joblist");
    const open = new Set([...(el?.querySelectorAll("details[open]") || [])]
      .map((d) => d.querySelector("summary .grow")?.textContent));
    if (el) {
      el.innerHTML = jobs.jobs.map(jobDetails).join("") || `<p class="muted small">Nothing queued yet.</p>`;
      el.querySelectorAll("details").forEach((d) => {
        if (open.has(d.querySelector("summary .grow")?.textContent)) d.open = true;
      });
    }
  } catch { /* transient */ }
}

// ------------------------------------------------------------------ new project

function viewNew() {
  if (!key()) { location.hash = "#/account"; return; }
  render(`
    <div class="panel">
      <h2>New project</h2>
      <p class="muted small">Your project gets a public marketplace page, a batch queue,
      and an OpenAI-compatible realtime endpoint backed by the pool.</p>
      <div class="formrow">
        <div><label>Name</label><input id="f-name" placeholder="doge-miner-fullstack"></div>
        <div><label>Repository URL</label><input id="f-repo" placeholder="https://github.com/you/project"></div>
      </div>
      <label>Tagline</label><input id="f-tagline" placeholder="One sentence that makes a donor care.">
      <label>Description</label><textarea id="f-desc"></textarea>
      <div class="formrow">
        <div><label>Preferred model</label><input id="f-model" placeholder="claude-sonnet-4-5" value="claude-sonnet-4-5"></div>
        <div><label>Fallback model</label><input id="f-fallback" placeholder="gpt-5-mini"></div>
      </div>
      <div class="formrow">
        <div><label>Temperature (optional — honored by API runners)</label><input id="f-temp" type="number" step="0.1" min="0" max="2"></div>
        <div><label>Max output tokens (optional)</label><input id="f-maxtok" type="number" min="1"></div>
      </div>
      <div class="mt"><button class="btn" id="btn-create">Create project</button></div>
      <div id="create-msg"></div>
    </div>
  `);
  document.getElementById("btn-create").addEventListener("click", async () => {
    const msg = document.getElementById("create-msg");
    const num = (id) => { const v = document.getElementById(id).value; return v === "" ? null : Number(v); };
    try {
      const r = await api("/api/projects", {
        method: "POST",
        body: JSON.stringify({
          name: document.getElementById("f-name").value,
          repo_url: document.getElementById("f-repo").value,
          tagline: document.getElementById("f-tagline").value,
          description: document.getElementById("f-desc").value,
          model: document.getElementById("f-model").value,
          fallback_model: document.getElementById("f-fallback").value,
          temperature: num("f-temp"),
          max_tokens: num("f-maxtok"),
        }),
      });
      msg.innerHTML = `
        <div class="notice"><b>Save this inference key — it is shown exactly once.</b>
        It is the Bearer token for this project's <code class="inline">/v1/chat/completions</code> endpoint.
        <div class="keybox">${esc(r.inference_key)}</div>
        <a href="#/p/${esc(r.slug)}">Open your project page →</a></div>`;
    } catch (e) { msg.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
  });
}

// ------------------------------------------------------------------ account

async function viewAccount() {
  if (!key()) {
    render(`
      <div class="panel">
        <h2>Create an account</h2>
        <div class="formrow">
          <div><label>Username (a-z, 0-9, dashes)</label><input id="r-name" placeholder="steph"></div>
          <div><label>Display name</label><input id="r-display" placeholder="Steph"></div>
        </div>
        <div class="mt"><button class="btn" id="btn-register">Create account</button></div>
        <div id="reg-msg"></div>
      </div>
      <div class="panel">
        <h2>Already have a key?</h2>
        <label>Account API key</label><input id="l-key" placeholder="ppk_...">
        <div class="mt"><button class="btn ghost" id="btn-login">Sign in</button></div>
        <div id="login-msg"></div>
      </div>
    `);
    document.getElementById("btn-register").addEventListener("click", async () => {
      const msg = document.getElementById("reg-msg");
      try {
        const r = await api("/api/register", {
          method: "POST",
          body: JSON.stringify({
            name: document.getElementById("r-name").value,
            display_name: document.getElementById("r-display").value,
          }),
        });
        setKey(r.api_key);
        msg.innerHTML = `
          <div class="notice"><b>Save your account key — it is shown exactly once.</b>
          It is stored hashed on the server; this browser keeps a copy locally so you stay signed in.
          <div class="keybox">${esc(r.api_key)}</div>
          <a href="#/account" onclick="viewAccount()">Continue →</a></div>`;
      } catch (e) { msg.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
    });
    document.getElementById("btn-login").addEventListener("click", async () => {
      const msg = document.getElementById("login-msg");
      setKey(document.getElementById("l-key").value.trim());
      try { await api("/api/me"); viewAccount(); }
      catch (e) { setKey(""); msg.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
    });
    return;
  }

  let me;
  try { me = await api("/api/me"); }
  catch { setKey(""); viewAccount(); return; }

  const nodes = me.nodes.map((n) => `
    <tr><td><span class="dot ${n.online ? "on" : "off"}"></span>${esc(n.name)}</td>
    <td class="muted">${esc((n.runners || []).join(", ") || "—")}</td>
    <td class="muted">${esc((n.models || []).join(", ") || "—")}</td>
    <td>${fmtN(n.jobs_done)}</td></tr>`).join("");

  render(`
    <div class="panel">
      <div class="row"><h2 class="pagetitle">@${esc(me.account.name)}</h2>
        <span class="muted">${esc(me.account.display_name)}</span>
        <span class="grow"></span>
        <button class="btn small ghost" id="btn-logout">Sign out</button></div>
    </div>

    <div class="panel">
      <h2>🖥️ My nodes</h2>
      ${nodes ? `<table class="donors"><tr><th>Node</th><th>Runners</th><th>Models</th><th>Jobs done</th></tr>${nodes}</table>`
              : `<p class="muted small">No nodes yet. Generate a pairing code and run the connector.</p>`}
      <div class="mt row">
        <button class="btn" id="btn-pair">Generate pairing code</button>
        <span class="muted small">Codes are single-use and expire in 15 minutes.</span>
      </div>
      <div id="pair-out"></div>
    </div>

    <div class="panel">
      <h2>📦 My projects</h2>
      ${me.projects.length
        ? me.projects.map((p) => `<div class="row mt"><a href="#/p/${esc(p.slug)}">${esc(p.name)}</a>
            <span class="muted small">model ${esc(p.model)}</span></div>`).join("")
        : `<p class="muted small">None yet — <a href="#/new">create one</a>.</p>`}
    </div>

    <div class="panel">
      <h2>🤝 Projects I support</h2>
      ${me.supports.length
        ? me.supports.map((s) => `<div class="row mt"><a href="#/p/${esc(s.slug)}">${esc(s.name)}</a></div>`).join("")
        : `<p class="muted small">None yet — <a href="#/">browse the marketplace</a>.</p>`}
      <p class="muted small mt">Your online nodes automatically serve the queues of projects
      you support (and your own projects).</p>
    </div>
  `);

  document.getElementById("btn-logout").addEventListener("click", () => { setKey(""); viewAccount(); });
  document.getElementById("btn-pair").addEventListener("click", async () => {
    try {
      const r = await api("/api/pair", { method: "POST" });
      document.getElementById("pair-out").innerHTML = `
        <div class="notice">On the machine with your AI CLI / API keys, run:
        <pre class="code">python connector/node_connector.py --server ${esc(location.origin)} --code ${esc(r.code)}</pre>
        The node inherits <b>your</b> identity: work it completes is credited to @${esc(me.account.name)}.</div>`;
    } catch (e) { alert(e.message); }
  });
}

// ------------------------------------------------------------------ docs pages

function viewConnect() {
  render(`
    <div class="panel">
      <h2>Connect a node</h2>
      <p>A node is any machine with an AI CLI (<code class="inline">claude</code>,
      <code class="inline">codex</code>, <code class="inline">gemini</code>,
      <code class="inline">cursor-agent</code>) or a metered API key in an environment
      variable (<code class="inline">ANTHROPIC_API_KEY</code>, <code class="inline">OPENAI_API_KEY</code>).
      The connector is one stdlib-only Python script — no pip installs.</p>
      <h3>1. Get a pairing code</h3>
      <p class="small muted">Sign in → <a href="#/account">Account</a> → <i>Generate pairing code</i>.</p>
      <h3>2. Run the connector on the node machine</h3>
      <pre class="code">python connector/node_connector.py --server ${esc(location.origin)} --code XXXX-XXXX</pre>
      <p class="small muted">It auto-detects available runners, registers the node under your
      account, saves its node token in <code class="inline">~/.promptpool/</code>, and starts
      polling for work from projects you support.</p>
      <h3>3. Support projects</h3>
      <p class="small muted">Browse the <a href="#/">marketplace</a> and hit <i>Support</i>.
      Your node only ever serves projects you explicitly chose. Every completed job lists you
      as an <b>AI Donor</b> on the project page.</p>
      <div class="notice"><b>Your keys never leave the node.</b> The connector sends prompts
      and answers — never credentials. CLI runners execute in an empty scratch directory with
      agentic turns capped; API runners have no tool access at all. You are responsible for
      staying within your provider's terms — metered API keys are the recommended way to donate.</div>
    </div>
  `);
}

function viewDocs() {
  const o = location.origin;
  render(`
    <div class="panel">
      <h2>API docs</h2>
      <h3>Realtime: OpenAI-compatible proxy</h3>
      <p class="small muted">Point any OpenAI-style client at the pool. The Bearer token is a
      <b>project inference key</b> (<code class="inline">ppi_…</code>). Model, temperature and
      max_tokens default to the project's settings; requests wait up to 180s for a donor node.</p>
      <pre class="code">curl ${esc(o)}/v1/chat/completions \\
  -H "Authorization: Bearer ppi_..." -H "Content-Type: application/json" \\
  -d '{"model": "claude-sonnet-4-5", "stream": false,
       "messages": [{"role": "user", "content": "Refactor plan for miner.py?"}]}'</pre>
      <pre class="code"># python
from openai import OpenAI
client = OpenAI(base_url="${esc(o)}/v1", api_key="ppi_...")
r = client.chat.completions.create(model="default",
        messages=[{"role": "user", "content": "hello pool"}])
print(r.choices[0].message.content)</pre>
      <h3>Batch</h3>
      <pre class="code">> POST /api/projects/&lt;slug&gt;/jobs   (Bearer: account key; owner only)
  {"title": "…", "prompt": "…", "model": "…"}     → {"job_id": 7}
> GET  /api/jobs/7                                → status/output
> GET  /api/jobs/7/wait?timeout=120               → long-poll until done</pre>
      <h3>Everything else</h3>
      <pre class="code">POST /api/register                 create account → account key (once)
GET  /api/projects                 marketplace
GET  /api/projects/&lt;slug&gt;          project + donors
POST /api/projects/&lt;slug&gt;/support  become a donor
POST /api/pair                     pairing code for a new node
GET  /v1/models                    models currently online in the pool</pre>
      <p class="small muted">Full node protocol lives in the connector source —
      <code class="inline">connector/node_connector.py</code> — ~300 lines of stdlib Python.</p>
    </div>
  `);
}

// ------------------------------------------------------------------ router

async function route() {
  const h = location.hash || "#/";
  try {
    if (h === "#/" || h === "") await viewHome();
    else if (h.startsWith("#/p/")) await viewProject(h.slice(4));
    else if (h === "#/new") viewNew();
    else if (h === "#/account") await viewAccount();
    else if (h === "#/connect") viewConnect();
    else if (h === "#/docs") viewDocs();
    else await viewHome();
  } catch (e) {
    render(`<div class="panel"><p class="error">${esc(e.message)}</p></div>`);
  }
}
window.addEventListener("hashchange", route);
route();
