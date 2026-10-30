"""HTTP side of the secure processing environment: the only way to reach an SPE.

Every route needs `Authorization: Bearer <key>` (the key the APPROVE_SPE
decision returned) and `X-Applicant: <name>` (the applicant named on the
permit, percent-encoded because header values are ASCII). The key fixes which SPE is opened, so there is no SPE id in the URL
and a key cannot be pointed at another permit's data.
The pages themselves live in frontend/ (Vue + Vuetify), not here.
"""

from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from app import spe

router = APIRouter(prefix="/api/spe", tags=["spe"])


class QueryBody(BaseModel):
    sql: str


def current_spe(authorization: str | None = Header(None), x_applicant: str | None = Header(None)) -> str:
    scheme, _, key = (authorization or "").partition(" ")
    return spe.authenticate(key if scheme.lower() == "bearer" else None, unquote(x_applicant or ""))


@router.post("/access")
def access(spe_id: str = Depends(current_spe)) -> dict[str, Any]:
    """Exchange key + name for the permit: what the frontend calls first."""
    return spe.info(spe_id)


@router.get("")
def spe_info(spe_id: str = Depends(current_spe)) -> dict[str, Any]:
    return spe.info(spe_id)


@router.post("/query")
def spe_query(body: QueryBody, spe_id: str = Depends(current_spe)) -> dict[str, Any]:
    return spe.query(spe_id, body.sql)


@router.get("/download")
@router.get("/download/{what:path}")
def spe_download(what: str = "", spe_id: str = Depends(current_spe)) -> None:
    spe.download(spe_id, what=what)
