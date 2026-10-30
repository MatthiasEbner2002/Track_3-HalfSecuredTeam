"""The secure processing environment: key + name in, only the cohort and named tables inside, images out, nothing else."""

import base64
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, pipeline, spe
from app.layers import routing
from app.main import app

from conftest import load_request

client = TestClient(app)


@pytest.fixture(autouse=True)
def spe_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SPE_DIR", tmp_path / "spe")
    return tmp_path / "spe"


def _run_r05(conn):
    choice = routing.RouteChoice(route="spe", articles=["66", "68", "73"], reason="spe", grounding=[], evidence=[], flags=[])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(routing, "choose", lambda req: choice)
        return pipeline.run(load_request("R05"), conn)


@pytest.fixture
def r05(conn):
    """R05 run through the pipeline: creates <SPE_DIR>/R05.db and returns the result."""
    return _run_r05(conn)


NAME = quote("Dr. Amir Hadžić")  # R05's applicant, percent-encoded as the frontend sends it


@pytest.fixture
def auth(r05):
    return {"Authorization": f"Bearer {r05.data['spe']['access_key']}", "X-Applicant": NAME}


def test_pipeline_creates_spe_and_returns_the_key_once(r05, spe_dir, expected):
    assert r05.decision == "APPROVE_SPE"
    assert r05.data["cohort_size"] == expected["R05"]["answer"]["cohort_size"] == 1152
    s = r05.data["spe"]
    assert s["id"] == "R05" and set(s["tables"]) == {"conditions", "medications", "observations"}
    assert s["access_key"].startswith("R05.") and len(s["access_key"]) > 20
    assert s["url"] == config.SPE_FRONTEND_URL
    assert (spe_dir / "R05.db").is_file()
    assert s["access_key"] not in (spe_dir / "R05.db").read_bytes().decode("latin1")  # only the hash is stored


def test_spe_holds_only_named_tables_and_selected_patients(r05, spe_dir, conn):
    s = sqlite3.connect(spe_dir / "R05.db")
    tables = {t for (t,) in s.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"cohort", "conditions", "medications", "observations", "manifest", "audit_log"}
    assert s.execute("SELECT COUNT(*) FROM cohort").fetchone()[0] == 1152
    for t in ("conditions", "medications", "observations"):
        stray = s.execute(f"SELECT COUNT(*) FROM {t} WHERE patient_id NOT IN (SELECT patient_id FROM cohort)").fetchone()[0]
        assert stray == 0, t
    ids = [p for (p,) in s.execute("SELECT patient_id FROM cohort")]
    assert all(spe.PATIENT_ID.match(p) for p in ids)  # pseudonyms kept
    opted = conn.execute(
        f"SELECT COUNT(*) FROM patients WHERE opted_out = 1 AND patient_id IN ({','.join('?' * len(ids))})", ids
    ).fetchone()[0]
    assert opted == 0


def test_rebuild_rotates_the_key(r05, conn):
    old = r05.data["spe"]["access_key"]
    again = _run_r05(conn)
    assert again.data["spe"]["tables"] == r05.data["spe"]["tables"]
    assert again.data["spe"]["access_key"] != old
    assert client.post("/api/spe/access", headers={"Authorization": f"Bearer {old}", "X-Applicant": NAME}).status_code == 401


def test_key_expires_after_one_day(r05, auth, spe_dir):
    info = client.post("/api/spe/access", headers=auth).json()
    issued, expires = datetime.fromisoformat(info["created"]), datetime.fromisoformat(info["key_expires"])
    assert expires - issued == timedelta(hours=config.SPE_KEY_TTL_HOURS) == timedelta(days=1)
    assert r05.data["spe"]["key_expires"] == info["key_expires"]
    # same key, many uses: not one-time
    for _ in range(3):
        assert client.post("/api/spe/access", headers=auth).status_code == 200
    # move the expiry into the past
    s = sqlite3.connect(spe_dir / "R05.db")
    manifest = json.loads(s.execute("SELECT json FROM manifest").fetchone()[0])
    manifest["key_expires"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    s.execute("UPDATE manifest SET json = ?", (json.dumps(manifest),)); s.commit(); s.close()
    res = client.post("/api/spe/access", headers=auth)
    assert res.status_code == 401 and "expired" in res.json()["detail"]
    assert client.post("/api/spe/query", json={"sql": "SELECT 1"}, headers=auth).status_code == 401
    assert [a["action"] for a in spe.audit_log("R05")].count("access_expired") == 2


def test_key_and_name_are_both_required(r05, auth):
    key = auth["Authorization"]
    assert client.post("/api/spe/access").status_code == 401
    assert client.post("/api/spe/access", headers={"Authorization": key}).status_code == 401  # key without name
    assert client.post("/api/spe/access", headers={"X-Applicant": NAME}).status_code == 401  # name without key
    assert client.post("/api/spe/access", headers={"Authorization": key, "X-Applicant": "Dr. Someone Else"}).status_code == 401
    assert client.post("/api/spe/access", headers={"Authorization": "Bearer nonsense", "X-Applicant": NAME}).status_code == 401
    assert client.post("/api/spe/access", headers={"Authorization": "Bearer R05.wrongsecret", "X-Applicant": NAME}).status_code == 401
    assert client.post("/api/spe/access", headers={"Authorization": "Bearer NOPE.secret", "X-Applicant": NAME}).status_code == 401
    # case and spacing of the name do not matter
    assert client.post("/api/spe/access", headers={"Authorization": key, "X-Applicant": quote("  dr.  amir HADŽIĆ ")}).status_code == 200
    info = client.post("/api/spe/access", headers=auth).json()
    assert info["cohort_size"] == 1152 and info["articles"] == ["53", "66", "68", "73"]
    assert "key_hash" not in info and "patients" not in info["tables"]
    assert info["columns"]["conditions"] == ["patient_id", "icd10", "description", "onset_date"]
    rejected = [a["detail"] for a in spe.audit_log("R05") if a["action"] == "access_rejected"]
    assert len(rejected) == 2  # wrong name, wrong secret; "nonsense" never names an SPE, so nothing to log it in
    assert {"key_ok": True, "name_ok": False, "name_given": "Dr. Someone Else"} in rejected


def test_download_is_refused_and_logged(r05, auth):
    for url in ("/api/spe/download", "/api/spe/download/conditions.csv"):
        res = client.get(url, headers=auth)
        assert res.status_code == 403
        assert "Art. 73" in res.json()["detail"]
    assert client.get("/api/spe/download").status_code == 401
    assert [a["action"] for a in spe.audit_log("R05")].count("download_blocked") == 2
    assert client.get("/spe/R05.db").status_code == 404  # the file has no URL
    assert client.get("/spe/R05").status_code == 404  # no server-side page either


def test_query_returns_an_image_not_rows(r05, auth):
    res = client.post("/api/spe/query", json={"sql": "SELECT * FROM observations"}, headers=auth)
    assert res.status_code == 200
    body = res.json()
    assert "rows" not in body and body["rows_shown"] == config.SPE_PAGE_ROWS and body["truncated"] is True
    assert body["columns"][0] == "patient_id"
    assert body["image"].startswith("data:image/png;base64,")
    img = Image.open(io.BytesIO(base64.b64decode(body["image"].split(",", 1)[1])))
    assert img.format == "PNG" and img.height > 50 * 15
    assert "p-" not in res.text.split('"image"')[0]  # no patient id anywhere in the JSON

    agg = client.post("/api/spe/query", json={"sql": "SELECT icd10, COUNT(*) AS n FROM conditions GROUP BY icd10 ORDER BY n DESC LIMIT 3"}, headers=auth).json()
    assert agg["rows_shown"] == 3 and not agg["truncated"]


def test_query_is_read_only(r05, auth):
    for sql in ("ATTACH 'x' AS y", "PRAGMA table_info(conditions)", "INSERT INTO cohort VALUES ('p-00000000')",
                "DROP TABLE cohort", "SELECT * FROM audit_log", "SELECT * FROM manifest", "SELECT 1; SELECT 2"):
        assert client.post("/api/spe/query", json={"sql": sql}, headers=auth).status_code == 400, sql
    assert sqlite3.connect(spe.path("R05")).execute("SELECT COUNT(*) FROM cohort").fetchone()[0] == 1152


def test_runaway_query_is_interrupted(r05, auth):
    sql = "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c) SELECT COUNT(*) FROM c"
    res = client.post("/api/spe/query", json={"sql": sql}, headers=auth)
    assert res.status_code == 400 and "interrupted" in res.json()["detail"]


def test_no_endpoint_returns_rows(r05, auth):
    """Besides the image, nothing under /api/spe hands out data."""
    for method, url in (("get", "/api/spe/outputs"), ("post", "/api/spe/outputs"), ("get", "/api/spe/rows"), ("get", "/api/spe/export")):
        assert getattr(client, method)(url, headers=auth).status_code in (404, 405), url


def test_render_png_handles_empty_and_odd_values():
    png = spe.render_png(["a", "b"], [], "mark")
    assert Image.open(io.BytesIO(png)).format == "PNG"
    png = spe.render_png(["x"], [{"x": None}, {"x": 1.23456789}, {"x": "y" * 100}], "mark")
    assert Image.open(io.BytesIO(png)).width < 800  # long cells are cut
