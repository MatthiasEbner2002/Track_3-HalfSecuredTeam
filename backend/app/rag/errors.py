"""Shared error for the external services the pipeline depends on."""


class ServiceUnavailable(RuntimeError):
    """A backing service (Ollama embeddings, OpenRouter chat) is unreachable or misconfigured."""
