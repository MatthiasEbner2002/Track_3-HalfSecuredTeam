"""Paths and constants. Override paths with environment variables if needed."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = Path(os.environ.get("EHDS_DATA_DIR", REPO_ROOT / "data"))
DB_PATH = Path(os.environ.get("EHDS_DB_PATH", DATA_DIR / "ehds.db"))
REQUESTS_DIR = Path(os.environ.get("EHDS_REQUESTS_DIR", REPO_ROOT / "requests"))

# Law corpus: Regulation (EU) 2025/327 as saved from EUR-Lex, parsed into paragraphs + embeddings.
LAW_SOURCE = Path(os.environ.get("EHDS_LAW_SOURCE", REPO_ROOT / "eulex.html"))
LAW_DB_PATH = Path(os.environ.get("EHDS_LAW_DB_PATH", DATA_DIR / "law.db"))

# Local LLM via Ollama. Temperature 0 and a fixed seed keep decisions reproducible.
OLLAMA_URL = os.environ.get("EHDS_OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = os.environ.get("EHDS_LLM_MODEL", "qwen2.5:7b")
EMBED_MODEL = os.environ.get("EHDS_EMBED_MODEL", "nomic-embed-text")
LLM_SEED = 42
RETRIEVAL_TOP_K = 3  # candidate points per article shown to the LLM

REFERENCE_DATE = "2025-12-31"  # date for ages and active medications
SMALL_CELL_THRESHOLD = 10  # counts below this are shown as "<10"
