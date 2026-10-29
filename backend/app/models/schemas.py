"""API models. DataRequest mirrors the request JSON files (requests/R01.json etc.)."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class Applicant(BaseModel):
    name: str
    organisation: str
    country: str


class RequestedAs(StrEnum):
    HEALTH_DATA_REQUEST = "health data request"
    HEALTH_DATA_ACCESS_APPLICATION = "health data access application"


class DataRequest(BaseModel):
    id: str
    submitted: datetime
    applicant: Applicant
    requested_as: RequestedAs
    requested_format: str
    purpose: str
    question: str
    definitions: list[str]


class DecisionType(StrEnum):
    REJECT = "REJECT"
    APPROVE_STATISTICAL = "APPROVE_STATISTICAL"
    APPROVE_SPE = "APPROVE_SPE"


class Decision(BaseModel):
    decision: DecisionType
    articles: list[str]  # article numbers of Regulation (EU) 2025/327, e.g. ["53", "69"]
    reason: str


class RequestResult(BaseModel):
    request_id: str
    decision: Decision
    answer: dict[str, Any] | None = None  # statistics, SPE details, or None on REJECT
