"""Secure processing environment (SPE), one per APPROVE_SPE request (Art. 73).

Provisioning copies only the selected patients and the record types the request
names into a separate SQLite file under settings.spe_dir/<request_id>.db,
keeping their patient IDs. Downloads are always blocked.
Every access and blocked download is audit-logged in the SPE database itself.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


def _spe_db_path(request_id: str) -> Path:
    spe_dir = settings.spe_dir
    spe_dir.mkdir(parents=True, exist_ok=True)
    return spe_dir / f"{request_id}.db"


def provision(
    request_id: str,
    patient_ids: list[str],
    record_types: list[str],
    source_conn: sqlite3.Connection,
) -> Path:
    """Create the SPE for a request and copy the selected records into it.

    Args:
        request_id: The request ID, used as the SPE folder/file name.
        patient_ids: Patient IDs to copy (opted-out already excluded).
        record_types: Which tables to copy: "conditions", "medications",
                      "observations", "encounters".
        source_conn: The main SQLite connection to read from.

    Returns:
        Path to the SPE SQLite file.
    """
    db_path = _spe_db_path(request_id)
    spe_conn = sqlite3.connect(str(db_path))

    # Copy the patients subset (always)
    _copy_patients(spe_conn, source_conn, patient_ids)

    # Copy the requested record types
    _TABLE_COPY = {
        "conditions": _copy_conditions,
        "medications": _copy_medications,
        "observations": _copy_observations,
        "encounters": _copy_encounters,
    }
    for rt in record_types:
        if rt in _TABLE_COPY:
            _TABLE_COPY[rt](spe_conn, source_conn, patient_ids)

    # Create an audit log table
    spe_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            ts       TEXT NOT NULL,
            event    TEXT NOT NULL,
            detail   TEXT
        )
        """
    )
    spe_conn.commit()

    audit(request_id, "provisioned", f"{len(patient_ids)} patients, tables: {record_types}")
    spe_conn.close()
    return db_path


def _copy_patients(spe: sqlite3.Connection, src: sqlite3.Connection, ids: list[str]) -> None:
    placeholders = ",".join("?" * len(ids))
    rows = src.execute(
        f"SELECT * FROM patients WHERE patient_id IN ({placeholders})", ids
    ).fetchall()
    spe.execute(
        """
        CREATE TABLE IF NOT EXISTS patients (
            patient_id TEXT, sex TEXT, birth_date TEXT, region TEXT,
            data_holder TEXT, opted_out INTEGER
        )
        """
    )
    spe.executemany("INSERT INTO patients VALUES (?,?,?,?,?,?)", rows)
    spe.commit()


def _copy_conditions(spe: sqlite3.Connection, src: sqlite3.Connection, ids: list[str]) -> None:
    placeholders = ",".join("?" * len(ids))
    rows = src.execute(
        f"SELECT * FROM conditions WHERE patient_id IN ({placeholders})", ids
    ).fetchall()
    spe.execute(
        """
        CREATE TABLE IF NOT EXISTS conditions (
            patient_id TEXT, icd10 TEXT, description TEXT, onset_date TEXT
        )
        """
    )
    spe.executemany("INSERT INTO conditions VALUES (?,?,?,?)", rows)
    spe.commit()


def _copy_medications(spe: sqlite3.Connection, src: sqlite3.Connection, ids: list[str]) -> None:
    placeholders = ",".join("?" * len(ids))
    rows = src.execute(
        f"SELECT * FROM medications WHERE patient_id IN ({placeholders})", ids
    ).fetchall()
    spe.execute(
        """
        CREATE TABLE IF NOT EXISTS medications (
            patient_id TEXT, atc TEXT, name TEXT, start_date TEXT, end_date TEXT
        )
        """
    )
    spe.executemany("INSERT INTO medications VALUES (?,?,?,?,?)", rows)
    spe.commit()


def _copy_observations(spe: sqlite3.Connection, src: sqlite3.Connection, ids: list[str]) -> None:
    placeholders = ",".join("?" * len(ids))
    rows = src.execute(
        f"SELECT * FROM observations WHERE patient_id IN ({placeholders})", ids
    ).fetchall()
    spe.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
            patient_id TEXT, loinc TEXT, name TEXT, value REAL, unit TEXT, date TEXT
        )
        """
    )
    spe.executemany("INSERT INTO observations VALUES (?,?,?,?,?,?)", rows)
    spe.commit()


def _copy_encounters(spe: sqlite3.Connection, src: sqlite3.Connection, ids: list[str]) -> None:
    placeholders = ",".join("?" * len(ids))
    rows = src.execute(
        f"SELECT * FROM encounters WHERE patient_id IN ({placeholders})", ids
    ).fetchall()
    spe.execute(
        """
        CREATE TABLE IF NOT EXISTS encounters (
            encounter_id TEXT, patient_id TEXT, data_holder TEXT,
            type TEXT, start_date TEXT, end_date TEXT, primary_icd10 TEXT
        )
        """
    )
    spe.executemany("INSERT INTO encounters VALUES (?,?,?,?,?,?,?)", rows)
    spe.commit()


def describe(request_id: str) -> dict[str, Any]:
    """Return the SPE's record types, exact patient count and audit log."""
    db_path = _spe_db_path(request_id)
    if not db_path.exists():
        return {"error": f"SPE for {request_id} not found"}

    conn = sqlite3.connect(str(db_path))
    tables: list[str] = []
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name != 'audit_log'"
    ).fetchall():
        tables.append(row[0])

    patient_count = 0
    if "patients" in tables:
        r = conn.execute("SELECT COUNT(*) FROM patients").fetchone()
        patient_count = r[0] if r else 0

    logs = conn.execute(
        "SELECT ts, event, detail FROM audit_log ORDER BY ts"
    ).fetchall()
    audit_log = [{"ts": r[0], "event": r[1], "detail": r[2]} for r in logs]
    conn.close()

    audit(request_id, "described", "")

    return {
        "request_id": request_id,
        "patient_count": patient_count,
        "record_types": [t for t in tables if t != "patients"],
        "audit_log": audit_log,
        "download_blocked": True,
        "reason": "Art. 73: data never leaves the SPE.",
    }


def block_download(request_id: str) -> None:
    """Log a download attempt; the API then answers 403 citing Art. 73."""
    audit(request_id, "download_blocked", "Blocked by Art. 73: data cannot leave the SPE.")


def audit(request_id: str, event: str, detail: str = "") -> None:
    """Append an event to the SPE's audit log."""
    db_path = _spe_db_path(request_id)
    if not db_path.exists():
        return
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_log (ts TEXT, event TEXT, detail TEXT)"
    )
    conn.execute(
        "INSERT INTO audit_log (ts, event, detail) VALUES (?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), event, detail),
    )
    conn.commit()
    conn.close()
