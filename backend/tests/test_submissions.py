import json

from fastapi.testclient import TestClient

from app.config import REQUESTS_DIR
from app.main import app

client = TestClient(app)


def test_submit_list_view_download():
    body = json.loads((REQUESTS_DIR / "R01.json").read_text())
    body["id"] = "T-SUB-1"
    response = client.post("/api/submissions", json=body)
    assert response.status_code == 200
    assert response.json()["result"]["flags"] == ["placeholder"]

    assert client.post("/api/submissions", json=body).status_code == 409
    assert "T-SUB-1" in [s["request"]["id"] for s in client.get("/api/submissions").json()]
    assert client.get("/api/submissions/T-SUB-1").json()["request"]["question"] == body["question"]

    download = client.get("/api/submissions/T-SUB-1/download?part=request")
    assert download.headers["content-disposition"] == 'attachment; filename="T-SUB-1.json"'
    assert download.json()["definitions"] == body["definitions"]


def test_empty_id_is_assigned_and_access_application_gets_spe():
    body = json.loads((REQUESTS_DIR / "R05.json").read_text())
    body["id"] = ""
    result = client.post("/api/submissions", json=body).json()
    assert result["request"]["id"].startswith("S")
    assert result["result"]["decision"] == "APPROVE_SPE"
