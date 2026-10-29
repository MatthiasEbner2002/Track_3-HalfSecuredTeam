"""HTTP API under /api. Every route is a stub until the services are implemented."""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.models.schemas import DataRequest, RequestResult

router = APIRouter(prefix="/api")


def _not_implemented() -> HTTPException:
    return HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="Not implemented yet")


@router.post("/requests", response_model=RequestResult)
def submit_request(body: DataRequest) -> RequestResult:
    """Decide a request and return the decision with statistics or SPE details."""
    raise _not_implemented()


@router.get("/requests", response_model=list[RequestResult])
def list_requests() -> list[RequestResult]:
    """List all decided requests for the staff view."""
    raise _not_implemented()


@router.get("/requests/{request_id}", response_model=RequestResult)
def get_request(request_id: str) -> RequestResult:
    """Return the stored decision and answer for one request."""
    raise _not_implemented()


@router.get("/spe/{request_id}")
def get_spe(request_id: str) -> dict[str, Any]:
    """Describe the SPE of a request: record types, exact patient count, audit log."""
    raise _not_implemented()


@router.get("/spe/{request_id}/download")
def download_spe(request_id: str) -> None:
    """Must later answer 403 citing Art. 73: data never leaves the SPE."""
    raise _not_implemented()
