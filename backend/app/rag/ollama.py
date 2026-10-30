"""Minimal Ollama client (stdlib only) for local embeddings (nomic-embed-text)."""

import json
import urllib.error
import urllib.request
from typing import Any

from app.config import EMBED_MODEL, OLLAMA_URL
from app.rag.errors import ServiceUnavailable


class OllamaUnavailable(ServiceUnavailable):
    """Ollama is down, unreachable, or the embedding model is not pulled."""


def _post(path: str, body: dict[str, Any], timeout: float = 120) -> dict[str, Any]:
    request = urllib.request.Request(
        OLLAMA_URL + path, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            model = body.get("model", EMBED_MODEL)
            raise OllamaUnavailable(
                f"model {model!r} is not available in Ollama at {OLLAMA_URL} "
                f"(pull it with `ollama pull {model}`)"
            ) from e
        raise
    except (urllib.error.URLError, OSError) as e:
        raise OllamaUnavailable(
            f"Ollama is not reachable at {OLLAMA_URL} (start it with `ollama serve`)"
        ) from e


def is_available() -> bool:
    try:
        _post("/api/show", {"model": EMBED_MODEL}, timeout=2)
        return True
    except (OSError, OllamaUnavailable):
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
