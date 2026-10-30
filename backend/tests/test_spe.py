"""The secure processing environment: only the cohort, only the named tables, no way out but the airlock."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config, pipeline, spe
from app.layers import routing
from app.main import app

from conftest import load_request

client = TestClient(app)


@pytest.fixture(autouse=True)
def spe_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SPE_DIR", tmp_path / "spe")
    return tmp_path / "spe"


@pytest.fixture
def r05(conn):
    """R05 run through the pipeline: creates <SPE_DIR>/R05.db."""
    choice = routing.RouteChoice(route="spe", articles=["66", "68", "73"], reason="spe", grounding=[], evidence=[], flags=[])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(routing, "choose", lambda req: choice)
        return pipeline.run(load_request("R05"), conn)


def test_pipeline_creates_spe_with_exact_cohort(r05, spe_dir, expected):
    assert r05.decision == "APPROVE_SPE"
    assert r05.data["cohort_size"] == expected["R05"]["answer"]["cohort_size"] == 1152
    assert r05.data["spe"] == {"id": "R05", "url": "/spe/R05", "tables": r05.data["spe"]["tables"]}
    assert set(r05.data["spe"]["tables"]) == {"conditions", "medications", "observations"}
    assert (spe_dir / "R05.db").is_file()


def test_spe_holds_only_named_tables_and_selected_patients(r05, spe_dir, conn):
    s = sqlite3.connect(spe_dir / "R05.db")
    tables = {t for (t,) in s.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"cohort", "conditions", "medications", "observations", "manifest", "audit_log", "outputs"}
    assert s.execute("SELECT COUNT(*) FROM cohort").fetchone()[0] == 1152
    for t in ("conditions", "medications", "observations"):
        stray = s.execute(f"SELECT COUNT(*) FROM {t} WHERE patient_id NOT IN (SELECT patient_id FROM cohort)").fetchone()[0]
        assert stray == 0, t
    # patient IDs are kept (pseudonymised, not anonymised) and opted-out patients are not inside
    ids = [p for (p,) in s.execute("SELECT patient_id FROM cohort")]
    assert all(spe.PATIENT_ID.match(p) for p in ids)
    opted = conn.execute(
        f"SELECT COUNT(*) FROM patients WHERE opted_out = 1 AND patient_id IN ({','.join('?' * len(ids))})", ids
    ).fetchone()[0]
    assert opted == 0


def test_rebuild_is_idempotent(r05, conn):
    before = spe.info("R05")["tables"]
    choice = routing.RouteChoice(route="spe", articles=["66", "68", "73"], reason="spe", grounding=[], evidence=[], flags=[])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(routing, "choose", lambda req: choice)
        again = pipeline.run(load_request("R05"), conn)
    assert again.data["spe"]["tables"] == before
    assert spe.info("R05")["audit_entries"] == 1  # a rebuild starts a fresh log


def test_download_is_refused_and_logged(r05):
    for url in ("/api/spe/R05/download", "/api/spe/R05/download/conditions.csv"):
        res = client.get(url)
        assert res.status_code == 403
        assert "Art. 73" in res.json()["detail"]
    actions = [a["action"] for a in client.get("/api/spe/R05/audit").json()]
    assert actions.count("download_blocked") == 2
    # the file itself has no URL
    assert client.get("/spe/R05.db").status_code == 404
    assert client.get("/api/spe/R05.db").status_code == 404


def test_query_is_read_only_and_capped(r05):
    res = client.post("/api/spe/R05/query", json={"sql": "SELECT * FROM observations"})
    assert res.status_code == 200
    body = res.json()
    assert len(body["rows"]) == config.SPE_PAGE_ROWS and body["truncated"] is True
    assert body["columns"][0] == "patient_id"  # pseudonyms are visible inside the SPE

    agg = client.post("/api/spe/R05/query", json={"sql": "SELECT icd10, COUNT(*) AS n FROM conditions GROUP BY icd10 ORDER BY n DESC LIMIT 3"}).json()
    assert agg["rows"][0]["icd10"].startswith("E11") and not agg["truncated"]

    for sql in ("ATTACH 'x' AS y", "PRAGMA table_info(conditions)", "INSERT INTO cohort VALUES ('p-00000000')",
                "DROP TABLE cohort", "SELECT * FROM audit_log", "SELECT 1; SELECT 2"):
        assert client.post("/api/spe/R05/query", json={"sql": sql}).status_code == 400, sql
    assert sqlite3.connect(spe.path("R05")).execute("SELECT COUNT(*) FROM cohort").fetchone()[0] == 1152


def test_runaway_query_is_interrupted(r05):
    sql = "WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c) SELECT COUNT(*) FROM c"
    res = client.post("/api/spe/R05/query", json={"sql": sql})
    assert res.status_code == 400 and "interrupted" in res.json()["detail"]


def test_airlock_blocks_records_and_small_cells_and_passes_aggregates(r05):
    records = client.post("/api/spe/R05/query", json={"sql": "SELECT * FROM conditions LIMIT 5"}).json()["rows"]
    refused = client.post("/api/spe/R05/outputs", json={"name": "raw", "rows": records}).json()
    assert refused["status"] == "refused"
    assert any("patient IDs" in c for c in refused["checks"]) and any("record-level" in c for c in refused["checks"])

    small = client.post("/api/spe/R05/outputs", json={"name": "small", "rows": [{"icd10": "I10", "n": 517}, {"icd10": "Z99", "n": 3}]}).json()
    assert small["status"] == "refused" and "below 10" in small["checks"][0]

    ok = client.post("/api/spe/R05/outputs", json={"name": "by_icd", "rows": [{"icd10": "E11.9", "n": 869}]}).json()
    assert ok["status"] == "pending" and ok["checks"] == []
    # the applicant sees no content until staff approve
    assert "content" not in client.get(f"/api/spe/R05/outputs/{ok['id']}").json()
    assert client.get(f"/api/spe/R05/outputs/{ok['id']}", headers={"X-Role": "staff"}).json()["content"] == [{"icd10": "E11.9", "n": 869}]
    approved = client.post(f"/api/spe/R05/outputs/{ok['id']}/review", json={"status": "approved", "note": "aggregate"}).json()
    assert approved["status"] == "approved"
    assert client.get(f"/api/spe/R05/outputs/{ok['id']}").json()["content"] == [{"icd10": "E11.9", "n": 869}]
    assert client.post(f"/api/spe/R05/outputs/{ok['id']}/review", json={"status": "rejected"}).status_code == 409


def test_text_output_with_patient_ids_is_refused(r05):
    pid = client.post("/api/spe/R05/query", json={"sql": "SELECT patient_id FROM cohort LIMIT 1"}).json()["rows"][0]["patient_id"]
    leak = client.post("/api/spe/R05/outputs", json={"name": "notes", "content": f"worst case was {pid}"}).json()
    assert leak["status"] == "refused"
    fine = client.post("/api/spe/R05/outputs", json={"name": "metrics", "content": '{"auc": 0.81}'}).json()
    assert fine["status"] == "pending"


def test_info_and_page(r05):
    info = client.get("/api/spe/R05").json()
    assert info["cohort_size"] == 1152 and info["articles"] == ["53", "66", "68", "73"]
    assert info["tables"]["conditions"] > 0 and "patients" not in info["tables"]
    assert client.get("/spe/R05").status_code == 200
    assert client.get("/spe/NOPE").status_code == 404
    assert client.get("/api/spe/NOPE").status_code == 404
