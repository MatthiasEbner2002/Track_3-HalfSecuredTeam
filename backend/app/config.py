"""Paths and constants. Override paths with environment variables if needed."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: Path) -> None:
    """Read KEY=VALUE lines into os.environ (setdefault; real env wins). No logging of values."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


for _env_file in (REPO_ROOT / "backend" / ".env", REPO_ROOT / ".env"):
    load_env_file(_env_file)

DATA_DIR = Path(os.environ.get("EHDS_DATA_DIR", REPO_ROOT / "data"))
DB_PATH = Path(os.environ.get("EHDS_DB_PATH", DATA_DIR / "ehds.db"))
REQUESTS_DIR = Path(os.environ.get("EHDS_REQUESTS_DIR", REPO_ROOT / "requests"))

# Law corpus: Regulation (EU) 2025/327 as saved from EUR-Lex, parsed into paragraphs + embeddings.
LAW_SOURCE = Path(os.environ.get("EHDS_LAW_SOURCE", REPO_ROOT / "eulex.html"))
LAW_DB_PATH = Path(os.environ.get("EHDS_LAW_DB_PATH", DATA_DIR / "law.db"))

# Chat LLM via OpenRouter (decision and routing); embeddings stay on local Ollama.
# Temperature 0 and the on-disk answer cache keep decisions reproducible.
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = os.environ.get("EHDS_OPENROUTER_URL", "https://openrouter.ai/api/v1")
OLLAMA_URL = os.environ.get("EHDS_OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = os.environ.get("EHDS_LLM_MODEL", "anthropic/claude-haiku-5.5")
EMBED_MODEL = os.environ.get("EHDS_EMBED_MODEL", "nomic-embed-text")
LLM_TIMEOUT_S = int(os.environ.get("EHDS_OPENROUTER_TIMEOUT", "60"))
RETRIEVAL_TOP_K = 3  # candidate points per article shown to the LLM

REFERENCE_DATE = "2025-12-31"  # date for ages and active medications
SMALL_CELL_THRESHOLD = 10  # counts below this are shown as "<10"

# Secure processing environments (Art. 73): one SQLite file per data permit, never served as a file.
SPE_DIR = Path(os.environ.get("EHDS_SPE_DIR", REPO_ROOT / "backend" / "spe"))
SPE_PAGE_ROWS = 50  # most rows one query shows on screen (as an image)
CORS_ORIGINS = [o for o in os.environ.get("EHDS_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o]
SPE_FRONTEND_URL = os.environ.get("EHDS_SPE_FRONTEND_URL", "http://localhost:5173")  # where the applicant enters the key
SPE_QUERY_STEPS = 2_000_000  # SQLite VM steps before a query is aborted
