#!/usr/bin/env python3
"""PromptPool node connector — donate your idle AI tokens to projects you support.

Stdlib only. First run (pairing code from your Account page):

    python node_connector.py --server http://localhost:8377 --code AB12-CD34

Later runs just:  python node_connector.py

Your API keys NEVER leave this machine. The connector reads prompts from the
pool, runs them through a local CLI (claude/codex/gemini/cursor-agent) or a
direct provider API using YOUR environment variables, and posts back only the
answer. CLI runners execute in an empty scratch directory with agentic turns
capped. You are responsible for staying within your provider's terms of
service — metered API keys are the recommended way to donate.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from fnmatch import fnmatch
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("PROMPTPOOL_HOME", Path.home() / ".promptpool"))
CONFIG_PATH = CONFIG_DIR / "node.json"
WORK_DIR = CONFIG_DIR / "workdir"  # empty scratch cwd for CLI runners

# Runner templates. {model} is substituted; prompt goes via stdin when
# "stdin" is true, otherwise replaces "{prompt}" in the argument list.
# Override any of these in node.json under "runner_overrides".
CLI_RUNNERS = {
    "claude": {
        "bin": "claude",
        "models": ["claude*", "sonnet*", "opus*", "haiku*"],
        "cmd": ["claude", "-p", "--model", "{model}",
                "--output-format", "text", "--max-turns", "1"],
        "stdin": True,
    },
    "codex": {
        "bin": "codex",
        "models": ["gpt*", "codex*", "o1*", "o3*", "o4*"],
        "cmd": ["codex", "exec", "-m", "{model}", "{prompt}"],
        "stdin": False,
    },
    "gemini": {
        "bin": "gemini",
        "models": ["gemini*"],
        "cmd": ["gemini", "-m", "{model}", "-p", "{prompt}"],
        "stdin": False,
    },
    "cursor-agent": {
        "bin": "cursor-agent",
        "models": ["cursor*"],
        "cmd": ["cursor-agent", "-p", "--model", "{model}",
                "--output-format", "text", "{prompt}"],
        "stdin": False,
    },
}
API_RUNNERS = {
    "anthropic-api": {"env": "ANTHROPIC_API_KEY", "models": ["claude*"]},
    "openai-api": {"env": "OPENAI_API_KEY", "models": ["gpt*", "o1*", "o3*", "o4*"]},
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def http_json(method: str, url: str, body=None, token=None, timeout=40):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ------------------------------------------------------------------ runners

def detect_runners(requested: list[str] | None) -> dict[str, dict]:
    found = {}
    for name, spec in CLI_RUNNERS.items():
        if shutil.which(spec["bin"]):
            found[name] = dict(spec, kind="cli")
    for name, spec in API_RUNNERS.items():
        if os.environ.get(spec["env"]):
            found[name] = dict(spec, kind="api")
    if requested:
        if "echo" in requested:
            found["echo"] = {"kind": "echo", "models": ["*"]}
        found = {k: v for k, v in found.items() if k in requested}
        missing = [r for r in requested if r not in found]
        if missing:
            log(f"warning: requested runners not available here: {', '.join(missing)}")
    if not found:
        log("no runners detected (no AI CLIs on PATH, no provider keys in env).")
        log("falling back to the 'echo' test runner — it just echoes prompts back.")
        found["echo"] = {"kind": "echo", "models": ["*"]}
    return found


def run_cli(spec: dict, job: dict) -> dict:
    cmd = []
    for part in spec["cmd"]:
        part = part.replace("{model}", job["model"])
        part = part.replace("{prompt}", job["prompt"])
        cmd.append(part)
    exe = shutil.which(cmd[0])
    if exe:
        cmd[0] = exe
    timeout = max(60, int(job.get("lease_seconds", 300)) - 30)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        cmd,
        input=job["prompt"] if spec.get("stdin") else None,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=str(WORK_DIR),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"runner exited {proc.returncode}: {(proc.stderr or proc.stdout or '')[:500]}"
        )
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError("runner produced no output")
    return {"output": out}


def run_anthropic(job: dict) -> dict:
    body = {
        "model": job["model"],
        "max_tokens": job.get("max_tokens") or 1024,
        "messages": [{"role": "user", "content": job["prompt"]}],
    }
    if job.get("temperature") is not None:
        body["temperature"] = job["temperature"]
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(), method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", os.environ["ANTHROPIC_API_KEY"])
    req.add_header("anthropic-version", "2023-06-01")
    with urllib.request.urlopen(req, timeout=180) as resp:
        r = json.loads(resp.read().decode())
    text = "".join(b.get("text", "") for b in r.get("content", []))
    usage = r.get("usage", {})
    return {"output": text, "tokens_in": usage.get("input_tokens"),
            "tokens_out": usage.get("output_tokens")}


def run_openai(job: dict) -> dict:
    body = {
        "model": job["model"],
        "messages": [{"role": "user", "content": job["prompt"]}],
    }
    if job.get("temperature") is not None:
        body["temperature"] = job["temperature"]
    if job.get("max_tokens"):
        body["max_tokens"] = job["max_tokens"]
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode(), method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {os.environ['OPENAI_API_KEY']}")
    with urllib.request.urlopen(req, timeout=180) as resp:
        r = json.loads(resp.read().decode())
    usage = r.get("usage", {})
    return {"output": r["choices"][0]["message"]["content"],
            "tokens_in": usage.get("prompt_tokens"),
            "tokens_out": usage.get("completion_tokens")}


def execute(runners: dict, job: dict) -> dict:
    name = pick_runner(runners, job["model"])
    if not name:
        raise RuntimeError(f"no local runner serves model '{job['model']}'")
    spec = runners[name]
    log(f"  running job #{job['id']} with {name} (model {job['model']}) ...")
    if spec["kind"] == "echo":
        result = {"output": f"[echo runner] You said:\n\n{job['prompt']}"}
    elif spec["kind"] == "api":
        result = run_anthropic(job) if name == "anthropic-api" else run_openai(job)
    else:
        result = run_cli(spec, job)
    result["runner"] = name
    result["model_used"] = job["model"]
    return result


def pick_runner(runners: dict, model: str) -> str | None:
    for name, spec in runners.items():
        if any(fnmatch(model.lower(), pat.lower()) for pat in spec["models"]):
            return name
    return None


# ------------------------------------------------------------------ lifecycle

def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def register(server: str, code: str, name: str, runners: dict,
             models: list[str]) -> dict:
    r = http_json("POST", f"{server}/api/nodes/register", {
        "code": code, "name": name,
        "runners": sorted(runners), "models": models,
    })
    cfg = {"server": server, "node_token": r["node_token"],
           "node_id": r["node_id"], "name": name, "account": r["account"]}
    save_config(cfg)
    log(f"node '{name}' registered to account @{r['account']} "
        f"(config: {CONFIG_PATH})")
    return cfg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", help="PromptPool server URL")
    ap.add_argument("--code", help="pairing code from your Account page")
    ap.add_argument("--name", default=platform.node() or "node")
    ap.add_argument("--runners", help="comma list to restrict runners (e.g. claude,echo)")
    ap.add_argument("--models", help="comma list of model patterns this node serves")
    ap.add_argument("--once", action="store_true", help="process one job, then exit")
    args = ap.parse_args()

    requested = [r.strip() for r in args.runners.split(",")] if args.runners else None
    runners = detect_runners(requested)
    overrides = load_config().get("runner_overrides", {})
    for rname, patch in overrides.items():
        if rname in runners:
            runners[rname].update(patch)
    models = ([m.strip() for m in args.models.split(",")] if args.models
              else sorted({p for s in runners.values() for p in s["models"]}))

    cfg = load_config()
    if args.code:
        if not args.server:
            ap.error("--code requires --server")
        cfg = register(args.server.rstrip("/"), args.code.upper(), args.name,
                       runners, models)
    if not cfg.get("node_token"):
        ap.error("not registered — run once with --server URL --code XXXX-XXXX")
    server, token = cfg["server"], cfg["node_token"]

    log(f"PromptPool node '{cfg.get('name', args.name)}' → {server}")
    log(f"runners: {', '.join(runners)}   models: {', '.join(models)}")
    log("keys stay in this machine's environment; only prompts/answers move.")

    while True:
        try:
            resp = http_json("POST", f"{server}/api/nodes/poll",
                             {"runners": sorted(runners), "models": models,
                              "wait": 20},
                             token=token, timeout=40)
        except urllib.error.HTTPError as e:
            log(f"server rejected poll ({e.code}): {e.read().decode()[:200]}")
            if e.code == 401:
                return 1
            time.sleep(10)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            log(f"cannot reach server ({e}); retrying in 10s")
            time.sleep(10)
            continue

        job = resp.get("job")
        if not job:
            continue
        log(f"claimed job #{job['id']} ({job['kind']}) "
            f"for model {job['model']}")
        try:
            result = execute(runners, job)
            http_json("POST", f"{server}/api/nodes/jobs/{job['id']}/complete",
                      result, token=token)
            log(f"  ✓ job #{job['id']} done "
                f"({len(result['output'])} chars, runner {result['runner']})")
        except Exception as e:  # noqa: BLE001 — report any failure to the pool
            log(f"  ✗ job #{job['id']} failed: {e}")
            try:
                http_json("POST", f"{server}/api/nodes/jobs/{job['id']}/fail",
                          {"error": str(e)[:1000]}, token=token)
            except Exception:
                pass
        if args.once:
            return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nbye — your node is offline.")
        sys.exit(0)
