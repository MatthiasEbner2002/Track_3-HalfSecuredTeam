import json
import os

import pytest

# Unit tests never call the extraction LLM (costs money, needs the network); test_llm_mapping fakes it.
os.environ.setdefault("EHDS_LLM", "off")

from app import db, ratelimit
from app.config import LAW_DB_PATH, REQUESTS_DIR
from app.layers import decision, routing
from app.models import DataRequest
from app.rag import ollama, openrouter
from app.rag.store import Retriever
from fakes import CHUNKS, FakeEmbedder, FakeLLM

REQUEST_IDS = sorted(p.stem for p in REQUESTS_DIR.glob("R*.json"))


def load_request(request_id: str) -> DataRequest:
    return DataRequest.model_validate_json((REQUESTS_DIR / f"{request_id}.json").read_text())


def pytest_configure(config):
    config.addinivalue_line("markers", "real_llm: needs Ollama running and data/law.db built")


def pytest_collection_modifyitems(config, items):
    if LAW_DB_PATH.exists() and ollama.is_available() and openrouter.is_configured():
        return
    skip = pytest.mark.skip(
        reason="needs Ollama (embeddings), data/law.db and OPENROUTER_API_KEY"
    )
    for item in items:
        if "real_llm" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def fake_retriever(tmp_path) -> Retriever:
    return Retriever.from_chunks(CHUNKS, FakeEmbedder(), tmp_path / "law.db")


@pytest.fixture(autouse=True)
def fake_decision_backend(request, monkeypatch, tmp_path_factory):
    """Unless a test is marked real_llm, the decision layer runs on fakes."""
    if "real_llm" in request.keywords:
        return
    retriever = Retriever.from_chunks(CHUNKS, FakeEmbedder(), tmp_path_factory.mktemp("law") / "law.db")
    monkeypatch.setattr(decision, "_default_retriever", lambda: retriever)
    monkeypatch.setattr(decision, "_default_llm", lambda: FakeLLM())


@pytest.fixture(autouse=True)
def fresh_rate_limiter(monkeypatch):
    """Every TestClient call comes from one address; a fresh, generous limiter keeps tests from blocking each other."""
    monkeypatch.setattr(ratelimit, "limiter", ratelimit.RateLimiter(limit=10_000))


@pytest.fixture(scope="session")
def conn():
    connection = db.connect()
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def expected() -> dict:
    return json.loads((REQUESTS_DIR / "expected.json").read_text())


@pytest.fixture
def r01() -> DataRequest:
    return load_request("R01")
