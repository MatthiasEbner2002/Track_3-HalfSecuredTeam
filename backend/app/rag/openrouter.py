"""OpenRouter chat client (stdlib only) for JSON-schema-constrained answers.

Only request text is sent to OpenRouter; patient data never leaves the app.
Answers are cached on disk keyed by (model, prompts, schema), so an identical
request gives the identical decision across restarts and costs nothing twice.
"""

import copy
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.config import (
    LLM_MODEL,
    LLM_TIMEOUT_S,
    OPENROUTER_API_KEY,
    OPENROUTER_URL,
    REPO_ROOT,
)
from app.rag.errors import ServiceUnavailable

CACHE_DIR = REPO_ROOT / "backend" / ".cache" / "openrouter"


class OpenRouterUnavailable(ServiceUnavailable):
    """OpenRouter is unreachable, the key is missing/rejected, or the call fails."""


def is_configured() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY") or OPENROUTER_API_KEY)


def _strict_schema(schema: dict) -> dict:
    """Deep copy with additionalProperties: false on every object schema."""
    strict = copy.deepcopy(schema)

    def fix(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" or "properties" in node:
            node.setdefault("additionalProperties", False)
        for value in node.get("properties", {}).values():
            fix(value)
        fix(node.get("items"))

    fix(strict)
    return strict


def _server_message(error: urllib.error.HTTPError) -> str:
    """The API's own error message, truncated; never contains the key."""
    try:
        body = json.loads(error.read())
        return str(body.get("error", {}).get("message", ""))[:200]
    except Exception:
        return str(getattr(error, "reason", "") or "")[:200]


def _parse_answer(content: str, schema: dict) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text.strip())
    answer = json.loads(text)
    missing = [key for key in schema.get("required", []) if key not in answer]
    if missing:
        raise ValueError(f"answer is missing required keys: {missing}")
    return answer


class OpenRouterLLM:
    def __init__(
        self,
        model: str = LLM_MODEL,
        api_key: str | None = None,
        cache_dir: Path | None = CACHE_DIR,
    ):
        self.model = model
        self.api_key = api_key
        self.cache_dir = cache_dir

    def _key(self) -> str:
        key = self.api_key or os.environ.get("OPENROUTER_API_KEY") or OPENROUTER_API_KEY
        if not key:
            raise OpenRouterUnavailable(
                "OPENROUTER_API_KEY is not set (put it in backend/.env)"
            )
        return key

    def _http(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{OPENROUTER_URL}/chat/completions",
            json.dumps(body).encode(),
            {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/MatthiasEbner2002/Track_3-HalfSecuredTeam",
                "X-Title": "EHDS Health Data Access Body",
            },
        )
        with urllib.request.urlopen(request, timeout=LLM_TIMEOUT_S) as response:
            return json.load(response)

    def _call(self, key: str, system: str, user: str, schema: dict) -> dict:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "answer", "strict": True, "schema": schema},
            },
            "provider": {"require_parameters": True},
        }
        for attempt in range(2):
            try:
                payload = self._http(key, body)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    raise OpenRouterUnavailable(
                        f"OpenRouter rejected the API key (HTTP {e.code})"
                    ) from e
                if e.code == 402:
                    raise OpenRouterUnavailable("OpenRouter is out of credits (HTTP 402)") from e
                if (e.code == 429 or e.code >= 500) and attempt == 0:
                    time.sleep(2)
                    continue
                raise OpenRouterUnavailable(
                    f"OpenRouter returned HTTP {e.code}: {_server_message(e)}"
                ) from e
            except (urllib.error.URLError, OSError) as e:
                raise OpenRouterUnavailable(
                    f"OpenRouter is not reachable at {OPENROUTER_URL}"
                ) from e
            try:
                return _parse_answer(payload["choices"][0]["message"]["content"], schema)
            except (ValueError, KeyError, TypeError) as e:
                if attempt == 0:
                    continue
                raise ValueError(
                    "OpenRouter returned an answer that is not valid JSON for the schema"
                ) from e
        raise ValueError("unreachable")

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        """Return the model's answer parsed as JSON matching schema."""
        key = self._key()
        strict = _strict_schema(schema)
        cache_key = hashlib.sha256(
            json.dumps(
                {"model": self.model, "system": system, "user": user, "schema": strict},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if self.cache_dir is not None:
            cached = self.cache_dir / f"{cache_key}.json"
            if cached.exists():
                return json.loads(cached.read_text(encoding="utf-8"))

        answer = self._call(key, system, user, strict)

        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{cache_key}.json").write_text(
                json.dumps(answer), encoding="utf-8"
            )
        return answer
