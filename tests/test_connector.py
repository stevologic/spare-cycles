"""Logic tests for the node connector (no network, no CLIs required)."""

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def nc():
    spec = importlib.util.spec_from_file_location(
        "node_connector",
        os.path.join(ROOT, "connector", "node_connector.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pick_runner_matches_exact_local_model(nc):
    runners = {"ollama": {"kind": "local", "models": ["llama3.1:8b"]}}
    assert nc.pick_runner(runners, "llama3.1:8b") == "ollama"
    assert nc.pick_runner(runners, "llama3.1:70b") is None


def test_pick_runner_glob_and_priority(nc):
    runners = {
        "claude": {"kind": "cli", "models": ["claude*", "opus*"]},
        "openai-api": {"kind": "api", "models": ["gpt*"]},
    }
    assert nc.pick_runner(runners, "claude-opus-4-8") == "claude"
    assert nc.pick_runner(runners, "gpt-5-mini") == "openai-api"
    assert nc.pick_runner(runners, "grok-4") is None


def test_grok_runner_registered(nc):
    assert "grok" in nc.CLI_RUNNERS
    assert "grok*" in nc.CLI_RUNNERS["grok"]["models"]
    assert nc.API_RUNNERS["xai-api"]["env"] == "XAI_API_KEY"


def test_local_server_parsers(nc):
    ollama = nc.LOCAL_SERVERS["ollama"]["parse"](
        {"models": [{"name": "llama3.1:8b"}, {"model": "qwen2.5:14b"}]})
    assert "llama3.1:8b" in ollama and "qwen2.5:14b" in ollama
    lmstudio = nc.LOCAL_SERVERS["lmstudio"]["parse"](
        {"data": [{"id": "mistral-7b"}, {"id": None}]})
    assert lmstudio[0] == "mistral-7b"


def test_detect_runners_falls_back_to_echo(nc, monkeypatch):
    monkeypatch.setattr(nc.shutil, "which", lambda _:  None)
    monkeypatch.setattr(nc, "detect_local_servers", dict)
    for spec in nc.API_RUNNERS.values():
        monkeypatch.delenv(spec["env"], raising=False)
    found = nc.detect_runners(None)
    assert found == {"echo": {"kind": "echo", "models": ["*"]}}


def test_detect_runners_respects_requested_filter(nc, monkeypatch):
    monkeypatch.setattr(nc.shutil, "which", lambda _: "/usr/bin/fake")
    monkeypatch.setattr(nc, "detect_local_servers", dict)
    found = nc.detect_runners(["claude"])
    assert set(found) == {"claude"}


def test_cli_templates_substitute_model(nc):
    cmd = [part.replace("{model}", "m-1").replace("{prompt}", "p")
           for part in nc.CLI_RUNNERS["claude"]["cmd"]]
    assert "m-1" in cmd and "{model}" not in " ".join(cmd)
