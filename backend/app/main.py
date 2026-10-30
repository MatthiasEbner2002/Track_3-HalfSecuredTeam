"""FastAPI entry point. Run with: uvicorn app.main:app --reload --port 8000"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app import db, pipeline, spe, spe_api, submissions
from app.config import CORS_ORIGINS, REQUESTS_DIR, SPE_FRONTEND_URL
from app.models import DataRequest, PipelineResult
from app.rag.errors import ServiceUnavailable

app = FastAPI(title="EHDS Health Data Access Body", version="0.1.0")
app.include_router(submissions.router)
app.include_router(spe_api.router)
# the SPE frontend (frontend/, Vite) runs on its own origin
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

E2E_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "e2e" / "cases"


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    """The pages (test console, SPE) live in frontend/; this API only serves JSON."""
    return RedirectResponse(f"{SPE_FRONTEND_URL}/#/console")


def _load_sample(sample_id: str) -> dict[str, Any] | None:
    """The 10 task requests (R01..R10, answer in expected.json) or an end-to-end case (E001..E200)."""
    if sample_id.startswith("R") and (path := REQUESTS_DIR / f"{sample_id}.json").is_file():
        expected_path = REQUESTS_DIR / "expected.json"
        expected = json.loads(expected_path.read_text()).get(sample_id) if expected_path.is_file() else None
        return {"request": json.loads(path.read_text()), "expected": expected}
    if sample_id.startswith("E") and (path := E2E_DIR / f"{sample_id}.json").is_file():
        case = json.loads(path.read_text())
        return {"request": case["request"], "expected": case["expected"], "note": case.get("note")}
    return None


@app.get("/api/samples")
def list_samples() -> list[dict[str, Any]]:
    out = []
    for path in sorted(REQUESTS_DIR.glob("R*.json")) + sorted(E2E_DIR.glob("E*.json")):
        sample = _load_sample(path.stem) or {}
        expected = sample.get("expected") or {}
        out.append({"id": path.stem, "decision": expected.get("decision"), "note": sample.get("note")})
    return out


@app.get("/api/samples/{sample_id}")
def get_sample(sample_id: str) -> dict[str, Any]:
    if sample := _load_sample(sample_id):
        return sample
    raise HTTPException(status_code=404, detail=f"no sample {sample_id}")


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


@app.exception_handler(ServiceUnavailable)
def service_unavailable(request: Request, exc: ServiceUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(spe.SpeError)
def spe_error(request: Request, exc: spe.SpeError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/requests", response_model=PipelineResult)
def submit_request(request: DataRequest, conn: sqlite3.Connection = Depends(get_conn)) -> PipelineResult:
    return pipeline.run(request, conn)
