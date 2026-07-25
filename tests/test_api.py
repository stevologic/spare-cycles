"""End-to-end API flows through the FastAPI test client: accounts, projects,
nodes, the batch queue protocol, karma ordering, recovery, and rate limits."""

import pytest


# ---------------------------------------------------------------- accounts

def test_register_returns_key_and_recovery_codes(account):
    a = account()
    assert a["key"].startswith("sck_")
    assert len(a["codes"]) == 5
    assert all(c.startswith("SCR-") for c in a["codes"])


def test_register_rejects_duplicate_name(client, account):
    a = account()
    r = client.post("/api/register", json={"name": a["name"]})
    assert r.status_code == 409


@pytest.mark.parametrize("bad", ["ab", "Has Spaces", "x" * 31, "", "-lead"])
def test_register_rejects_bad_names(client, bad):
    assert client.post("/api/register", json={"name": bad}).status_code == 400


def test_register_normalizes_case(client, unique):
    name = unique("case")
    r = client.post("/api/register", json={"name": name.upper()})
    assert r.status_code == 200
    assert r.json()["account"]["name"] == name  # stored lowercase


def test_me_requires_valid_key(client, account):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={
        "Authorization": "Bearer sck_wrong"}).status_code == 401
    a = account()
    me = client.get("/api/me", headers=a["h"])
    assert me.status_code == 200
    assert me.json()["recovery_codes_left"] == 5


# ---------------------------------------------------------------- projects

def test_project_lifecycle(client, account, project):
    a = account()
    p = project(a, tagline="testing tagline")
    assert p["inference_key"].startswith("sci_")

    listing = client.get("/api/projects").json()["projects"]
    mine = next(x for x in listing if x["slug"] == p["slug"])
    assert mine["owner_is_donor"] is False
    assert mine["jobs_done"] == 0

    detail = client.get(f"/api/projects/{p['slug']}").json()
    assert detail["tagline"] == "testing tagline"
    assert detail["model"] == "test-model-alpha"

    assert client.get("/api/projects/nope-nope").status_code == 404


def test_project_edit_is_owner_only(client, account, project):
    owner, stranger = account(), account()
    p = project(owner)
    r = client.patch(f"/api/projects/{p['slug']}",
                     json={"tagline": "hacked"}, headers=stranger["h"])
    assert r.status_code == 403

    r = client.patch(f"/api/projects/{p['slug']}",
                     json={"tagline": "new tag", "model": "swapped-model"},
                     headers=owner["h"])
    assert r.status_code == 200
    assert r.json()["tagline"] == "new tag"
    assert r.json()["model"] == "swapped-model"

    r = client.patch(f"/api/projects/{p['slug']}",
                     json={"model": ""}, headers=owner["h"])
    assert r.status_code == 400


def test_rotate_inference_key_kills_old_key(client, account, project):
    a = account()
    p = project(a)
    old = p["inference_key"]

    r = client.post(f"/api/projects/{p['slug']}/rotate_key", headers=a["h"])
    assert r.status_code == 200
    new = r.json()["inference_key"]
    assert new != old and new.startswith("sci_")

    # The auth check runs before any job is queued, so bad keys 401 instantly.
    dead = client.post("/v1/chat/completions",
                       headers={"Authorization": f"Bearer {old}"},
                       json={"messages": [{"role": "user", "content": "x"}]})
    assert dead.status_code == 401


# ------------------------------------------------------------ nodes + queue

def test_pairing_code_is_single_use(client, account):
    a = account()
    code = client.post("/api/pair", headers=a["h"]).json()["code"]
    first = client.post("/api/nodes/register",
                        json={"code": code, "name": "n1", "models": ["*"]})
    assert first.status_code == 200
    assert first.json()["node_token"].startswith("scn_")
    second = client.post("/api/nodes/register",
                         json={"code": code, "name": "n2", "models": ["*"]})
    assert second.status_code == 401


def test_batch_job_full_cycle(client, account, project, node, work_one):
    a = account()
    p = project(a)
    nd = node(a)

    empty = client.post("/api/nodes/poll", json={"wait": 0}, headers=nd["h"])
    assert empty.json()["job"] is None

    jid = client.post(f"/api/projects/{p['slug']}/jobs",
                      json={"title": "t", "prompt": "do the thing"},
                      headers=a["h"]).json()["job_id"]

    job = work_one(nd, output="all done here")
    assert job["id"] == jid
    assert job["model"] == "test-model-alpha"

    waited = client.get(f"/api/jobs/{jid}/wait", params={"timeout": 5}).json()
    assert waited["status"] == "done"
    assert waited["output"] == "all done here"
    assert waited["donor"] == f"T {a['name']}"

    detail = client.get(f"/api/projects/{p['slug']}").json()
    assert detail["jobs_done"] == 1
    assert detail["owner_is_donor"] is True
    assert detail["donors"][0]["is_maintainer"] is True


def test_model_matching_respects_fallback_at_claim(client, account, project,
                                                   node):
    a = account()
    p = project(a, model="modelx-9", fallback_model="modely-2")
    client.post(f"/api/projects/{p['slug']}/jobs",
                json={"prompt": "x"}, headers=a["h"])
    nd = node(a, models=("modely-*",))
    r = client.post("/api/nodes/poll",
                    json={"wait": 0, "models": ["modely-*"]}, headers=nd["h"])
    job = r.json()["job"]
    assert job and job["model"] == "modely-2"  # fallback chosen at claim time


def test_cancel_queued_job(client, account, project, node):
    owner, stranger = account(), account()
    p = project(owner)
    jid = client.post(f"/api/projects/{p['slug']}/jobs",
                      json={"prompt": "cancel me"},
                      headers=owner["h"]).json()["job_id"]

    assert client.post(f"/api/jobs/{jid}/cancel",
                       headers=stranger["h"]).status_code == 403
    assert client.post(f"/api/jobs/{jid}/cancel",
                       headers=owner["h"]).status_code == 200
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "cancelled"
    # A second cancel is a conflict, and the queue no longer offers the job.
    assert client.post(f"/api/jobs/{jid}/cancel",
                       headers=owner["h"]).status_code == 409
    nd = node(owner)
    assert client.post("/api/nodes/poll", json={"wait": 0},
                       headers=nd["h"]).json()["job"] is None


def test_karma_donors_jump_the_queue(client, account, project, node, work_one):
    """A project whose owner has completed donations outranks a non-donor's
    earlier-queued job — the v1 karma flywheel."""
    donor, newbie, volunteer = account(), account(), account()

    # Make `donor` an actual donor: complete a job on their own project.
    dp = project(donor)
    client.post(f"/api/projects/{dp['slug']}/jobs",
                json={"prompt": "seed"}, headers=donor["h"])
    work_one(node(donor))

    # Non-donor queues FIRST; donor queues SECOND.
    np_ = project(newbie)
    client.post(f"/api/projects/{np_['slug']}/jobs",
                json={"prompt": "newbie job"}, headers=newbie["h"])
    client.post(f"/api/projects/{dp['slug']}/jobs",
                json={"prompt": "donor job"}, headers=donor["h"])

    # A volunteer serving both projects should get the donor's job first.
    for slug in (np_["slug"], dp["slug"]):
        client.post(f"/api/projects/{slug}/support", headers=volunteer["h"])
    vn = node(volunteer)
    first = client.post("/api/nodes/poll", json={"wait": 0},
                        headers=vn["h"]).json()["job"]
    second = client.post("/api/nodes/poll", json={"wait": 0},
                         headers=vn["h"]).json()["job"]
    assert first["prompt"] == "donor job"
    assert second["prompt"] == "newbie job"


def test_node_revocation(client, account, project, node, work_one):
    a = account()
    p = project(a)
    nd = node(a)
    client.post(f"/api/projects/{p['slug']}/jobs",
                json={"prompt": "x"}, headers=a["h"])
    work_one(nd)

    r = client.delete(f"/api/nodes/{nd['id']}", headers=a["h"])
    assert r.status_code == 200
    # Token dead, node gone from the account view…
    assert client.post("/api/nodes/poll", json={"wait": 0},
                       headers=nd["h"]).status_code == 401
    assert all(n["id"] != nd["id"]
               for n in client.get("/api/me", headers=a["h"]).json()["nodes"])
    # …but the donation stays attributed.
    detail = client.get(f"/api/projects/{p['slug']}").json()
    assert detail["donors"] and detail["donors"][0]["jobs"] == 1
    # Revoking someone else's node (or twice) is a 404.
    assert client.delete(f"/api/nodes/{nd['id']}",
                         headers=account()["h"]).status_code == 404


# ---------------------------------------------------------------- recovery

def test_recovery_code_flow(client, account):
    a = account()
    r = client.post("/api/recover",
                    json={"name": a["name"], "recovery_code": a["codes"][0]})
    assert r.status_code == 200
    new_key = r.json()["api_key"]
    assert r.json()["recovery_codes_left"] == 4

    old = client.get("/api/me", headers=a["h"])
    assert old.status_code == 401  # old key is dead
    fresh = client.get("/api/me",
                       headers={"Authorization": f"Bearer {new_key}"})
    assert fresh.status_code == 200

    # The used code cannot be replayed.
    again = client.post("/api/recover",
                        json={"name": a["name"], "recovery_code": a["codes"][0]})
    assert again.status_code == 401


def test_recovery_via_node_token(client, account, node):
    a = account()
    nd = node(a)
    r = client.post("/api/recover/node", headers=nd["h"])
    assert r.status_code == 200
    assert r.json()["account"] == a["name"]
    assert client.get("/api/me", headers=a["h"]).status_code == 401


def test_regenerating_codes_invalidates_old_set(client, account):
    a = account()
    r = client.post("/api/recovery_codes", headers=a["h"])
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 5
    dead = client.post("/api/recover",
                       json={"name": a["name"], "recovery_code": a["codes"][0]})
    assert dead.status_code == 401


def test_recover_rejects_unknown_user_and_bad_code(client, account):
    a = account()
    assert client.post("/api/recover", json={
        "name": "ghost-user-xyz", "recovery_code": "SCR-0000-0000"
    }).status_code == 401
    assert client.post("/api/recover", json={
        "name": a["name"], "recovery_code": "SCR-0000-0000"
    }).status_code == 401


# ------------------------------------------------- supporters and profiles

def test_supporters_and_public_profile(client, account, project):
    owner, fan = account(), account()
    p = project(owner)
    client.post(f"/api/projects/{p['slug']}/support", headers=fan["h"])

    sup = client.get(f"/api/projects/{p['slug']}/supporters").json()
    names = {s["name"]: s for s in sup["supporters"]}
    assert owner["name"] in names and names[owner["name"]]["is_maintainer"]
    assert fan["name"] in names and not names[fan["name"]]["is_maintainer"]

    prof = client.get(f"/api/accounts/{owner['name']}").json()
    assert prof["projects"][0]["slug"] == p["slug"]
    assert client.get("/api/accounts/nobody-here").status_code == 404


# --------------------------------------------------------- platform pieces

def test_health_and_stats(client):
    h = client.get("/api/health").json()
    assert h["ok"] is True and h["uptime_seconds"] >= 0
    s = client.get("/api/stats").json()
    assert set(s) >= {"projects", "accounts", "nodes_online", "jobs_done"}


def test_model_catalog_shape(client, monkeypatch):
    # Force the fallback path so tests are deterministic and offline.
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    cat = client.get("/api/models/catalog").json()
    assert {"Claude", "OpenAI", "Grok"} <= set(cat["providers"])
    assert all(cat["providers"][k] for k in ("Claude", "OpenAI", "Grok"))


def test_chat_completions_error_paths(client, account, project):
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer sci_bogus"},
                    json={"messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"

    a = account()
    p = project(a)
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": f"Bearer {p['inference_key']}"},
                    json={"messages": []})
    assert r.status_code == 400


def test_recover_is_rate_limited(client, monkeypatch):
    monkeypatch.delenv("SPARECYCLES_RATELIMIT", raising=False)
    statuses = [
        client.post("/api/recover", json={
            "name": "rl-ghost", "recovery_code": f"SCR-{i:04d}-0000"
        }).status_code
        for i in range(12)
    ]
    assert statuses[:10] == [401] * 10
    assert statuses[10] == statuses[11] == 429
