"""SpareCycles server: marketplace API, node queue, OpenAI-compatible proxy.

Run:  uvicorn server.main:app --port 8377
"""

import asyncio
import json
import os
import re
import secrets
import time
import urllib.request
from fnmatch import fnmatch

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from . import db

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
NODE_ONLINE_WINDOW = 90  # seconds since last poll to count a node as online

app = FastAPI(title="SpareCycles")
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

RECOVERY_CODE_COUNT = 5


def mint_recovery_codes(conn, account_id: int) -> list[str]:
    """Replace any unused recovery codes with a fresh one-time-use set."""
    conn.execute(
        "DELETE FROM recovery_codes WHERE account_id=? AND used_at IS NULL",
        (account_id,),
    )
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        code = "SCR-" + "-".join(secrets.token_hex(2).upper() for _ in range(2))
        conn.execute(
            "INSERT INTO recovery_codes(account_id, code_hash, created_at) "
            "VALUES(?,?,?)",
            (account_id, db.hash_key(code), db.now()),
        )
        codes.append(code)
    return codes


def rotate_account_key(conn, account_id: int) -> str:
    """Issue a new account API key; the old one stops working immediately."""
    key, key_hash = db.new_key("sck")
    conn.execute(
        "UPDATE accounts SET key_hash=? WHERE id=?", (key_hash, account_id)
    )
    return key


@app.post("/api/register")
async def register(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,29}", name):
        raise HTTPException(400, "name must be 3-30 chars: a-z, 0-9, dashes")
    display = (body.get("display_name") or name).strip()[:60]
    key, key_hash = db.new_key("sck")
    with db.connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO accounts(name, display_name, key_hash, created_at) "
                "VALUES(?,?,?,?)",
                (name, display, key_hash, db.now()),
            )
        except Exception:
            raise HTTPException(409, "that account name is taken")
        codes = mint_recovery_codes(conn, cur.lastrowid)
        return {
            "account": {"id": cur.lastrowid, "name": name, "display_name": display},
            "api_key": key,
            "recovery_codes": codes,
            "note": "Save the key AND the recovery codes now - both are hashed "
                    "server-side and never shown again. Each recovery code can "
                    "be traded once for a fresh API key if you lose yours.",
        }


@app.post("/api/recover")
async def recover_with_code(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip().lower()
    code = (body.get("recovery_code") or "").strip().upper()
    fail = HTTPException(401, "invalid username or recovery code")
    if not name or not code:
        raise fail
    with db.connect() as conn:
        acct = conn.execute(
            "SELECT * FROM accounts WHERE name=?", (name,)
        ).fetchone()
        if not acct:
            raise fail
        row = conn.execute(
            "SELECT * FROM recovery_codes WHERE account_id=? AND code_hash=? "
            "AND used_at IS NULL",
            (acct["id"], db.hash_key(code)),
        ).fetchone()
        if not row:
            raise fail
        conn.execute(
            "UPDATE recovery_codes SET used_at=? WHERE id=?", (db.now(), row["id"])
        )
        new_api_key = rotate_account_key(conn, acct["id"])
        left = conn.execute(
            "SELECT COUNT(*) FROM recovery_codes WHERE account_id=? "
            "AND used_at IS NULL", (acct["id"],),
        ).fetchone()[0]
        return {
            "account": acct["name"],
            "api_key": new_api_key,
            "recovery_codes_left": left,
            "note": "Old API key is now invalid. Save the new one - and "
                    "generate fresh recovery codes if you are running low.",
        }


@app.post("/api/recover/node")
async def recover_with_node(request: Request):
    """A paired node's token proves account ownership - it can mint a new
    account key (run `node_connector.py --recover` on the node machine)."""
    with db.connect() as conn:
        node = require_node(conn, request)
        acct = conn.execute(
            "SELECT * FROM accounts WHERE id=?", (node["account_id"],)
        ).fetchone()
        new_api_key = rotate_account_key(conn, acct["id"])
        return {
            "account": acct["name"],
            "api_key": new_api_key,
            "note": "Old API key is now invalid. Save the new one.",
        }


@app.post("/api/recovery_codes")
async def regenerate_recovery_codes(request: Request):
    with db.connect() as conn:
        acct = require_account(conn, request)
        codes = mint_recovery_codes(conn, acct["id"])
        return {
            "recovery_codes": codes,
            "note": "Previous unused codes are now invalid. Save these once.",
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
        codes_left = conn.execute(
            "SELECT COUNT(*) FROM recovery_codes WHERE account_id=? "
            "AND used_at IS NULL", (acct["id"],),
        ).fetchone()[0]
        t = db.now()
        return {
            "account": {"name": acct["name"], "display_name": acct["display_name"]},
            "recovery_codes_left": codes_left,
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


@app.get("/api/accounts/{name}")
async def public_profile(name: str):
    """Public profile: what someone has built and what they've carried.
    Deliberately excludes node names/tokens — only counts are public."""
    with db.connect() as conn:
        db.requeue_expired(conn)
        acct = conn.execute(
            "SELECT * FROM accounts WHERE name=?", (name.strip().lower(),)
        ).fetchone()
        if not acct:
            raise HTTPException(404, "no such account")

        owned = conn.execute(
            "SELECT * FROM projects WHERE owner_id=? ORDER BY created_at DESC",
            (acct["id"],),
        ).fetchall()
        supported = conn.execute(
            """SELECT p.slug, p.name, p.tagline, a.display_name AS owner
                 FROM supports s
                 JOIN projects p ON p.id = s.project_id
                 JOIN accounts a ON a.id = p.owner_id
                WHERE s.account_id = ? AND p.owner_id != ?
                ORDER BY s.created_at DESC""",
            (acct["id"], acct["id"]),
        ).fetchall()
        totals = conn.execute(
            """SELECT COUNT(*) AS jobs,
                      COALESCE(SUM(j.tokens_in + j.tokens_out), 0) AS tokens,
                      COUNT(DISTINCT j.project_id) AS projects_helped
                 FROM jobs j JOIN nodes n ON n.id = j.node_id
                WHERE n.account_id = ? AND j.status = 'done'""",
            (acct["id"],),
        ).fetchone()
        nodes_online = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE account_id=? AND last_seen > ?",
            (acct["id"], db.now() - NODE_ONLINE_WINDOW),
        ).fetchone()[0]

        return {
            "name": acct["name"],
            "display_name": acct["display_name"],
            "joined": acct["created_at"],
            "donated": {
                "jobs": totals["jobs"],
                "tokens": totals["tokens"],
                "projects_helped": totals["projects_helped"],
            },
            "nodes_online": nodes_online,
            "projects": [
                project_public(p, project_stats(conn, p["id"])) for p in owned
            ],
            "supporting": [dict(r) for r in supported],
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
        key, key_hash = db.new_key("sci")
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


@app.get("/api/projects/{slug}/supporters")
async def project_supporters(slug: str):
    """Everyone who opted their nodes into this project's queue — which is a
    superset of the donor leaderboard (a supporter may not have completed a
    job yet)."""
    with db.connect() as conn:
        p = conn.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
        if not p:
            raise HTTPException(404, "no such project")
        rows = conn.execute(
            """SELECT a.id, a.name, a.display_name, s.created_at,
                      (SELECT COUNT(*) FROM nodes n
                        WHERE n.account_id = a.id AND n.last_seen > :online)
                        AS nodes_online,
                      (SELECT COUNT(*) FROM jobs j JOIN nodes n2 ON n2.id = j.node_id
                        WHERE n2.account_id = a.id AND j.project_id = :pid
                          AND j.status = 'done') AS jobs,
                      (SELECT COALESCE(SUM(j.tokens_in + j.tokens_out), 0)
                         FROM jobs j JOIN nodes n3 ON n3.id = j.node_id
                        WHERE n3.account_id = a.id AND j.project_id = :pid
                          AND j.status = 'done') AS tokens
               FROM supports s JOIN accounts a ON a.id = s.account_id
               WHERE s.project_id = :pid
               ORDER BY tokens DESC, s.created_at ASC""",
            {"pid": p["id"], "online": db.now() - NODE_ONLINE_WINDOW},
        ).fetchall()
        return {
            "project": p["slug"],
            "supporters": [
                {
                    "name": r["name"],
                    "display_name": r["display_name"],
                    "since": r["created_at"],
                    "nodes_online": r["nodes_online"],
                    "jobs": r["jobs"],
                    "tokens": r["tokens"],
                    "is_maintainer": r["id"] == p["owner_id"],
                }
                for r in rows
            ],
        }


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
        token, token_hash = db.new_key("scn")
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


@app.get("/api/nodes/me")
async def node_me(request: Request):
    """What this node looks like from the pool's side — used by
    `node_connector.py --status` to report on a backgrounded runner."""
    with db.connect() as conn:
        node = require_node(conn, request)
        acct = conn.execute(
            "SELECT name, display_name FROM accounts WHERE id=?",
            (node["account_id"],),
        ).fetchone()
        serving = conn.execute(
            """SELECT p.slug, p.name FROM supports s
                 JOIN projects p ON p.id = s.project_id
                WHERE s.account_id = ? ORDER BY p.name""",
            (node["account_id"],),
        ).fetchall()
        recent = conn.execute(
            """SELECT j.id, j.status, j.title, j.model, j.model_used, j.runner,
                      j.finished_at, p.slug
                 FROM jobs j JOIN projects p ON p.id = j.project_id
                WHERE j.node_id = ? ORDER BY j.id DESC LIMIT 5""",
            (node["id"],),
        ).fetchall()
        t = db.now()
        return {
            "node": {
                "name": node["name"],
                "runners": json.loads(node["runners"]),
                "models": json.loads(node["models"]),
                "jobs_done": node["jobs_done"],
                "last_seen": node["last_seen"],
                "online": bool(node["last_seen"]
                               and t - node["last_seen"] < NODE_ONLINE_WINDOW),
            },
            "account": {"name": acct["name"],
                        "display_name": acct["display_name"]},
            "serving": [dict(r) for r in serving],
            "recent_jobs": [dict(r) for r in recent],
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


# ------------------------------------------------------- model catalog

# Live model lists come from each provider's models API when the server has
# that provider's key in its environment. The keys are used for this
# read-only metadata call ONLY — inference always happens on donor nodes.
FALLBACK_CATALOG = {
    "Claude": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5",
               "claude-fable-5"],
    "OpenAI": ["gpt-5.1", "gpt-5.1-codex", "gpt-5", "gpt-5-mini", "gpt-5-nano"],
    "Grok": ["grok-4.1", "grok-4", "grok-4-fast", "grok-code-fast-1"],
}
CATALOG_TTL = 3600          # when every provider answered
CATALOG_TTL_PARTIAL = 300   # retry sooner if any provider fell back
_catalog_cache: dict = {"at": 0.0, "ttl": 0, "data": None}


def _provider_get(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url)
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _fetch_claude_models() -> list[str] | None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    data = _provider_get(
        "https://api.anthropic.com/v1/models?limit=100",
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    rows = sorted(data.get("data", []),
                  key=lambda m: m.get("created_at", ""), reverse=True)
    return [m["id"] for m in rows]


def _fetch_openai_models() -> list[str] | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    data = _provider_get(
        "https://api.openai.com/v1/models",
        {"Authorization": f"Bearer {api_key}"},
    )
    skip = ("audio", "realtime", "tts", "transcribe", "embed", "image",
            "dall-e", "whisper", "moderation", "davinci", "babbage", "search")
    rows = sorted(data.get("data", []),
                  key=lambda m: m.get("created", 0), reverse=True)
    return [
        m["id"] for m in rows
        if (m["id"].startswith("gpt-") or re.match(r"^o\d", m["id"]))
        and not any(s in m["id"] for s in skip)
    ]


def _fetch_grok_models() -> list[str] | None:
    api_key = os.environ.get("XAI_API_KEY")
    if not api_key:
        return None
    data = _provider_get(
        "https://api.x.ai/v1/models",
        {"Authorization": f"Bearer {api_key}"},
    )
    rows = sorted(data.get("data", []),
                  key=lambda m: m.get("created", 0), reverse=True)
    return [m["id"] for m in rows]


def _fetch_or_none(fn) -> list[str] | None:
    try:
        return fn() or None
    except Exception:
        return None


def _online_local_models(exclude: set[str]) -> list[str]:
    """Concrete (non-glob) model ids advertised by currently-online nodes —
    e.g. llama3.1:8b served via Ollama or LM Studio on a donor machine."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT models FROM nodes WHERE last_seen > ?",
            (db.now() - NODE_ONLINE_WINDOW,),
        ).fetchall()
    return sorted({
        m for r in rows for m in json.loads(r["models"])
        if m and not any(c in m for c in "*?[") and m not in exclude
    })


@app.get("/api/models/catalog")
async def models_catalog():
    if _catalog_cache["data"] and time.time() - _catalog_cache["at"] < _catalog_cache["ttl"]:
        data = _catalog_cache["data"]
    else:
        fetched = await asyncio.gather(
            asyncio.to_thread(_fetch_or_none, _fetch_claude_models),
            asyncio.to_thread(_fetch_or_none, _fetch_openai_models),
            asyncio.to_thread(_fetch_or_none, _fetch_grok_models),
        )
        providers, live = {}, {}
        for name, models in zip(FALLBACK_CATALOG, fetched):
            live[name] = models is not None
            providers[name] = models if models is not None else FALLBACK_CATALOG[name]
        data = {"providers": providers, "live": live, "fetched_at": time.time()}
        ttl = CATALOG_TTL if all(live.values()) else CATALOG_TTL_PARTIAL
        _catalog_cache.update(at=time.time(), ttl=ttl, data=data)
    # Local models reflect who is online right now, so they bypass the cache.
    known = {m for models in data["providers"].values() for m in models}
    local = _online_local_models(known)
    if not local:
        return data
    return {
        "providers": {**data["providers"], "Local nodes": local},
        "live": {**data["live"], "Local nodes": True},
        "fetched_at": data["fetched_at"],
    }


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
            {"id": m, "object": "model", "owned_by": "sparecycles"} for m in seen
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
                "id": f"chatcmpl-sc{jid}", "object": "chat.completion.chunk",
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
        "id": f"chatcmpl-sc{jid}",
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

# Browsers, iOS and link crawlers all probe the site root for these regardless
# of what the HTML declares, so serve them from / as well as /static.
ROOT_ASSETS = {
    "favicon.ico": ("favicon.ico", "image/x-icon"),
    "favicon.svg": ("favicon.svg", "image/svg+xml"),
    "apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    # Pre-iOS-8 devices ask for the -precomposed name first.
    "apple-touch-icon-precomposed.png": ("apple-touch-icon.png", "image/png"),
    "icon-192.png": ("icon-192.png", "image/png"),
    "icon-512.png": ("icon-512.png", "image/png"),
    "og-image.png": ("og-image.png", "image/png"),
    "site.webmanifest": ("site.webmanifest", "application/manifest+json"),
}


def _serve_asset(filename: str, media_type: str):
    async def handler():
        return FileResponse(
            os.path.join(WEB_DIR, filename), media_type=media_type,
            headers={"Cache-Control": "public, max-age=86400"},
        )
    return handler


for _route, (_file, _mime) in ROOT_ASSETS.items():
    # HEAD too: link scrapers and uptime checks probe assets that way.
    app.add_api_route(f"/{_route}", _serve_asset(_file, _mime),
                      methods=["GET", "HEAD"], include_in_schema=False)


def public_base(request: Request) -> str:
    """Absolute origin for og:/twitter: tags — relative image URLs are ignored
    by every major link scraper. Set SPARECYCLES_PUBLIC_URL when the public
    hostname differs from what reaches the app (CDN, tunnel, odd proxy)."""
    configured = os.environ.get("SPARECYCLES_PUBLIC_URL")
    if configured:
        return configured.rstrip("/")
    fwd = request.headers.get("x-forwarded-proto", "")
    scheme = fwd.split(",")[0].strip() or request.url.scheme
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host") or request.url.netloc)
    return f"{scheme}://{host}"


def asset_version() -> str:
    """Cache-buster derived from the front-end files' mtimes, so a deploy can
    never leave a returning visitor running stale JS against a new API."""
    stamp = 0.0
    for name in ("app.js", "styles.css"):
        try:
            stamp = max(stamp, os.path.getmtime(os.path.join(WEB_DIR, name)))
        except OSError:
            pass
    return f"{int(stamp):x}"


@app.api_route("/", methods=["GET", "HEAD"])
async def index(request: Request):
    with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    html = (html.replace("{{BASE}}", public_base(request))
                .replace("{{V}}", asset_version()))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
