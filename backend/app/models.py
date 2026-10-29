"""Data passed between the pipeline layers.

Each layer takes the previous layer's output and returns the next model, so a
layer can be replaced without touching the others as long as it keeps its
signature.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Route = Literal["statistics", "spe"]
DecisionType = Literal["REJECT", "APPROVE_STATISTICAL", "APPROVE_SPE"]


class Applicant(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    organisation: str
    country: str


class DataRequest(BaseModel):
    """A request as found in requests/R*.json."""

    model_config = ConfigDict(extra="allow")

    id: str
    submitted: str | None = None
    applicant: Applicant
    requested_as: str  # "health data request" | "health data access application"
    requested_format: str | None = None
    purpose: str
    question: str
    definitions: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    """Output of the decision system: accept or reject, with the articles cited."""

    accepted: bool
    articles: list[str] = Field(default_factory=list)
    reason: str = ""


class ResultSpec(BaseModel):
    """'Result needed': what the statistics or SPE branch asks the data layers for."""

    route: Route
    request: DataRequest
    record_types: list[str] = Field(default_factory=list)  # tables to copy into an SPE


class CodeMapping(BaseModel):
    """Plain-English term -> codes, e.g. 'type 2 diabetes' -> {'icd10': ['E11%']}."""

    terms: dict[str, dict[str, list[str]]] = Field(default_factory=dict)


class Query(BaseModel):
    name: str
    sql: str
    params: list[Any] = Field(default_factory=list)


class QueryPlan(BaseModel):
    queries: list[Query] = Field(default_factory=list)


# name of a query -> its rows
QueryResults = dict[str, list[dict[str, Any]]]


class PipelineResult(BaseModel):
    """What the applicant and staff get back."""

    request_id: str
    decision: DecisionType
    articles: list[str]
    reason: str
    route: Route | None = None
    mapping: CodeMapping | None = None
    queries: list[Query] = Field(default_factory=list)
    data: QueryResults | None = None
