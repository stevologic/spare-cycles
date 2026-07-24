/* SpareCycles web UI — vanilla JS hash router, no build step. */

const $app = document.getElementById("app");
let refreshTimer = null;

const key = () => localStorage.getItem("sc_key") || "";
const setKey = (k) => k ? localStorage.setItem("sc_key", k) : localStorage.removeItem("sc_key");

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
const fmtDate = (t) => t
  ? new Date(t * 1000).toLocaleDateString(undefined,
      { year: "numeric", month: "short", day: "numeric" })
  : "—";
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
      <p class="tag">SpareCycles is a marketplace of vibe-coding projects and a pool of donated
      AI compute. Browse projects, point your idle agent at one, and become an
      <b>AI Donor</b> — or bring your project and let the pool carry it.
      <br><a href="#/how">New here? See how it works →</a></p>
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
          <td><a href="#/u/${esc(d.name)}">${esc(d.display_name)}</a>
              ${d.is_maintainer ? '<span class="badge owner">maintainer</span>' : ""}</td>
          <td>${fmtN(d.jobs)}</td><td>${fmtN(d.tokens)}</td></tr>`).join("")}
       </table>`
    : `<p class="muted small">No completed donations yet. Be the first AI Donor.</p>`;

  render(`
    <div class="panel projhead">
      <h2 class="pagetitle">${esc(p.name)}</h2>
      <div class="row">
        <span class="muted">by <a href="#/u/${esc(p.owner_name)}">${esc(p.owner)}</a></span>
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
        <button class="chip" id="chip-supporters" title="See who supports this project">
          <b>${p.supporters}</b> ${p.supporters === 1 ? "supporter" : "supporters"} →
        </button>
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
  -H "Authorization: Bearer sci_..." -H "Content-Type: application/json" \\
  -d '{"messages": [{"role": "user", "content": "hello pool"}]}'</pre>
    </div>

    <div class="panel">
      <h2>Job feed <span class="muted small">(public, refreshes live)</span></h2>
      <div class="joblist" id="joblist">${jobs.jobs.map(jobDetails).join("") || `<p class="muted small">Nothing queued yet.</p>`}</div>
    </div>

    <dialog class="modal" id="dlg-supporters">
      <div class="modal-head">
        <h3>🤝 Supporters of ${esc(p.name)}</h3>
        <span class="grow"></span>
        <button class="btn small ghost" id="dlg-close">Close</button>
      </div>
      <div class="modal-body" id="dlg-body"><p class="muted small">Loading…</p></div>
    </dialog>
  `);

  const dlg = document.getElementById("dlg-supporters");
  document.getElementById("dlg-close").addEventListener("click", () => dlg.close());
  // Close on backdrop click. `e.target === dlg` alone isn't enough: the very
  // click that opened the modal gets retargeted to the dialog if the trigger
  // sits under the modal's box, which would slam it shut instantly. Only
  // treat it as a backdrop hit when the pointer is outside the dialog itself.
  dlg.addEventListener("click", (e) => {
    if (e.target !== dlg) return;
    const r = dlg.getBoundingClientRect();
    const outside = e.clientX < r.left || e.clientX > r.right
                 || e.clientY < r.top || e.clientY > r.bottom;
    if (outside) dlg.close();
  });
  document.getElementById("chip-supporters").addEventListener("click", async () => {
    const body = document.getElementById("dlg-body");
    body.innerHTML = `<p class="muted small">Loading…</p>`;
    dlg.showModal();
    try {
      const r = await api(`/api/projects/${slug}/supporters`);
      body.innerHTML = r.supporters.length ? `
        <table class="donors">
          <tr><th>Supporter</th><th>Nodes</th><th>Jobs</th><th>Tokens</th></tr>
          ${r.supporters.map((s) => `<tr>
            <td><a href="#/u/${esc(s.name)}">${esc(s.display_name)}</a>
              <a class="muted small" href="#/u/${esc(s.name)}">@${esc(s.name)}</a>
              ${s.is_maintainer ? '<span class="badge owner">maintainer</span>' : ""}
              <br><span class="muted small">supporting since ${fmtDate(s.since)}</span></td>
            <td>${s.nodes_online
              ? `<span class="dot on"></span>${s.nodes_online} online`
              : `<span class="dot off"></span><span class="muted">offline</span>`}</td>
            <td>${fmtN(s.jobs)}</td>
            <td>${fmtN(s.tokens)}</td>
          </tr>`).join("")}
        </table>
        <p class="muted small mt">Supporters volunteer their nodes for this project's queue.
        Anyone with jobs above is also an AI Donor on the leaderboard.</p>`
        : `<p class="muted small">No supporters yet — be the first to point a node at this project.</p>`;
    } catch (e) {
      body.innerHTML = `<p class="error">${esc(e.message)}</p>`;
    }
  });

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

// ------------------------------------------------------------------ profile

function projectCard(p, extra = "") {
  return `
    <a class="card" href="#/p/${esc(p.slug)}" style="color:inherit;text-decoration:none">
      <h3>${esc(p.name)}</h3>
      <div class="tagline">${esc(p.tagline || (p.description || "").slice(0, 120)
        || "A vibe-coding project looking for AI donors.")}</div>
      <div class="meta">${extra}</div>
    </a>`;
}

async function viewProfile(name) {
  render(`<p class="muted">Loading profile…</p>`);
  const u = await api(`/api/accounts/${encodeURIComponent(name)}`);
  const apps = u.projects.map((p) => projectCard(p, `
    <span><b>${fmtN(p.jobs_done)}</b> jobs done</span>
    <span><b>${fmtN(p.tokens_donated)}</b> tokens donated</span>
    <span><b>${p.supporters}</b> ${p.supporters === 1 ? "supporter" : "supporters"}</span>
    ${p.jobs_open ? `<span class="badge queued">${p.jobs_open} open</span>` : ""}`)).join("");

  render(`
    <div class="panel">
      <div class="row">
        <h2 class="pagetitle">${esc(u.display_name)}</h2>
        <span class="muted">@${esc(u.name)}</span>
        ${u.nodes_online
          ? `<span class="chip"><span class="dot on"></span>${u.nodes_online} node${u.nodes_online === 1 ? "" : "s"} online</span>`
          : `<span class="chip"><span class="dot off"></span>no nodes online</span>`}
      </div>
      <p class="muted small">Joined ${fmtDate(u.joined)}</p>
      <div class="statrow" style="justify-content:flex-start">
        <span class="chip"><b>${fmtN(u.donated.jobs)}</b> jobs donated</span>
        <span class="chip"><b>${fmtN(u.donated.tokens)}</b> tokens donated</span>
        <span class="chip"><b>${u.donated.projects_helped}</b> ${u.donated.projects_helped === 1 ? "project" : "projects"} helped</span>
        <span class="chip"><b>${u.projects.length}</b> ${u.projects.length === 1 ? "app" : "apps"} submitted</span>
      </div>
    </div>

    <div class="panel">
      <h2>📦 Apps by ${esc(u.display_name)}</h2>
      ${apps
        ? `<div class="grid">${apps}</div>`
        : `<p class="muted small">No projects submitted yet.</p>`}
    </div>

    <div class="panel">
      <h2>🤝 Supporting</h2>
      ${u.supporting.length
        ? u.supporting.map((s) => `<div class="row mt">
            <a href="#/p/${esc(s.slug)}">${esc(s.name)}</a>
            <span class="muted small">${esc(s.tagline || "by " + s.owner)}</span>
          </div>`).join("")
        : `<p class="muted small">Not supporting any other projects yet.</p>`}
      <p class="muted small mt">Supporting means volunteering their nodes to process
      another project's queue with their own tokens.</p>
    </div>
  `);
}

// ------------------------------------------------------------------ new project

// Bundled last-resort list, used only if /api/models/catalog is unreachable.
// The server serves live lists from each provider's models API when it has
// that provider's key in its environment.
const STATIC_MODEL_CHOICES = {
  Claude: ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5", "claude-fable-5"],
  OpenAI: ["gpt-5.1", "gpt-5.1-codex", "gpt-5", "gpt-5-mini", "gpt-5-nano"],
  Grok: ["grok-4.1", "grok-4", "grok-4-fast", "grok-code-fast-1"],
};
let modelCatalog = null;

async function getModelCatalog() {
  if (!modelCatalog) {
    try {
      modelCatalog = await api("/api/models/catalog");
    } catch {
      modelCatalog = { providers: STATIC_MODEL_CHOICES, live: {} };
    }
  }
  return modelCatalog;
}

function modelSelect(id, providers, selected, noneLabel) {
  const groups = Object.entries(providers).map(([vendor, models]) => `
    <optgroup label="${esc(vendor)}">
      ${models.map((m) => `<option value="${esc(m)}" ${m === selected ? "selected" : ""}>${esc(m)}</option>`).join("")}
    </optgroup>`).join("");
  return `<select id="${id}">
    ${noneLabel ? `<option value="" ${!selected ? "selected" : ""}>${noneLabel}</option>` : ""}
    ${groups}
  </select>`;
}

async function viewNew() {
  if (!key()) { location.hash = "#/account"; return; }
  render(`<p class="muted">Loading model catalog…</p>`);
  const cat = await getModelCatalog();
  const liveNote = Object.entries(cat.live || {})
    .map(([p, l]) => `${esc(p)}: ${l ? "live" : "bundled list"}`).join(" · ");
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
        <div><label>Preferred model</label>${modelSelect("f-model", cat.providers, "claude-opus-4-8")}</div>
        <div><label>Fallback model</label>${modelSelect("f-fallback", cat.providers, "", "— none —")}</div>
      </div>
      <p class="muted small">Model catalogs — ${liveNote || "bundled list"}. Providers marked
      "bundled list" use a built-in set; put <code class="inline">ANTHROPIC_API_KEY</code>,
      <code class="inline">OPENAI_API_KEY</code>, or <code class="inline">XAI_API_KEY</code> in the
      server environment to load each provider's live model list (used only to list models,
      never for inference).</p>
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
        <label>Account API key</label><input id="l-key" placeholder="sck_...">
        <div class="mt"><button class="btn ghost" id="btn-login">Sign in</button></div>
        <div id="login-msg"></div>
      </div>
      <div class="panel">
        <h2>Lost your key?</h2>
        <p class="muted small">Trade one of your one-time recovery codes for a fresh API key
        (the old key stops working). No codes left either? Run
        <code class="inline">python connector/node_connector.py --recover</code> on any machine
        with a paired node — it can mint a new key too.</p>
        <div class="formrow">
          <div><label>Username</label><input id="rec-name" placeholder="steph"></div>
          <div><label>Recovery code</label><input id="rec-code" placeholder="SCR-XXXX-XXXX"></div>
        </div>
        <div class="mt"><button class="btn ghost" id="btn-recover">Recover account</button></div>
        <div id="rec-msg"></div>
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
          <b>Recovery codes</b> — each can be traded once for a fresh key if you lose yours:
          <div class="keybox">${(r.recovery_codes || []).map(esc).join("<br>")}</div>
          <a href="#/account" onclick="viewAccount()">Continue →</a></div>`;
      } catch (e) { msg.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
    });
    document.getElementById("btn-login").addEventListener("click", async () => {
      const msg = document.getElementById("login-msg");
      setKey(document.getElementById("l-key").value.trim());
      try { await api("/api/me"); viewAccount(); }
      catch (e) { setKey(""); msg.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
    });
    document.getElementById("btn-recover").addEventListener("click", async () => {
      const msg = document.getElementById("rec-msg");
      try {
        const r = await api("/api/recover", {
          method: "POST",
          body: JSON.stringify({
            name: document.getElementById("rec-name").value,
            recovery_code: document.getElementById("rec-code").value,
          }),
        });
        setKey(r.api_key);
        msg.innerHTML = `
          <div class="notice"><b>Welcome back, @${esc(r.account)} — new API key (shown once):</b>
          <div class="keybox">${esc(r.api_key)}</div>
          Your old key no longer works. ${r.recovery_codes_left} recovery
          ${r.recovery_codes_left === 1 ? "code" : "codes"} left — generate a fresh set from
          your account page if you are running low.
          <a href="#/account" onclick="viewAccount()">Continue →</a></div>`;
      } catch (e) { msg.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
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
        <a class="small" href="#/u/${esc(me.account.name)}">view public profile →</a>
        <span class="grow"></span>
        <button class="btn small ghost" id="btn-logout">Sign out</button></div>
      <div class="row mt">
        <span class="muted small">🔑 ${me.recovery_codes_left ?? 0} unused recovery
        ${me.recovery_codes_left === 1 ? "code" : "codes"} — lose your API key and a code
        (or a paired node's <code class="inline">--recover</code>) gets you back in.</span>
        <button class="btn small ghost" id="btn-codes">Generate new codes</button>
      </div>
      <div id="codes-out"></div>
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
  document.getElementById("btn-codes").addEventListener("click", async () => {
    if (!confirm("Generate a new set? Your previous unused recovery codes stop working.")) return;
    try {
      const r = await api("/api/recovery_codes", { method: "POST" });
      document.getElementById("codes-out").innerHTML = `
        <div class="notice"><b>New recovery codes — shown exactly once:</b>
        <div class="keybox">${r.recovery_codes.map(esc).join("<br>")}</div>
        Store them somewhere safe (password manager, printed note).</div>`;
    } catch (e) { alert(e.message); }
  });
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

// ------------------------------------------------------------------ how it works

function viewHow() {
  render(`
    <div class="panel">
      <h2>How SpareCycles works</h2>
      <p>Some devs have <b>ideas but no tokens</b>. Others have <b>tokens but no ideas</b>.
      SpareCycles sits in the middle: a marketplace of vibe-coding projects and a pool of
      donated AI compute. Prompts go in from project owners; answers come back from
      volunteer <b>donor nodes</b> that run them on their own subscriptions, API keys, or
      fully offline local models (Ollama / LM Studio).</p>

      <h3>The big picture</h3>
      <svg class="diagram" viewBox="0 0 860 330" role="img" aria-label="Architecture diagram">
        <defs>
          <marker id="arrA" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--muted)"/>
          </marker>
        </defs>
        <rect x="20" y="95" width="210" height="140" rx="12" fill="var(--panel)" stroke="var(--line)"/>
        <text x="125" y="125" text-anchor="middle" font-size="15" font-weight="700" fill="var(--ink)">Idea-rich 💡</text>
        <text x="125" y="150" text-anchor="middle" font-size="12.5" fill="var(--ink)">project owners &amp; their tools</text>
        <text x="125" y="170" text-anchor="middle" font-size="11.5" fill="var(--muted)">Hermes · aider · curl · web UI</text>
        <text x="125" y="196" text-anchor="middle" font-size="11.5" fill="var(--muted)">…but out of tokens</text>

        <rect x="325" y="45" width="210" height="240" rx="12" fill="var(--panel)" stroke="var(--accent)"/>
        <text x="430" y="80" text-anchor="middle" font-size="16" font-weight="800" fill="var(--ink)">♻️ SpareCycles</text>
        <rect x="345" y="100" width="170" height="34" rx="8" fill="var(--bg)" stroke="var(--line)"/>
        <text x="430" y="122" text-anchor="middle" font-size="12.5" fill="var(--ink)">Marketplace</text>
        <rect x="345" y="142" width="170" height="34" rx="8" fill="var(--bg)" stroke="var(--line)"/>
        <text x="430" y="164" text-anchor="middle" font-size="12.5" fill="var(--ink)">Job queue</text>
        <rect x="345" y="184" width="170" height="34" rx="8" fill="var(--bg)" stroke="var(--line)"/>
        <text x="430" y="206" text-anchor="middle" font-size="12.5" fill="var(--ink)">AI Donor ledger</text>
        <text x="430" y="245" text-anchor="middle" font-size="11.5" fill="var(--muted)">never sees provider keys</text>
        <text x="430" y="263" text-anchor="middle" font-size="11.5" fill="var(--muted)">one small server (FastAPI + SQLite)</text>

        <rect x="630" y="95" width="210" height="140" rx="12" fill="var(--panel)" stroke="var(--line)"/>
        <text x="735" y="125" text-anchor="middle" font-size="15" font-weight="700" fill="var(--ink)">Token-rich 🔋</text>
        <text x="735" y="150" text-anchor="middle" font-size="12.5" fill="var(--ink)">donor nodes — node_connector.py</text>
        <text x="735" y="170" text-anchor="middle" font-size="11.5" fill="var(--muted)">claude · codex · gemini · grok · API keys</text>
        <text x="735" y="196" text-anchor="middle" font-size="11.5" fill="var(--muted)">ollama · LM Studio (offline models)</text>

        <line x1="230" y1="135" x2="325" y2="135" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#arrA)"/>
        <text x="277" y="126" text-anchor="middle" font-size="11.5" fill="var(--muted)">prompt</text>
        <line x1="325" y1="185" x2="230" y2="185" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#arrA)"/>
        <text x="277" y="203" text-anchor="middle" font-size="11.5" fill="var(--muted)">answer</text>
        <line x1="630" y1="135" x2="535" y2="135" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#arrA)"/>
        <text x="582" y="126" text-anchor="middle" font-size="11.5" fill="var(--muted)">poll &amp; claim</text>
        <line x1="630" y1="185" x2="535" y2="185" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#arrA)"/>
        <text x="582" y="203" text-anchor="middle" font-size="11.5" fill="var(--muted)">post answer</text>

        <text x="430" y="315" text-anchor="middle" font-size="12" fill="var(--muted)">Nodes connect outbound-only — no open ports, works behind home NAT.</text>
      </svg>

      <h3>A realtime request, step by step</h3>
      <p class="small muted">This is what happens when any OpenAI-compatible tool calls the
      pool's <code class="inline">/v1/chat/completions</code> endpoint with a project's
      inference key. Batch jobs ride the same pipeline — they just wait in the queue
      instead of holding the request open.</p>
      <svg class="diagram" viewBox="0 0 860 430" role="img" aria-label="Realtime request sequence diagram">
        <defs>
          <marker id="arrB" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/>
          </marker>
        </defs>
        <rect x="45" y="15" width="190" height="36" rx="9" fill="var(--panel)" stroke="var(--line)"/>
        <text x="140" y="38" text-anchor="middle" font-size="13.5" font-weight="700" fill="var(--ink)">Your tool</text>
        <rect x="335" y="15" width="190" height="36" rx="9" fill="var(--panel)" stroke="var(--accent)"/>
        <text x="430" y="38" text-anchor="middle" font-size="13.5" font-weight="700" fill="var(--ink)">SpareCycles server</text>
        <rect x="625" y="15" width="190" height="36" rx="9" fill="var(--panel)" stroke="var(--line)"/>
        <text x="720" y="38" text-anchor="middle" font-size="13.5" font-weight="700" fill="var(--ink)">Donor node</text>

        <line x1="140" y1="51" x2="140" y2="400" stroke="var(--line)" stroke-dasharray="4 4"/>
        <line x1="430" y1="51" x2="430" y2="400" stroke="var(--line)" stroke-dasharray="4 4"/>
        <line x1="720" y1="51" x2="720" y2="400" stroke="var(--line)" stroke-dasharray="4 4"/>

        <line x1="140" y1="95" x2="430" y2="95" stroke="var(--accent)" stroke-width="1.5" marker-end="url(#arrB)"/>
        <text x="285" y="86" text-anchor="middle" font-size="12" fill="var(--ink)">1 · POST /v1/chat/completions</text>
        <text x="285" y="110" text-anchor="middle" font-size="10.5" fill="var(--muted)">prompt, model, temperature, max_tokens</text>

        <rect x="355" y="128" width="150" height="30" rx="7" fill="var(--bg)" stroke="var(--line)"/>
        <text x="430" y="147" text-anchor="middle" font-size="12" fill="var(--ink)">job queued</text>

        <line x1="720" y1="195" x2="430" y2="195" stroke="var(--accent)" stroke-width="1.5" marker-end="url(#arrB)"/>
        <text x="575" y="186" text-anchor="middle" font-size="12" fill="var(--ink)">2 · long-poll: work for my models?</text>
        <line x1="430" y1="228" x2="720" y2="228" stroke="var(--accent)" stroke-width="1.5" marker-end="url(#arrB)"/>
        <text x="575" y="219" text-anchor="middle" font-size="12" fill="var(--ink)">3 · claim — job payload</text>

        <rect x="610" y="252" width="220" height="48" rx="9" fill="var(--bg)" stroke="var(--line)"/>
        <text x="720" y="272" text-anchor="middle" font-size="12" fill="var(--ink)">4 · run locally with YOUR runner</text>
        <text x="720" y="290" text-anchor="middle" font-size="10.5" fill="var(--muted)">CLI · metered API · ollama / LM Studio</text>

        <line x1="720" y1="335" x2="430" y2="335" stroke="var(--accent)" stroke-width="1.5" marker-end="url(#arrB)"/>
        <text x="575" y="326" text-anchor="middle" font-size="12" fill="var(--ink)">5 · POST answer + token usage</text>
        <line x1="430" y1="375" x2="140" y2="375" stroke="var(--accent)" stroke-width="1.5" marker-end="url(#arrB)"/>
        <text x="285" y="366" text-anchor="middle" font-size="12" fill="var(--ink)">6 · OpenAI-style response</text>

        <text x="430" y="422" text-anchor="middle" font-size="12" fill="var(--muted)">The HTTP call stays open (up to 180 s) while the pool waits — typical answers land in seconds.</text>
      </svg>

      <h3>Becoming an AI Donor</h3>
      <svg class="diagram" viewBox="0 0 860 170" role="img" aria-label="Donor onboarding steps">
        <defs>
          <marker id="arrC" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--muted)"/>
          </marker>
        </defs>
        <rect x="20" y="35" width="148" height="92" rx="11" fill="var(--panel)" stroke="var(--line)"/>
        <text x="94" y="70" text-anchor="middle" font-size="12.5" font-weight="700" fill="var(--ink)">1 · Create account</text>
        <text x="94" y="92" text-anchor="middle" font-size="11" fill="var(--muted)">free, on the web UI</text>
        <line x1="168" y1="81" x2="191" y2="81" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#arrC)"/>

        <rect x="191" y="35" width="148" height="92" rx="11" fill="var(--panel)" stroke="var(--line)"/>
        <text x="265" y="70" text-anchor="middle" font-size="12.5" font-weight="700" fill="var(--ink)">2 · Pairing code</text>
        <text x="265" y="92" text-anchor="middle" font-size="11" fill="var(--muted)">Account → generate</text>
        <line x1="339" y1="81" x2="362" y2="81" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#arrC)"/>

        <rect x="362" y="35" width="148" height="92" rx="11" fill="var(--panel)" stroke="var(--line)"/>
        <text x="436" y="70" text-anchor="middle" font-size="12.5" font-weight="700" fill="var(--ink)">3 · Run connector</text>
        <text x="436" y="92" text-anchor="middle" font-size="11" fill="var(--muted)">one Python command</text>
        <line x1="510" y1="81" x2="533" y2="81" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#arrC)"/>

        <rect x="533" y="35" width="148" height="92" rx="11" fill="var(--panel)" stroke="var(--line)"/>
        <text x="607" y="70" text-anchor="middle" font-size="12.5" font-weight="700" fill="var(--ink)">4 · Support projects</text>
        <text x="607" y="92" text-anchor="middle" font-size="11" fill="var(--muted)">pick from marketplace</text>
        <line x1="681" y1="81" x2="704" y2="81" stroke="var(--muted)" stroke-width="1.5" marker-end="url(#arrC)"/>

        <rect x="704" y="35" width="148" height="92" rx="11" fill="var(--accent)"/>
        <text x="778" y="70" text-anchor="middle" font-size="12.5" font-weight="700" fill="var(--accent-ink)">5 · AI Donor 🏆</text>
        <text x="778" y="92" text-anchor="middle" font-size="11" fill="var(--accent-ink)">credited on every job</text>
      </svg>
      <p class="small muted">Your node only ever serves projects you explicitly support (plus
      your own). Every completed job credits you on that project's donor leaderboard.</p>

      <h3>Trust &amp; privacy, in one breath</h3>
      <ul class="small">
        <li><b>Keys never leave the node.</b> The connector sends prompts and answers — never
        credentials. The server stores only hashed tokens it issued itself.</li>
        <li><b>Everything is public by default.</b> Job prompts and outputs appear on the
        project page. Sunlight is the anti-abuse mechanism — never submit secrets.</li>
        <li><b>CLI runners execute in an empty scratch directory</b> with agentic turns capped;
        API and local-model runners have no tool access at all.</li>
        <li><b>Offline models close the loop.</b> Donating via Ollama / LM Studio is literal
        compute donation — your GPU, your electricity, zero provider-ToS questions.</li>
        <li><b>Donation-only.</b> No money moves, tokens are never sold.</li>
      </ul>

      <h3>Glossary</h3>
      <table class="donors">
        <tr><th>Term</th><th>Meaning</th></tr>
        <tr><td><b>Project</b></td><td>A vibe-coding repo on the marketplace with a queue and a realtime endpoint.</td></tr>
        <tr><td><b>Inference key</b></td><td><code class="inline">sci_…</code> — Bearer token for a project's <code class="inline">/v1/chat/completions</code>. Shown once at creation.</td></tr>
        <tr><td><b>Job</b></td><td>One prompt → one answer. Kind is <i>realtime</i> (caller waits) or <i>batch</i> (queued).</td></tr>
        <tr><td><b>Node</b></td><td>A machine running <code class="inline">node_connector.py</code>, tied to your account.</td></tr>
        <tr><td><b>Runner</b></td><td>How a node executes a job: an AI CLI, a metered provider API, or a local model server (Ollama / LM Studio).</td></tr>
        <tr><td><b>Pairing code</b></td><td>Short-lived code that binds a new node to your account — your account key never touches the node.</td></tr>
        <tr><td><b>Supporting</b></td><td>Opting your nodes into serving a project's queue.</td></tr>
        <tr><td><b>AI Donor</b></td><td>Anyone whose node completed jobs for a project — ranked on its leaderboard.</td></tr>
      </table>

      <p class="mt small">Ready to go deeper? <a href="#/connect">Connect a node</a> ·
      <a href="#/docs">API docs</a> · <a href="#/">browse the marketplace</a></p>
    </div>
  `);
}

// ------------------------------------------------------------------ docs pages

function viewConnect() {
  render(`
    <div class="panel">
      <h2>Connect a node</h2>
      <p>A node is any machine with an AI CLI (<code class="inline">claude</code>,
      <code class="inline">codex</code>, <code class="inline">gemini</code>,
      <code class="inline">grok</code>, <code class="inline">cursor-agent</code>), a
      metered API key in an environment variable
      (<code class="inline">ANTHROPIC_API_KEY</code>, <code class="inline">OPENAI_API_KEY</code>,
      <code class="inline">XAI_API_KEY</code>), or a <b>local model server</b> —
      Ollama (<code class="inline">:11434</code>) or LM Studio
      (<code class="inline">:1234</code>) are auto-detected, and your installed models are
      advertised to the pool by name for fully offline donation.
      The connector is one stdlib-only Python script — no pip installs.</p>
      <h3>1. Get a pairing code</h3>
      <p class="small muted">Sign in → <a href="#/account">Account</a> → <i>Generate pairing code</i>.</p>
      <h3>2. Run the connector on the node machine</h3>
      <pre class="code">python connector/node_connector.py --server ${esc(location.origin)} --code XXXX-XXXX</pre>
      <p class="small muted">It auto-detects available runners, registers the node under your
      account, saves its node token in <code class="inline">~/.sparecycles/</code>, and starts
      polling for work from projects you support.</p>
      <h3>3. Run it in the background — and check on it</h3>
      <p class="small muted">Once it is polling you'll want it out of the way. Start it
      detached, then use <code class="inline">--status</code> at any time to see which
      runners this machine can serve, what the pool thinks your node is doing, and its
      last few jobs. It is read-only, so it is safe to run while the background
      connector keeps polling.</p>
      <pre class="code"># see the runners this machine serves + live pool status
python connector/node_connector.py --status

# ── run detached ──────────────────────────────────────────────
# macOS / Linux
nohup python connector/node_connector.py > ~/.sparecycles/node.log 2>&1 &

# Windows PowerShell
Start-Process -WindowStyle Hidden python -ArgumentList "connector/node_connector.py"

# ── watch what it is doing ────────────────────────────────────
tail -f ~/.sparecycles/node.log                  # macOS / Linux
Get-Content "$env:USERPROFILE\.sparecycles\node.log" -Wait   # Windows

# ── is it still running? ──────────────────────────────────────
pgrep -af node_connector.py                      # macOS / Linux
Get-Process python | Where-Object Path           # Windows

# ── stop it ───────────────────────────────────────────────────
pkill -f node_connector.py                       # macOS / Linux
Stop-Process -Name python                        # Windows (careful!)</pre>
      <p class="small muted">Prefer it always-on? Drop the same command into a systemd unit
      (<code class="inline">Restart=always</code>), a launchd plist, or Task Scheduler —
      the connector reconnects on its own after a network blip or a server restart.</p>

      <h3>4. Support projects</h3>
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
      <b>project inference key</b> (<code class="inline">sci_…</code>). Model, temperature and
      max_tokens default to the project's settings; requests wait up to 180s for a donor node.</p>
      <pre class="code">curl ${esc(o)}/v1/chat/completions \\
  -H "Authorization: Bearer sci_..." -H "Content-Type: application/json" \\
  -d '{"model": "claude-sonnet-4-5", "stream": false,
       "messages": [{"role": "user", "content": "Refactor plan for miner.py?"}]}'</pre>
      <pre class="code"># python
from openai import OpenAI
client = OpenAI(base_url="${esc(o)}/v1", api_key="sci_...")
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
GET  /api/projects/&lt;slug&gt;/supporters  who volunteers for this queue
GET  /api/accounts/&lt;name&gt;          public profile: apps + donations
GET  /api/nodes/me                 (node token) node self-status
POST /api/projects/&lt;slug&gt;/support  become a donor
POST /api/pair                     pairing code for a new node
POST /api/recover                  {name, recovery_code} → fresh account key
POST /api/recover/node             (node token) → fresh account key
POST /api/recovery_codes           (account key) → new one-time code set
GET  /api/models/catalog           model lists from provider APIs (cached)
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
    else if (h.startsWith("#/u/")) await viewProfile(decodeURIComponent(h.slice(4)));
    else if (h === "#/new") await viewNew();
    else if (h === "#/account") await viewAccount();
    else if (h === "#/how") viewHow();
    else if (h === "#/connect") viewConnect();
    else if (h === "#/docs") viewDocs();
    else await viewHome();
  } catch (e) {
    render(`<div class="panel"><p class="error">${esc(e.message)}</p></div>`);
  }
}
window.addEventListener("hashchange", route);
route();
