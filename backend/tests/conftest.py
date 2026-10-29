import json

import pytest

from app import db
from app.config import REQUESTS_DIR
from app.models import DataRequest

REQUEST_IDS = sorted(p.stem for p in REQUESTS_DIR.glob("R*.json"))


def load_request(request_id: str) -> DataRequest:
    return DataRequest.model_validate_json((REQUESTS_DIR / f"{request_id}.json").read_text())


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
