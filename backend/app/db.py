"""SQLite database built from the five CSV files in data/.

Rebuild with:  python -m app.db
The resulting data/ehds.db is committed to git so nobody has to rebuild it.
"""

import csv
import sqlite3
from pathlib import Path
from typing import Any

from app.config import DATA_DIR, DB_PATH

# table -> (column definitions, indexed columns). Columns mirror the CSV headers.
SCHEMA: dict[str, tuple[list[tuple[str, str]], list[str]]] = {
    "patients": (
        [
            ("patient_id", "TEXT PRIMARY KEY"),
            ("sex", "TEXT"),
            ("birth_date", "TEXT"),
            ("region", "TEXT"),
            ("data_holder", "TEXT"),
            ("opted_out", "INTEGER"),  # 1 = opted out (Art. 71)
        ],
        ["region", "data_holder"],
    ),
    "conditions": (
        [("patient_id", "TEXT"), ("icd10", "TEXT"), ("description", "TEXT"), ("onset_date", "TEXT")],
        ["patient_id", "icd10"],
    ),
    "medications": (
        [
            ("patient_id", "TEXT"),
            ("atc", "TEXT"),
            ("name", "TEXT"),
            ("start_date", "TEXT"),
            ("end_date", "TEXT"),  # NULL = still active on the reference date
        ],
        ["patient_id", "atc"],
    ),
    "observations": (
        [
            ("patient_id", "TEXT"),
            ("loinc", "TEXT"),
            ("name", "TEXT"),
            ("value", "REAL"),
            ("unit", "TEXT"),
            ("date", "TEXT"),
        ],
        ["patient_id", "loinc"],
    ),
    "encounters": (
        [
            ("encounter_id", "TEXT PRIMARY KEY"),
            ("patient_id", "TEXT"),
            ("data_holder", "TEXT"),
            ("type", "TEXT"),
            ("start_date", "TEXT"),
            ("end_date", "TEXT"),
            ("primary_icd10", "TEXT"),
        ],
        ["patient_id", "type", "primary_icd10"],
    ),
}

TABLES = list(SCHEMA)


def _convert(column_type: str, raw: str) -> Any:
    if raw == "":
        return None
    if raw in ("true", "false") and column_type == "INTEGER":
        return 1 if raw == "true" else 0
    if column_type == "REAL":
        return float(raw)
    return raw


def build_db(data_dir: Path = DATA_DIR, db_path: Path = DB_PATH) -> Path:
    """Create db_path from scratch out of the CSV files in data_dir."""
    tmp_path = db_path.with_suffix(".db.tmp")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp_path)
    try:
        for table, (columns, indexes) in SCHEMA.items():
            col_sql = ", ".join(f"{name} {ctype}" for name, ctype in columns)
            conn.execute(f"CREATE TABLE {table} ({col_sql})")

            with open(data_dir / f"{table}.csv", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = [
                    tuple(_convert(ctype, row[name]) for name, ctype in columns)
                    for row in reader
                ]
            placeholders = ", ".join("?" for _ in columns)
            conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)

            for col in indexes:
                conn.execute(f"CREATE INDEX idx_{table}_{col} ON {table} ({col})")
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()

    tmp_path.replace(db_path)
    return db_path


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the database, building it first if it does not exist."""
    if not db_path.exists():
        build_db(db_path=db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def execute(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Run one query and return the rows as plain dicts."""
    return [dict(row) for row in conn.execute(sql, params)]


if __name__ == "__main__":
    path = build_db()
    print(f"Built {path}")
