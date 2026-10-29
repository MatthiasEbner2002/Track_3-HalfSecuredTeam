"""FastAPI entry point. Run with: uv run uvicorn app.main:app --reload --port 8000"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: load the five CSV files into SQLite (reuses existing DB if up-to-date)
    init_db(data_dir=settings.data_dir)
    yield
    # Shutdown: SQLite connections are closed automatically by the GC;
    # nothing to do explicitly for the global connection.


app = FastAPI(title="EHDS Health Data Access Body", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}
