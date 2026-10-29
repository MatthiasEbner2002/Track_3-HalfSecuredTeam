"""Paths and constants. Override paths with environment variables if needed."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = Path(os.environ.get("EHDS_DATA_DIR", REPO_ROOT / "data"))
DB_PATH = Path(os.environ.get("EHDS_DB_PATH", DATA_DIR / "ehds.db"))
REQUESTS_DIR = Path(os.environ.get("EHDS_REQUESTS_DIR", REPO_ROOT / "requests"))

REFERENCE_DATE = "2025-12-31"  # date for ages and active medications
SMALL_CELL_THRESHOLD = 10  # counts below this are shown as "<10"
