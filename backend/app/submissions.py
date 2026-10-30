"""Submissions API: send a request, list them, view and download one.

The result is a PLACEHOLDER in the shape of PipelineResult until the pipeline
is ready; swap `_placeholder_result` for `pipeline.run(request, conn)` then.
Submissions live in memory and are gone after a restart.
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.models import DataRequest, PipelineResult

router = APIRouter(prefix="/api/submissions", tags=["submissions"])


class Submission(BaseModel):
    request: DataRequest
    result: PipelineResult
    received: str


_store: dict[str, Submission] = {}


def _placeholder_result(request: DataRequest) -> PipelineResult:
    if request.requested_as.strip().lower() == "health data access application":
        return PipelineResult(
            request_id=request.id,
            decision="APPROVE_SPE",
            articles=["53", "66", "68", "73"],
            reason="Placeholder: the decision engine is not connected yet.",
            flags=["placeholder"],
            route="spe",
            data={"cohort_size": 0, "record_types": ["conditions", "medications"]},
        )
    return PipelineResult(
        request_id=request.id,
        decision="APPROVE_STATISTICAL",
        articles=["53", "69"],
        reason="Placeholder: the decision engine is not connected yet.",
        flags=["placeholder"],
        route="statistics",
        data={"Group A": [120, 12, 10.0], "Group B": [85, "<10", "suppressed"]},
    )


def _next_id() -> str:
    n = len(_store) + 1
    while f"S{n:02d}" in _store:
        n += 1
    return f"S{n:02d}"


@router.post("", response_model=Submission)
def submit(request: DataRequest) -> Submission:
    if not request.id.strip():
        request.id = _next_id()
    if request.id in _store:
        raise HTTPException(409, f"a request with id {request.id} was already submitted")
    received = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _store[request.id] = Submission(request=request, result=_placeholder_result(request), received=received)
    return _store[request.id]


@router.get("", response_model=list[Submission])
def list_submissions() -> list[Submission]:
    return sorted(_store.values(), key=lambda s: s.received, reverse=True)


def _get(request_id: str) -> Submission:
    if request_id not in _store:
        raise HTTPException(404, f"no request with id {request_id}")
    return _store[request_id]


@router.get("/{request_id}", response_model=Submission)
def get_submission(request_id: str) -> Submission:
    return _get(request_id)


@router.get("/{request_id}/download")
def download(request_id: str, part: str = "all") -> Response:
    """part: "request" (the JSON as sent), "result" or "all" (both)."""
    s = _get(request_id)
    body = {
        "request": s.request.model_dump(mode="json"),
        "result": s.result.model_dump(mode="json"),
        "all": s.model_dump(mode="json"),
    }.get(part)
    if body is None:
        raise HTTPException(400, "part must be request, result or all")
    name = f"{request_id}.json" if part == "request" else f"{request_id}-{part}.json"
    return Response(
        json.dumps(body, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
