"""FastAPI entry point. Run with: uvicorn app.main:app --reload --port 8000"""

import sqlite3
from collections.abc import Iterator

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from app import db, pipeline, submissions
from app.models import DataRequest, PipelineResult
from app.rag.ollama import OllamaUnavailable

app = FastAPI(title="EHDS Health Data Access Body", version="0.1.0")
app.include_router(submissions.router)


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


@app.exception_handler(OllamaUnavailable)
def ollama_unavailable(request: Request, exc: OllamaUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/requests", response_model=PipelineResult)
def submit_request(
    request: DataRequest, conn: sqlite3.Connection = Depends(get_conn)
) -> PipelineResult:
    return pipeline.run(request, conn)
