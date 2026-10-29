import csv

import pytest

from app import db
from app.config import DATA_DIR


def _csv_rows(table: str) -> int:
    with open(DATA_DIR / f"{table}.csv", newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))


@pytest.mark.parametrize("table", db.TABLES)
def test_row_counts_match_csv(conn, table):
    (count,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert count == _csv_rows(table)


def test_opted_out_is_integer_flag(conn):
    (count,) = conn.execute("SELECT COUNT(*) FROM patients WHERE opted_out = 1").fetchone()
    assert count == 839  # stated in data/README.md


def test_empty_values_become_null(conn):
    (active,) = conn.execute("SELECT COUNT(*) FROM medications WHERE end_date IS NULL").fetchone()
    (empty,) = conn.execute("SELECT COUNT(*) FROM medications WHERE end_date = ''").fetchone()
    assert active > 0 and empty == 0


def test_observation_values_are_numeric(conn):
    (value,) = conn.execute("SELECT typeof(value) FROM observations LIMIT 1").fetchone()
    assert value == "real"


def test_build_db_from_csv(tmp_path):
    path = db.build_db(db_path=tmp_path / "test.db")
    conn = db.connect(path)
    try:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == set(db.TABLES)
    finally:
        conn.close()
