"""HTTP side of the secure processing environment: the only way to reach an SPE.

Who is calling comes from the X-Actor header (a name, default "applicant") and
X-Role ("applicant" or "staff"). There is no login in this prototype; the
headers exist so the audit log and the staff/applicant split are visible.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import spe

router = APIRouter(tags=["spe"])
STATIC_DIR = Path(__file__).parent / "static"


class QueryBody(BaseModel):
    sql: str


class OutputBody(BaseModel):
    name: str
    rows: list[dict[str, Any]] | None = None  # a table, small-cell checked
    content: str | None = None  # anything else (metrics, model card), staff checks


class ReviewBody(BaseModel):
    status: str  # approved | rejected
    note: str = ""


def _actor(actor: str | None, role: str | None) -> str:
    return actor or ("staff" if role == "staff" else "applicant")


@router.get("/spe/{spe_id}", include_in_schema=False)
def spe_page(spe_id: str) -> FileResponse:
    if not spe.exists(spe_id):
        raise HTTPException(404, f"no secure processing environment {spe_id!r}")
    return FileResponse(STATIC_DIR / "spe.html")


@router.get("/api/spe/{spe_id}")
def spe_info(spe_id: str) -> dict[str, Any]:
    return spe.info(spe_id)


@router.post("/api/spe/{spe_id}/query")
def spe_query(spe_id: str, body: QueryBody, x_actor: str | None = Header(None), x_role: str | None = Header(None)) -> dict[str, Any]:
    return spe.query(spe_id, body.sql, actor=_actor(x_actor, x_role))


@router.get("/api/spe/{spe_id}/download")
@router.get("/api/spe/{spe_id}/download/{what:path}")
def spe_download(spe_id: str, what: str = "", x_actor: str | None = Header(None), x_role: str | None = Header(None)) -> None:
    spe.download(spe_id, actor=_actor(x_actor, x_role), what=what)


@router.get("/api/spe/{spe_id}/outputs")
def spe_outputs(spe_id: str) -> list[dict[str, Any]]:
    return spe.list_outputs(spe_id)


@router.post("/api/spe/{spe_id}/outputs")
def spe_export(spe_id: str, body: OutputBody, x_actor: str | None = Header(None), x_role: str | None = Header(None)) -> dict[str, Any]:
    return spe.export(spe_id, body.name, rows=body.rows, content=body.content, actor=_actor(x_actor, x_role))


@router.get("/api/spe/{spe_id}/outputs/{output_id}")
def spe_output(spe_id: str, output_id: int, x_role: str | None = Header(None)) -> dict[str, Any]:
    return spe.get_output(spe_id, output_id, role=x_role or "applicant")


@router.post("/api/spe/{spe_id}/outputs/{output_id}/review")
def spe_review(spe_id: str, output_id: int, body: ReviewBody, x_actor: str | None = Header(None)) -> dict[str, Any]:
    return spe.review(spe_id, output_id, body.status, body.note, actor=x_actor or "staff")


@router.get("/api/spe/{spe_id}/audit")
def spe_audit(spe_id: str) -> list[dict[str, Any]]:
    return spe.audit_log(spe_id)
