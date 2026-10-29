"""FastAPI entry point. Run with: uvicorn app.main:app --reload --port 8000"""

import sqlite3
from collections.abc import Iterator

from fastapi import Depends, FastAPI

from app import db, pipeline
from app.models import DataRequest, PipelineResult

app = FastAPI(title="EHDS Health Data Access Body", version="0.1.0")


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/requests", response_model=PipelineResult)
def submit_request(
    request: DataRequest, conn: sqlite3.Connection = Depends(get_conn)
) -> PipelineResult:
    return pipeline.run(request, conn)
