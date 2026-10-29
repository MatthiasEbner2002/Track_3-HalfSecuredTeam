"""HTTP API under /api."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.db import get_conn, load_all_results, load_result, store_result
from app.models.schemas import DataRequest, DecisionType, RequestResult
from app.services import decide as decide_svc
from app.services import engine as engine_svc
from app.services import interpret as interpret_svc
from app.services import spe as spe_svc
from app.services.disclosure import apply as disclose

router = APIRouter(prefix="/api")


@router.post("/requests", response_model=RequestResult)
def submit_request(body: DataRequest) -> RequestResult:
    """Decide a request and return the decision with statistics or SPE details."""
    # 1. Decide
    decision = decide_svc.decide(body)

    answer: dict[str, Any] | None = None

    if decision.decision == DecisionType.REJECT:
        answer = None

    elif decision.decision == DecisionType.APPROVE_STATISTICAL:
        # 2. Interpret → QuerySpec
        spec = interpret_svc.interpret(body)
        # 3. Run query → raw rows
        raw_rows = engine_svc.run(spec)
        # 4. Apply small-cell rule
        answer = disclose(raw_rows)

    elif decision.decision == DecisionType.APPROVE_SPE:
        # 2. Interpret → QuerySpec
        spec = interpret_svc.interpret(body)
        # 3. Get cohort patient IDs
        patient_ids = engine_svc.get_cohort_patient_ids(spec)
        # 4. Provision SPE
        spe_svc.provision(
            request_id=body.id,
            patient_ids=patient_ids,
            record_types=spec.record_types,
            source_conn=get_conn(),
        )
        answer = {"cohort_size": len(patient_ids)}

    result = RequestResult(
        request_id=body.id,
        decision=decision,
        answer=answer,
    )

    # Persist
    store_result(body.id, result.model_dump(mode="json"))

    return result


@router.get("/requests", response_model=list[RequestResult])
def list_requests() -> list[RequestResult]:
    """List all decided requests for the staff view."""
    payloads = load_all_results()
    return [RequestResult(**p) for p in payloads]


@router.get("/requests/{request_id}", response_model=RequestResult)
def get_request(request_id: str) -> RequestResult:
    """Return the stored decision and answer for one request."""
    payload = load_result(request_id)
    if payload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Request not found")
    return RequestResult(**payload)


@router.get("/spe/{request_id}")
def get_spe(request_id: str) -> dict[str, Any]:
    """Describe the SPE of a request: record types, exact patient count, audit log."""
    return spe_svc.describe(request_id)


@router.get("/spe/{request_id}/download", status_code=status.HTTP_403_FORBIDDEN)
def download_spe(request_id: str) -> dict[str, str]:
    """Always 403: data never leaves the SPE (Art. 73)."""
    spe_svc.block_download(request_id)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Download blocked. Data cannot leave the secure processing environment (Art. 73).",
    )
