"""PromptPool server: marketplace API, node queue, OpenAI-compatible proxy.

Run:  uvicorn server.main:app --port 8377
"""

import asyncio
import json
import os
import re
import secrets
import time
from fnmatch import fnmatch

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
NODE_ONLINE_WINDOW = 90  # seconds since last poll to count a node as online

app = FastAPI(title="PromptPool")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)
db.init()


# ---------------------------------------------------------------- helpers

def bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    return auth[7:].strip()


def require_account(conn, request: Request):
    row = conn.execute(
        "SELECT * FROM accounts WHERE key_hash=?", (db.hash_key(bearer(request)),)
    ).fetchone()
    if not row:
        raise HTTPException(401, "invalid account key")
    return row


def require_node(conn, request: Request):
    row = conn.execute(
        "SELECT * FROM nodes WHERE token_hash=?", (db.hash_key(bearer(request)),)
    ).fetchone()
    if not row:
        raise HTTPException(401, "invalid node token")
    return row


def require_project_by_inference_key(conn, request: Request):
    row = conn.execute(
        "SELECT * FROM projects WHERE inference_key_hash=?",
        (db.hash_key(bearer(request)),),
    ).fetchone()
    if not row:
        raise HTTPException(401, "invalid project inference key")
    return row


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40]


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def job_public(row, donor=None) -> dict:
    d = {
        k: row[k]
        for k in (
            "id", "project_id", "kind", "title", "prompt", "model",
            "fallback_model", "status", "output", "error", "runner",
            "model_used", "tokens_in", "tokens_out", "created_at", "finished_at",
        )
    }
    d["donor"] = donor
    return d


def project_public(row, extra=None) -> dict:
    d = {
        k: row[k]
        for k in (
            "id", "slug", "name", "tagline", "description", "repo_url",
            "model", "fallback_model", "temperature", "max_tokens", "created_at",
        )
    }
    if extra:
        d.update(extra)
    return d


def project_stats(conn, pid: int) -> dict:
    r = conn.execute(
        """SELECT
             SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS jobs_done,
             SUM(CASE WHEN status IN ('queued','running') THEN 1 ELSE 0 END) AS jobs_open,
             SUM(CASE WHEN status='done' THEN tokens_in+tokens_out ELSE 0 END) AS tokens
           FROM jobs WHERE project_id=?""",
        (pid,),
    ).fetchone()
    supporters = conn.execute(
        "SELECT COUNT(*) FROM supports WHERE project_id=?", (pid,)
    ).fetchone()[0]
    return {
        "jobs_done": r["jobs_done"] or 0,
        "jobs_open": r["jobs_open"] or 0,
        "tokens_donated": r["tokens"] or 0,
        "supporters": supporters,
    }


def project_donors(conn, pid: int, owner_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT a.id, a.name, a.display_name,
                  COUNT(*) AS jobs, SUM(j.tokens_in + j.tokens_out) AS tokens
           FROM jobs j
           JOIN nodes n ON n.id = j.node_id
           JOIN accounts a ON a.id = n.account_id
           WHERE j.project_id=? AND j.status='done'
           GROUP BY a.id ORDER BY tokens DESC""",
        (pid,),
    ).fetchall()
    return [
        {
            "name": r["name"],
            "display_name": r["display_name"],
            "jobs": r["jobs"],
            "tokens": r["tokens"] or 0,
            "is_maintainer": r["id"] == owner_id,
        }
        for r in rows
    ]


# ---------------------------------------------------------------- job claiming

def match_model(job, node_models: list[str]) -> str | None:
    """Return the model the node should use for this job, or None if no match."""
    for candidate in (job["model"], job["fallback_model"]):
        if not candidate:
            continue
        for pattern in node_models:
            if fnmatch(candidate.lower(), pattern.lower()):
                return candidate
    return None


def try_claim(node, node_models: list[str]) -> dict | None:
    """Atomically claim the best queued job this node is allowed to serve."""
    with db.connect() as conn:
        db.requeue_expired(conn)
        conn.execute(
            "UPDATE nodes SET last_seen=? WHERE id=?", (db.now(), node["id"])
        )
        candidates = conn.execute(
            """SELECT j.* FROM jobs j JOIN projects p ON p.id=j.project_id
               WHERE j.status='queued'
                 AND (p.owner_id=:acct OR EXISTS(
                      SELECT 1 FROM supports s
                      WHERE s.project_id=p.id AND s.account_id=:acct))
               ORDER BY (j.kind='realtime') DESC, j.created_at ASC LIMIT 50""",
            {"acct": node["account_id"]},
        ).fetchall()
        for job in candidates:
            use_model = match_model(job, node_models)
            if not use_model:
                continue
            lease = db.LEASE_REALTIME if job["kind"] == "realtime" else db.LEASE_BATCH
            t = db.now()
            cur = conn.execute(
                """UPDATE jobs SET status='running', node_id=?, use_model=?,
                   attempts=attempts+1, claimed_at=?, deadline=?
                   WHERE id=? AND status='queued'""",
                (node["id"], use_model, t, t + lease, job["id"]),
            )
            if cur.rowcount == 1:
                return {
                    "id": job["id"],
                    "kind": job["kind"],
                    "title": job["title"],
                    "prompt": job["prompt"],
                    "model": use_model,
                    "temperature": job["temperature"],
                    "max_tokens": job["max_tokens"],
                    "lease_seconds": lease,
                }
    return None


def create_job(conn, project, body: dict, kind: str) -> int:
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt is required")
    model = (body.get("model") or project["model"]).strip()
    fallback = body.get("fallback_model")
    fallback = project["fallback_model"] if fallback is None else fallback
    temp = body.get("temperature", project["temperature"])
    max_tok = body.get("max_tokens", project["max_tokens"])
    cur = conn.execute(
        """INSERT INTO jobs(project_id, kind, title, prompt, model, fallback_model,
                            temperature, max_tokens, created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            project["id"], kind, (body.get("title") or "")[:120], prompt, model,
            fallback or "", temp, max_tok, db.now(),
        ),
    )
    return cur.lastrowid


# ---------------------------------------------------------------- accounts

@app.post("/api/register")
async def register(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,29}", name):
        raise HTTPException(400, "name must be 3-30 chars: a-z, 0-9, dashes")
    display = (body.get("display_name") or name).strip()[:60]
    key, key_hash = db.new_key("ppk")
    with db.connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO accounts(name, display_name, key_hash, created_at) "
                "VALUES(?,?,?,?)",
                (name, display, key_hash, db.now()),
            )
        except Exception:
            raise HTTPException(409, "that account name is taken")
        return {
            "account": {"id": cur.lastrowid, "name": name, "display_name": display},
            "api_key": key,
            "note": "Save this key now - it is hashed server-side and never shown again.",
        }


@app.get("/api/me")
async def me(request: Request):
    with db.connect() as conn:
        acct = require_account(conn, request)
        db.requeue_expired(conn)
        nodes = conn.execute(
            "SELECT id, name, runners, models, last_seen, jobs_done FROM nodes "
            "WHERE account_id=? ORDER BY id", (acct["id"],)
        ).fetchall()
        projects = conn.execute(
            "SELECT * FROM projects WHERE owner_id=? ORDER BY id", (acct["id"],)
        ).fetchall()
        supports = conn.execute(
            "SELECT p.slug, p.name FROM supports s JOIN projects p "
            "ON p.id=s.project_id WHERE s.account_id=?", (acct["id"],)
        ).fetchall()
        t = db.now()
        return {
            "account": {"name": acct["name"], "display_name": acct["display_name"]},
            "nodes": [
                {
                    "id": n["id"], "name": n["name"],
                    "runners": json.loads(n["runners"]),
                    "models": json.loads(n["models"]),
                    "online": bool(n["last_seen"] and t - n["last_seen"] < NODE_ONLINE_WINDOW),
                    "jobs_done": n["jobs_done"],
                }
                for n in nodes
            ],
            "projects": [project_public(p) for p in projects],
            "supports": [dict(s) for s in supports],
        }


@app.get("/api/stats")
async def stats():
    with db.connect() as conn:
        db.requeue_expired(conn)
        t = db.now()
        return {
            "projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "accounts": conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
            "nodes_online": conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE last_seen > ?",
                (t - NODE_ONLINE_WINDOW,),
            ).fetchone()[0],
            "jobs_done": conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='done'"
            ).fetchone()[0],
            "tokens_donated": conn.execute(
                "SELECT COALESCE(SUM(tokens_in+tokens_out),0) FROM jobs "
                "WHERE status='done'"
            ).fetchone()[0],
        }


# ---------------------------------------------------------------- projects

@app.post("/api/projects")
async def create_project(request: Request):
    body = await request.json()
    with db.connect() as conn:
        acct = require_account(conn, request)
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        model = (body.get("model") or "").strip()
        if not model:
            raise HTTPException(400, "model preference is required")
        slug = slugify(body.get("slug") or name)
        if not slug:
            raise HTTPException(400, "could not derive a slug from that name")
        key, key_hash = db.new_key("ppi")
        try:
            cur = conn.execute(
                """INSERT INTO projects(owner_id, slug, name, tagline, description,
                     repo_url, model, fallback_model, temperature, max_tokens,
                     inference_key_hash, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    acct["id"], slug, name[:80],
                    (body.get("tagline") or "")[:140],
                    (body.get("description") or "")[:4000],
                    (body.get("repo_url") or "")[:300],
                    model, (body.get("fallback_model") or "").strip(),
                    body.get("temperature"), body.get("max_tokens"),
                    key_hash, db.now(),
                ),
            )
        except Exception:
            raise HTTPException(409, f"slug '{slug}' is taken")
        # Owners implicitly support their own projects; make it visible too.
        conn.execute(
            "INSERT OR IGNORE INTO supports(account_id, project_id, created_at) "
            "VALUES(?,?,?)", (acct["id"], cur.lastrowid, db.now()),
        )
        return {
            "slug": slug,
            "inference_key": key,
            "note": "Save the inference key now - it is the Bearer token for "
                    "/v1/chat/completions and is never shown again.",
        }


@app.get("/api/projects")
async def list_projects():
    with db.connect() as conn:
        db.requeue_expired(conn)
        rows = conn.execute(
            "SELECT p.*, a.display_name AS owner_name FROM projects p "
            "JOIN accounts a ON a.id=p.owner_id ORDER BY p.created_at DESC"
        ).fetchall()
        out = []
        for r in rows:
            out.append(project_public(r, {"owner": r["owner_name"],
                                          **project_stats(conn, r["id"])}))
        out.sort(key=lambda p: (-p["jobs_done"], -p["supporters"]))
        return {"projects": out}


@app.get("/api/projects/{slug}")
async def get_project(slug: str):
    with db.connect() as conn:
        db.requeue_expired(conn)
        p = conn.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
        if not p:
            raise HTTPException(404, "no such project")
        owner = conn.execute(
            "SELECT name, display_name FROM accounts WHERE id=?", (p["owner_id"],)
        ).fetchone()
        return project_public(p, {
            "owner": owner["display_name"],
            "owner_name": owner["name"],
            **project_stats(conn, p["id"]),
            "donors": project_donors(conn, p["id"], p["owner_id"]),
        })


@app.get("/api/projects/{slug}/jobs")
async def project_jobs(slug: str, limit: int = 25):
    with db.connect() as conn:
        db.requeue_expired(conn)
        p = conn.execute("SELECT id FROM projects WHERE slug=?", (slug,)).fetchone()
        if not p:
            raise HTTPException(404, "no such project")
        rows = conn.execute(
            """SELECT j.*, a.display_name AS donor FROM jobs j
               LEFT JOIN nodes n ON n.id=j.node_id
               LEFT JOIN accounts a ON a.id=n.account_id
               WHERE j.project_id=? ORDER BY j.id DESC LIMIT ?""",
            (p["id"], min(max(limit, 1), 100)),
        ).fetchall()
        return {"jobs": [job_public(r, r["donor"]) for r in rows]}


@app.post("/api/projects/{slug}/support")
async def support(slug: str, request: Request):
    with db.connect() as conn:
        acct = require_account(conn, request)
        p = conn.execute("SELECT id FROM projects WHERE slug=?", (slug,)).fetchone()
        if not p:
            raise HTTPException(404, "no such project")
        conn.execute(
            "INSERT OR IGNORE INTO supports(account_id, project_id, created_at) "
            "VALUES(?,?,?)", (acct["id"], p["id"], db.now()),
        )
        return {"ok": True, "supporting": slug}


@app.delete("/api/projects/{slug}/support")
async def unsupport(slug: str, request: Request):
    with db.connect() as conn:
        acct = require_account(conn, request)
        p = conn.execute("SELECT id FROM projects WHERE slug=?", (slug,)).fetchone()
        if not p:
            raise HTTPException(404, "no such project")
        conn.execute(
            "DELETE FROM supports WHERE account_id=? AND project_id=?",
            (acct["id"], p["id"]),
        )
        return {"ok": True}


@app.post("/api/projects/{slug}/jobs")
async def queue_job(slug: str, request: Request):
    body = await request.json()
    with db.connect() as conn:
        acct = require_account(conn, request)
        p = conn.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
        if not p:
            raise HTTPException(404, "no such project")
        if p["owner_id"] != acct["id"]:
            raise HTTPException(403, "only the project owner can queue work")
        jid = create_job(conn, p, body, kind="batch")
        return {"job_id": jid, "status": "queued"}


# ---------------------------------------------------------------- jobs

@app.get("/api/jobs/{job_id}")
async def get_job(job_id: int):
    with db.connect() as conn:
        db.requeue_expired(conn)
        r = conn.execute(
            """SELECT j.*, a.display_name AS donor FROM jobs j
               LEFT JOIN nodes n ON n.id=j.node_id
               LEFT JOIN accounts a ON a.id=n.account_id WHERE j.id=?""",
            (job_id,),
        ).fetchone()
        if not r:
            raise HTTPException(404, "no such job")
        return job_public(r, r["donor"])


@app.get("/api/jobs/{job_id}/wait")
async def wait_job(job_id: int, timeout: float = 120):
    deadline = time.time() + min(max(timeout, 1), 570)
    while time.time() < deadline:
        with db.connect() as conn:
            db.requeue_expired(conn)
            r = conn.execute(
                """SELECT j.*, a.display_name AS donor FROM jobs j
                   LEFT JOIN nodes n ON n.id=j.node_id
                   LEFT JOIN accounts a ON a.id=n.account_id WHERE j.id=?""",
                (job_id,),
            ).fetchone()
            if not r:
                raise HTTPException(404, "no such job")
            if r["status"] in ("done", "failed", "expired"):
                return job_public(r, r["donor"])
        await asyncio.sleep(0.5)
    raise HTTPException(504, "job still pending")


# ---------------------------------------------------------------- nodes

@app.post("/api/pair")
async def make_pair_code(request: Request):
    with db.connect() as conn:
        acct = require_account(conn, request)
        conn.execute("DELETE FROM pair_codes WHERE expires_at < ?", (db.now(),))
        code = "-".join(secrets.token_hex(2).upper() for _ in range(2))
        conn.execute(
            "INSERT INTO pair_codes(code, account_id, expires_at) VALUES(?,?,?)",
            (code, acct["id"], db.now() + 900),
        )
        return {"code": code, "expires_in": 900}


@app.post("/api/nodes/register")
async def register_node(request: Request):
    body = await request.json()
    code = (body.get("code") or "").strip().upper()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM pair_codes WHERE code=? AND expires_at > ?",
            (code, db.now()),
        ).fetchone()
        if not row:
            raise HTTPException(401, "invalid or expired pairing code")
        conn.execute("DELETE FROM pair_codes WHERE code=?", (code,))
        token, token_hash = db.new_key("ppn")
        cur = conn.execute(
            "INSERT INTO nodes(account_id, name, token_hash, runners, models, "
            "created_at) VALUES(?,?,?,?,?,?)",
            (
                row["account_id"], (body.get("name") or "node")[:60], token_hash,
                json.dumps(body.get("runners") or []),
                json.dumps(body.get("models") or []),
                db.now(),
            ),
        )
        acct = conn.execute(
            "SELECT name FROM accounts WHERE id=?", (row["account_id"],)
        ).fetchone()
        return {
            "node_id": cur.lastrowid,
            "node_token": token,
            "account": acct["name"],
        }


@app.post("/api/nodes/poll")
async def node_poll(request: Request):
    body = await request.json()
    with db.connect() as conn:
        node = require_node(conn, request)
        models = body.get("models") or json.loads(node["models"])
        runners = body.get("runners")
        if runners is not None or body.get("models") is not None:
            conn.execute(
                "UPDATE nodes SET runners=COALESCE(?,runners), "
                "models=COALESCE(?,models) WHERE id=?",
                (
                    json.dumps(runners) if runners is not None else None,
                    json.dumps(body.get("models")) if body.get("models") is not None else None,
                    node["id"],
                ),
            )
    node_d = dict(node)
    wait = min(float(body.get("wait", 20)), 25)
    deadline = time.time() + wait
    while True:
        job = try_claim(node_d, models)
        if job or time.time() >= deadline:
            return {"job": job}
        await asyncio.sleep(1.0)


def _node_job(conn, request: Request, job_id: int):
    node = require_node(conn, request)
    job = conn.execute(
        "SELECT * FROM jobs WHERE id=? AND node_id=? AND status='running'",
        (job_id, node["id"]),
    ).fetchone()
    if not job:
        raise HTTPException(409, "job is not running on this node (lease expired?)")
    return node, job


@app.post("/api/nodes/jobs/{job_id}/complete")
async def complete_job(job_id: int, request: Request):
    body = await request.json()
    with db.connect() as conn:
        node, job = _node_job(conn, request, job_id)
        output = body.get("output") or ""
        conn.execute(
            """UPDATE jobs SET status='done', output=?, runner=?, model_used=?,
               tokens_in=?, tokens_out=?, finished_at=? WHERE id=?""",
            (
                output, body.get("runner"), body.get("model_used") or job["use_model"],
                int(body.get("tokens_in") or estimate_tokens(job["prompt"])),
                int(body.get("tokens_out") or estimate_tokens(output)),
                db.now(), job_id,
            ),
        )
        conn.execute(
            "UPDATE nodes SET jobs_done=jobs_done+1, last_seen=? WHERE id=?",
            (db.now(), node["id"]),
        )
        return {"ok": True}


@app.post("/api/nodes/jobs/{job_id}/fail")
async def fail_job(job_id: int, request: Request):
    body = await request.json()
    with db.connect() as conn:
        node, job = _node_job(conn, request, job_id)
        # Give other nodes a chance until attempts run out.
        if job["attempts"] >= db.MAX_ATTEMPTS:
            conn.execute(
                "UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
                ((body.get("error") or "node failed")[:2000], db.now(), job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET status='queued', node_id=NULL, use_model=NULL, "
                "claimed_at=NULL, deadline=NULL, error=? WHERE id=?",
                ((body.get("error") or "node failed")[:2000], job_id),
            )
        conn.execute(
            "UPDATE nodes SET last_seen=? WHERE id=?", (db.now(), node["id"])
        )
        return {"ok": True}


# ------------------------------------------- OpenAI-compatible realtime proxy

def messages_to_prompt(messages: list) -> str:
    parts = []
    for m in messages or []:
        content = m.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                p.get("text", "") for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        parts.append((m.get("role", "user"), str(content)))
    if len(parts) == 1 and parts[0][0] == "user":
        return parts[0][1]
    labels = {"system": "System", "user": "User", "assistant": "Assistant"}
    text = "\n\n".join(f"{labels.get(r, r.title())}: {c}" for r, c in parts)
    return text + "\n\nAssistant:"


def openai_error(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "invalid_request_error", "code": code}},
        status_code=status,
    )


@app.get("/v1/models")
async def list_models():
    with db.connect() as conn:
        t = db.now()
        rows = conn.execute(
            "SELECT models FROM nodes WHERE last_seen > ?",
            (t - NODE_ONLINE_WINDOW,),
        ).fetchall()
    seen = sorted({m for r in rows for m in json.loads(r["models"])})
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "owned_by": "promptpool"} for m in seen
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:
        return openai_error(400, "body must be JSON", "invalid_json")
    with db.connect() as conn:
        try:
            project = require_project_by_inference_key(conn, request)
        except HTTPException as e:
            return openai_error(e.status_code, e.detail, "invalid_api_key")
        prompt = messages_to_prompt(body.get("messages"))
        if not prompt.strip():
            return openai_error(400, "messages are required", "missing_messages")
        job_body = {
            "prompt": prompt,
            "model": body.get("model") or None,
            "temperature": body.get("temperature", project["temperature"]),
            "max_tokens": body.get("max_tokens", project["max_tokens"]),
            "title": "realtime via /v1/chat/completions",
        }
        if job_body["model"] in (None, "", "default"):
            job_body["model"] = project["model"]
        jid = create_job(conn, project, job_body, kind="realtime")

    timeout = min(float(request.query_params.get("timeout", 180)), 570)
    deadline = time.time() + timeout
    row = None
    while time.time() < deadline:
        with db.connect() as conn:
            db.requeue_expired(conn)
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            if row["status"] in ("done", "failed", "expired"):
                break
        await asyncio.sleep(0.4)

    if not row or row["status"] not in ("done", "failed", "expired"):
        return openai_error(
            504,
            f"no donor node answered within {int(timeout)}s "
            "(job stays queued; poll /api/jobs/%d)" % jid,
            "pool_timeout",
        )
    if row["status"] != "done":
        return openai_error(502, row["error"] or "job failed", "pool_job_failed")

    created = int(row["finished_at"] or time.time())
    model = row["model_used"] or row["use_model"] or row["model"]
    usage = {
        "prompt_tokens": row["tokens_in"],
        "completion_tokens": row["tokens_out"],
        "total_tokens": row["tokens_in"] + row["tokens_out"],
    }
    if body.get("stream"):
        def sse():
            base = {
                "id": f"chatcmpl-pp{jid}", "object": "chat.completion.chunk",
                "created": created, "model": model,
            }
            first = dict(base, choices=[{"index": 0, "delta": {"role": "assistant"},
                                         "finish_reason": None}])
            yield f"data: {json.dumps(first)}\n\n"
            text = row["output"] or ""
            for i in range(0, len(text), 200):
                chunk = dict(base, choices=[{"index": 0,
                                             "delta": {"content": text[i:i+200]},
                                             "finish_reason": None}])
                yield f"data: {json.dumps(chunk)}\n\n"
            last = dict(base, choices=[{"index": 0, "delta": {},
                                        "finish_reason": "stop"}])
            yield f"data: {json.dumps(last)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(sse(), media_type="text/event-stream")

    return {
        "id": f"chatcmpl-pp{jid}",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": row["output"] or ""},
            "finish_reason": "stop",
        }],
        "usage": usage,
    }


# ---------------------------------------------------------------- web ui

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))
