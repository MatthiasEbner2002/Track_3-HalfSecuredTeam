"""SQLite database: load the five CSV files once at startup and keep the connection.

The database file lives at settings.data_dir / 'ehds.db' so it can be committed
to git alongside the CSV files (20 k synthetic patients, <10 MB total).

Tables (mirrors the CSV columns exactly):
  patients    – patient_id, sex, birth_date, region, data_holder, opted_out
  conditions  – patient_id, icd10, description, onset_date
  medications – patient_id, atc, name, start_date, end_date
  observations– patient_id, loinc, name, value, unit, date
  encounters  – encounter_id, patient_id, data_holder, type, start_date, end_date, primary_icd10
  results     – request_id (PK), payload TEXT (JSON)

opted_out is stored as INTEGER (1/0) for easy filtering in SQL.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("Database not initialised – call init_db() first")
    return _conn


def init_db(data_dir: Path, db_path: Path | None = None) -> sqlite3.Connection:
    """Load CSVs into SQLite and return the connection.

    If db_path already exists and is newer than all CSVs, the existing file is
    reused so startup is fast on subsequent runs.
    """
    global _conn

    if db_path is None:
        db_path = data_dir / "ehds.db"

    csvs = {
        "patients": data_dir / "patients.csv",
        "conditions": data_dir / "conditions.csv",
        "medications": data_dir / "medications.csv",
        "observations": data_dir / "observations.csv",
        "encounters": data_dir / "encounters.csv",
    }

    # Reuse existing DB if it is up to date.
    if db_path.exists():
        db_mtime = db_path.stat().st_mtime
        if all(p.stat().st_mtime <= db_mtime for p in csvs.values()):
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            _conn = conn
            _ensure_results_table(conn)
            return conn

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row

    _load_csvs(conn, csvs)
    _ensure_results_table(conn)

    _conn = conn
    return conn


def _load_csvs(conn: sqlite3.Connection, csvs: dict[str, Path]) -> None:
    """Drop and recreate all data tables from the CSV files."""
    dtypes_patients = {
        "patient_id": str,
        "sex": str,
        "birth_date": str,
        "region": str,
        "data_holder": str,
        "opted_out": str,
    }
    df_patients = pd.read_csv(csvs["patients"], dtype=dtypes_patients)
    # Convert opted_out to integer for SQL filtering
    df_patients["opted_out"] = df_patients["opted_out"].str.lower().map(
        {"true": 1, "false": 0}
    )
    df_patients.to_sql("patients", conn, if_exists="replace", index=False)

    dtypes_conditions = {
        "patient_id": str,
        "icd10": str,
        "description": str,
        "onset_date": str,
    }
    df_conditions = pd.read_csv(csvs["conditions"], dtype=dtypes_conditions)
    df_conditions.to_sql("conditions", conn, if_exists="replace", index=False)

    dtypes_meds = {
        "patient_id": str,
        "atc": str,
        "name": str,
        "start_date": str,
        "end_date": str,
    }
    df_meds = pd.read_csv(csvs["medications"], dtype=dtypes_meds)
    df_meds.to_sql("medications", conn, if_exists="replace", index=False)

    dtypes_obs = {
        "patient_id": str,
        "loinc": str,
        "name": str,
        "value": float,
        "unit": str,
        "date": str,
    }
    df_obs = pd.read_csv(csvs["observations"], dtype=dtypes_obs)
    df_obs.to_sql("observations", conn, if_exists="replace", index=False)

    dtypes_enc = {
        "encounter_id": str,
        "patient_id": str,
        "data_holder": str,
        "type": str,
        "start_date": str,
        "end_date": str,
        "primary_icd10": str,
    }
    df_enc = pd.read_csv(csvs["encounters"], dtype=dtypes_enc)
    df_enc.to_sql("encounters", conn, if_exists="replace", index=False)

    # Indexes for the most common join columns
    cur = conn.cursor()
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_patients_pid ON patients(patient_id)",
        "CREATE INDEX IF NOT EXISTS idx_conditions_pid ON conditions(patient_id)",
        "CREATE INDEX IF NOT EXISTS idx_conditions_icd ON conditions(icd10)",
        "CREATE INDEX IF NOT EXISTS idx_medications_pid ON medications(patient_id)",
        "CREATE INDEX IF NOT EXISTS idx_medications_atc ON medications(atc)",
        "CREATE INDEX IF NOT EXISTS idx_observations_pid ON observations(patient_id)",
        "CREATE INDEX IF NOT EXISTS idx_observations_loinc ON observations(loinc)",
        "CREATE INDEX IF NOT EXISTS idx_encounters_pid ON encounters(patient_id)",
    ]:
        cur.execute(idx_sql)
    conn.commit()


def _ensure_results_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS results (
            request_id TEXT PRIMARY KEY,
            payload    TEXT NOT NULL
        )
        """
    )
    conn.commit()


def store_result(request_id: str, payload: dict[str, Any]) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO results (request_id, payload) VALUES (?, ?)",
        (request_id, json.dumps(payload)),
    )
    conn.commit()


def load_result(request_id: str) -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT payload FROM results WHERE request_id = ?", (request_id,)
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload"])  # type: ignore[index]


def load_all_results() -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute("SELECT payload FROM results ORDER BY request_id").fetchall()
    return [json.loads(r["payload"]) for r in rows]
