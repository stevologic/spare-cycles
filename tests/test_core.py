"""Pure-logic tests for the server's helper functions."""

from server.main import estimate_tokens, match_model, messages_to_prompt, slugify


# ------------------------------------------------------------------ slugify

def test_slugify_basic():
    assert slugify("Hello World!") == "hello-world"
    assert slugify("  DOGE Miner — Fullstack  ") == "doge-miner-fullstack"


def test_slugify_strips_to_empty_when_no_alnum():
    assert slugify("!!! ---") == ""


def test_slugify_caps_length():
    assert len(slugify("x" * 200)) <= 40


# ----------------------------------------------------------- token estimate

def test_estimate_tokens_floor_is_one():
    assert estimate_tokens("") == 1
    assert estimate_tokens(None) == 1


def test_estimate_tokens_scales():
    assert estimate_tokens("abcd" * 100) == 100


# ------------------------------------------------------------- model match

def _job(model, fallback=""):
    return {"model": model, "fallback_model": fallback}


def test_match_exact():
    assert match_model(_job("llama3.1:8b"), ["llama3.1:8b"]) == "llama3.1:8b"


def test_match_glob_pattern():
    assert match_model(_job("claude-opus-4-8"), ["claude*"]) == "claude-opus-4-8"


def test_match_case_insensitive():
    assert match_model(_job("GPT-5"), ["gpt*"]) == "GPT-5"


def test_match_falls_back_when_preferred_unserved():
    job = _job("claude-opus-4-8", fallback="gpt-5-mini")
    assert match_model(job, ["gpt*"]) == "gpt-5-mini"


def test_match_prefers_primary_over_fallback():
    job = _job("claude-opus-4-8", fallback="gpt-5-mini")
    assert match_model(job, ["gpt*", "claude*"]) == "claude-opus-4-8"


def test_match_none_when_nothing_serves():
    assert match_model(_job("grok-4", "gemini-pro"), ["claude*"]) is None


def test_match_wildcard_serves_everything():
    assert match_model(_job("anything-at-all"), ["*"]) == "anything-at-all"


# --------------------------------------------------------- prompt building

def test_single_user_message_passes_through_verbatim():
    assert messages_to_prompt([{"role": "user", "content": "hi"}]) == "hi"


def test_multi_turn_gets_labels_and_assistant_cue():
    prompt = messages_to_prompt([
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hey"},
        {"role": "user", "content": "explain"},
    ])
    assert "System: be brief" in prompt
    assert "User: hello" in prompt
    assert "Assistant: hey" in prompt
    assert prompt.endswith("Assistant:")


def test_content_part_lists_are_flattened():
    prompt = messages_to_prompt([
        {"role": "user", "content": [
            {"type": "text", "text": "part one"},
            {"type": "image_url", "image_url": {"url": "ignored"}},
            {"type": "text", "text": "part two"},
        ]},
        {"role": "user", "content": "tail"},
    ])
    assert "part one" in prompt and "part two" in prompt
    assert "ignored" not in prompt
