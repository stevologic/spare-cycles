"""Shared fixtures. The environment must be set before server.main is
imported (db path is resolved at import time), which is why the env setup
lives at module scope here and the app import lives inside the fixture."""

import os
import sys
import tempfile
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("SPARECYCLES_DATA",
                      tempfile.mkdtemp(prefix="sparecycles-test-"))
os.environ.setdefault("SPARECYCLES_RATELIMIT", "off")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from server.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def unique():
    """Unique, name-regex-safe identifiers so tests never collide."""
    return lambda prefix="u": f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def account(client, unique):
    """Factory: register a fresh account, return its credentials."""
    def make():
        name = unique("acct")
        r = client.post("/api/register",
                        json={"name": name, "display_name": f"T {name}"})
        assert r.status_code == 200, r.text
        d = r.json()
        return {
            "name": name,
            "key": d["api_key"],
            "codes": d["recovery_codes"],
            "h": {"Authorization": f"Bearer {d['api_key']}"},
        }
    return make


@pytest.fixture
def project(client, unique):
    """Factory: create a project owned by `acct`, return slug + keys."""
    def make(acct, **overrides):
        body = {"name": unique("proj"), "model": "test-model-alpha",
                "fallback_model": "test-model-beta"}
        body.update(overrides)
        r = client.post("/api/projects", json=body, headers=acct["h"])
        assert r.status_code == 200, r.text
        d = r.json()
        return {"slug": d["slug"], "inference_key": d["inference_key"]}
    return make


@pytest.fixture
def node(client, unique):
    """Factory: pair a node onto `acct` via a real pairing code."""
    def make(acct, models=("*",), runners=("echo",)):
        code = client.post("/api/pair", headers=acct["h"]).json()["code"]
        r = client.post("/api/nodes/register", json={
            "code": code, "name": unique("node"),
            "runners": list(runners), "models": list(models),
        })
        assert r.status_code == 200, r.text
        d = r.json()
        return {"id": d["node_id"], "token": d["node_token"],
                "h": {"Authorization": f"Bearer {d['node_token']}"}}
    return make


@pytest.fixture
def work_one(client):
    """Poll once as `nd`, complete the claimed job, return the job payload."""
    def run(nd, output="test output", models=("*",)):
        r = client.post("/api/nodes/poll",
                        json={"wait": 0, "models": list(models)}, headers=nd["h"])
        assert r.status_code == 200, r.text
        job = r.json()["job"]
        assert job, "expected a job to claim, queue was empty"
        done = client.post(f"/api/nodes/jobs/{job['id']}/complete",
                           json={"output": output, "runner": "echo",
                                 "tokens_in": 3, "tokens_out": 5},
                           headers=nd["h"])
        assert done.status_code == 200, done.text
        return job
    return run
