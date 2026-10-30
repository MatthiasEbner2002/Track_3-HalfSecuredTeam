from fastapi.testclient import TestClient

from app.config import REQUESTS_DIR
from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_submit_request():
    body = (REQUESTS_DIR / "R01.json").read_text()
    response = client.post("/api/requests", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 200
    assert response.json()["request_id"] == "R01"


def test_sql_and_mapping_only_with_debug():
    body = (REQUESTS_DIR / "R01.json").read_text()
    plain = client.post("/api/requests", content=body, headers={"Content-Type": "application/json"}).json()
    assert plain["queries"] == [] and "mapping" not in plain and plain["data"]
    debug = client.post("/api/requests?debug=1", content=body, headers={"Content-Type": "application/json"}).json()
    assert debug["queries"] and "SELECT" in debug["queries"][0]["sql"] and debug["mapping"]["terms"]


def test_invalid_request_is_rejected_by_validation():
    response = client.post("/api/requests", json={"id": "X"})
    assert response.status_code == 422
