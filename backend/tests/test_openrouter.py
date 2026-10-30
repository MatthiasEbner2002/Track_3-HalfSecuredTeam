"""OpenRouter client on a fake urlopen: request shape, errors, retries, no caching."""

import io
import json
import urllib.error
import urllib.request

import pytest

from app import config
from app.rag import openrouter
from app.rag.openrouter import OpenRouterLLM, OpenRouterUnavailable

SCHEMA = {
    "type": "object",
    "properties": {
        "quote": {"type": "string"},
        "nested": {"type": "object", "properties": {"x": {"type": "integer"}}},
        "items_list": {"type": "array", "items": {"type": "object", "properties": {"y": {"type": "string"}}}},
    },
    "required": ["quote"],
}
ANSWER = {"quote": "yes", "nested": {"x": 1}}


def _payload(answer) -> io.BytesIO:
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(answer)}}]}).encode())


def _http_error(code: int, message: str = "oops") -> urllib.error.HTTPError:
    body = json.dumps({"error": {"message": message}}).encode()
    return urllib.error.HTTPError("url", code, "msg", {}, io.BytesIO(body))


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-secret")


@pytest.fixture
def http(monkeypatch):
    """Fake urlopen; .requests records every call, .queue holds responses/errors."""
    calls = []

    class FakeHTTP:
        queue: list = []

        def __call__(self, request, timeout=None):
            calls.append(request)
            item = self.queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    fake = FakeHTTP()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    fake.requests = calls
    return fake


def body_of(request) -> dict:
    return json.loads(request.data)


def test_request_shape_and_strict_schema(api_key, http):
    schema = json.loads(json.dumps(SCHEMA))
    http.queue = [_payload(ANSWER)]
    result = OpenRouterLLM().complete_json("sys", "user", schema)

    assert result == ANSWER
    (request,) = http.requests
    assert request.get_header("Authorization") == "Bearer sk-test-secret"
    body = body_of(request)
    assert body["model"] == "anthropic/claude-haiku-5.5"
    assert "temperature" not in body and "seed" not in body  # with require_parameters they exclude endpoints
    assert body["provider"] == {"require_parameters": True}
    js = body["response_format"]["json_schema"]
    assert js["strict"] is True
    sent = js["schema"]
    assert sent["additionalProperties"] is False
    assert sent["properties"]["nested"]["additionalProperties"] is False
    assert sent["properties"]["items_list"]["items"]["additionalProperties"] is False
    assert "additionalProperties" not in schema  # caller's dict untouched


def test_json_fence_is_stripped(api_key, http):
    fenced = {"choices": [{"message": {"content": "```json\n" + json.dumps(ANSWER) + "\n```"}}]}
    http.queue = [io.BytesIO(json.dumps(fenced).encode())]
    assert OpenRouterLLM().complete_json("s", "u", SCHEMA) == ANSWER


def test_missing_key(monkeypatch, http):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(openrouter, "OPENROUTER_API_KEY", "")
    with pytest.raises(OpenRouterUnavailable) as exc:
        OpenRouterLLM().complete_json("s", "u", SCHEMA)
    assert "OPENROUTER_API_KEY" in str(exc.value) and "sk-test-secret" not in str(exc.value)
    assert http.requests == []


def test_401_rejected_key(api_key, http):
    http.queue = [_http_error(401)]
    with pytest.raises(OpenRouterUnavailable, match="rejected the API key"):
        OpenRouterLLM().complete_json("s", "u", SCHEMA)


def test_429_retries_once(api_key, http, monkeypatch):
    monkeypatch.setattr(openrouter.time, "sleep", lambda s: None)
    http.queue = [_http_error(429, "rate limited"), _payload(ANSWER)]
    assert OpenRouterLLM().complete_json("s", "u", SCHEMA) == ANSWER
    assert len(http.requests) == 2


def test_invalid_json_twice_raises_value_error(api_key, http):
    bad = io.BytesIO(json.dumps({"choices": [{"message": {"content": "not json"}}]}).encode())
    http.queue = [bad, io.BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())]
    with pytest.raises(ValueError):
        OpenRouterLLM().complete_json("s", "u", SCHEMA)
    assert len(http.requests) == 2


def test_complete_text_sends_no_schema_and_returns_raw_content(api_key, http):
    raw = {"choices": [{"message": {"content": "free text answer"}}]}
    http.queue = [io.BytesIO(json.dumps(raw).encode())]
    assert OpenRouterLLM().complete_text("sys", "user") == "free text answer"
    body = body_of(http.requests[0])
    assert "response_format" not in body and "provider" not in body


def test_identical_calls_hit_the_network_twice(api_key, http):
    llm = OpenRouterLLM()
    http.queue = [_payload(ANSWER), _payload(ANSWER)]
    llm.complete_json("s", "u", SCHEMA)
    llm.complete_json("s", "u", SCHEMA)
    assert len(http.requests) == 2


def test_load_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n\n"
        "OPENROUTER_API_KEY=sk-or-abc\n"
        "export EHDS_OPENROUTER_TIMEOUT=30\n"
        'QUOTED="with spaces"\n'
        "EXISTING=from-file\n"
    )
    for name in ("OPENROUTER_API_KEY", "EHDS_OPENROUTER_TIMEOUT", "QUOTED", "EXISTING"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EXISTING", "from-env")
    config.load_env_file(env)
    import os

    assert os.environ["OPENROUTER_API_KEY"] == "sk-or-abc"
    assert os.environ["EHDS_OPENROUTER_TIMEOUT"] == "30"
    assert os.environ["QUOTED"] == "with spaces"
    assert os.environ["EXISTING"] == "from-env"  # setdefault: real env wins
