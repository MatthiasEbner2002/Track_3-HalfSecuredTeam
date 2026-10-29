"""Minimal Ollama client (stdlib only) for embeddings and JSON-constrained chat."""

import json
import urllib.request
from typing import Any

from app.config import EMBED_MODEL, LLM_MODEL, LLM_SEED, OLLAMA_URL


def _post(path: str, body: dict[str, Any], timeout: float = 120) -> dict[str, Any]:
    request = urllib.request.Request(
        OLLAMA_URL + path, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def is_available() -> bool:
    try:
        _post("/api/show", {"model": LLM_MODEL}, timeout=2)
        return True
    except OSError:
        return False


class OllamaEmbedder:
    """nomic-embed-text expects task prefixes on documents and queries."""

    def __init__(self, model: str = EMBED_MODEL):
        self.model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([f"search_document: {t}" for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([f"search_query: {text}"])[0]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return _post("/api/embed", {"model": self.model, "input": texts})["embeddings"]


class OllamaLLM:
    def __init__(self, model: str = LLM_MODEL):
        self.model = model

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return the model's answer parsed as JSON matching schema."""
        response = _post(
            "/api/chat",
            {
                "model": self.model,
                "stream": False,
                "format": schema,
                "options": {"temperature": 0, "seed": LLM_SEED},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        return json.loads(response["message"]["content"])
