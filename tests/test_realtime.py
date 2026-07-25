"""Integration tests against a real uvicorn process: the OpenAI-compatible
realtime proxy (incl. streaming) and the actual node_connector script."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _req(url, body=None, token=None, method=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    method = method or ("POST" if data else "GET")
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


@pytest.fixture(scope="module")
def server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ,
               SPARECYCLES_DATA=tempfile.mkdtemp(prefix="sc-rt-"),
               SPARECYCLES_RATELIMIT="off")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.main:app",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                _req(f"{base}/api/health", timeout=2)
                break
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(0.25)
        else:
            raise RuntimeError("uvicorn did not become healthy")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def rig(server):
    """Account + project + registered node against the live server."""
    acct = _req(f"{server}/api/register",
                {"name": "rt-owner", "display_name": "RT Owner"})
    key = acct["api_key"]
    proj = _req(f"{server}/api/projects",
                {"name": "RT Project", "model": "rt-model"}, token=key)
    code = _req(f"{server}/api/pair", {}, token=key)["code"]
    node = _req(f"{server}/api/nodes/register",
                {"code": code, "name": "rt-node", "runners": ["sim"],
                 "models": ["*"]})
    return {"base": server, "key": key, "ikey": proj["inference_key"],
            "slug": proj["slug"], "node_token": node["node_token"]}


def _serve_jobs(rig, stop, prefix="echo:"):
    """Background thread acting as a donor node."""
    while not stop.is_set():
        try:
            r = _req(f"{rig['base']}/api/nodes/poll",
                     {"wait": 2, "models": ["*"]},
                     token=rig["node_token"], timeout=30)
            job = r.get("job")
            if job:
                _req(f"{rig['base']}/api/nodes/jobs/{job['id']}/complete",
                     {"output": prefix + job["prompt"], "runner": "sim",
                      "tokens_in": 2, "tokens_out": 4},
                     token=rig["node_token"])
        except Exception:
            time.sleep(0.5)


@pytest.fixture()
def node_sim(rig):
    stop = threading.Event()
    t = threading.Thread(target=_serve_jobs, args=(rig, stop), daemon=True)
    t.start()
    yield
    stop.set()
    t.join(timeout=10)


def test_realtime_chat_completion_roundtrip(rig, node_sim):
    t0 = time.time()
    resp = _req(f"{rig['base']}/v1/chat/completions",
                {"messages": [{"role": "user", "content": "ping-123"}]},
                token=rig["ikey"], timeout=90)
    elapsed = time.time() - t0
    assert resp["choices"][0]["message"]["content"] == "echo:ping-123"
    assert resp["model"] == "rt-model"
    assert resp["usage"]["total_tokens"] == 6
    assert resp["id"].startswith("chatcmpl-sc")
    # Event-driven wakeups should make this fast even on a busy CI box.
    assert elapsed < 30, f"realtime roundtrip took {elapsed:.1f}s"


def test_streaming_chat_completion(rig, node_sim):
    body = json.dumps({"stream": True,
                       "messages": [{"role": "user", "content": "flow"}]})
    r = urllib.request.Request(f"{rig['base']}/v1/chat/completions",
                               data=body.encode(), method="POST")
    r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", f"Bearer {rig['ikey']}")
    with urllib.request.urlopen(r, timeout=90) as resp:
        raw = resp.read().decode()
    frames = [ln[6:] for ln in raw.splitlines() if ln.startswith("data: ")]
    assert frames[-1] == "[DONE]"
    chunks = [json.loads(f) for f in frames[:-1]]
    assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"
    text = "".join(c["choices"][0]["delta"].get("content", "")
                   for c in chunks)
    assert text == "echo:flow"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_real_connector_script_end_to_end(rig):
    """Run the actual node_connector.py (echo runner) against the live
    server: pair, claim a queued batch job, complete it, report status."""
    home = tempfile.mkdtemp(prefix="sc-conn-home-")
    env = dict(os.environ, SPARECYCLES_HOME=home)
    code = _req(f"{rig['base']}/api/pair", {}, token=rig["key"])["code"]
    jid = _req(f"{rig['base']}/api/projects/{rig['slug']}/jobs",
               {"title": "for the script", "prompt": "hello script"},
               token=rig["key"])["job_id"]

    run = subprocess.run(
        [sys.executable, os.path.join(ROOT, "connector", "node_connector.py"),
         "--server", rig["base"], "--code", code,
         "--runners", "echo", "--name", "ci-script-node", "--once"],
        env=env, capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace",
    )
    assert run.returncode == 0, run.stdout + run.stderr

    job = _req(f"{rig['base']}/api/jobs/{jid}")
    assert job["status"] == "done"
    assert "[echo runner]" in job["output"]
    assert "hello script" in job["output"]

    status = subprocess.run(
        [sys.executable, os.path.join(ROOT, "connector", "node_connector.py"),
         "--status"],
        env=env, capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace",
    )
    assert status.returncode == 0, status.stdout + status.stderr
    assert "ci-script-node" in status.stdout
    assert "1 job(s) completed" in status.stdout
