"""Data passed between the pipeline layers.

Each layer takes the previous layer's output and returns the next model, so a
layer can be replaced without touching the others as long as it keeps its
signature.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class LawParagraph(BaseModel):
    """One chunk of the regulation, the unit of retrieval and grounding.

    kind "paragraph": a paragraph's own text without its points, id "Art. 53(1)"
    kind "point":     one point with its sub-points, id "Art. 53(1)(e)" or "Art. 54(b)"
    kind "recital":   one recital, id "Recital 62"
    """

    id: str
    kind: Literal["paragraph", "point", "recital"] = "paragraph"
    article: str  # e.g. "54"; "" for recitals
    article_title: str = ""
    text: str
    lead_in: str = ""  # for points: the paragraph sentence that introduces the list


class Evidence(BaseModel):
    """An article point found to apply, with the sentence of the request that shows it."""

    point: str  # e.g. "54(1)(b)", "53(1)(e)" or "spe_justification"
    label: str  # readable name of the point
    phrase: str  # quoted from the request
    source: str  # "llm" (IBM Bob), "model" (local NLI) or "rule"
    score: float | None = None  # model confidence 0..1; None for rules


class Decision(BaseModel):
    """Output of the decision system: accept or reject, with the articles cited."""

    accepted: bool
    articles: list[str] = Field(default_factory=list)
    reason: str = ""
    grounding: list[LawParagraph] = Field(default_factory=list)  # chunks the reason rests on
    needs_review: bool = False  # the model's answer could not be verified against the law text
    evidence: list[Evidence] = Field(default_factory=list)  # for staff: why
    flags: list[str] = Field(default_factory=list)  # e.g. "needs_review", "rules_only"


class ResultSpec(BaseModel):
    """'Result needed': what the statistics or SPE branch asks the data layers for."""

    route: Route
    request: DataRequest
    record_types: list[str] = Field(default_factory=list)  # tables to copy into an SPE


ConceptKind = Literal[
    "diagnosis",  # conditions.icd10 prefix
    "drug",  # medications.atc prefix
    "measurement",  # observations.loinc; no threshold = "has a value"
    "encounter",  # encounters.type / primary_icd10 prefix / start year
    "age",  # computed age on the reference date
    "sex",  # patients.sex
    "region",  # patients.region
    "hospital",  # patients.data_holder
]
GroupDim = Literal["region", "hospital", "sex", "band"]


class Concept(BaseModel):
    """A named yes/no property of a patient (or of a stay, for encounters)."""

    name: str
    kind: ConceptKind
    codes: list[str] = Field(default_factory=list)  # ICD-10/ATC prefixes, one LOINC, primary_icd10 prefixes
    active: bool = False  # drug: active on the reference date
    year: int | None = None  # diagnosis onset year / encounter start year
    encounter_type: Literal["inpatient", "outpatient", "emergency"] | None = None
    min_age: int | None = None
    max_age: int | None = None
    value: str | None = None  # sex, region or hospital
    op: Literal[">=", ">", "<=", "<"] | None = None  # measurement threshold on the latest value
    threshold: float | None = None


class Term(BaseModel):
    concept: str
    negate: bool = False


# AND of OR-groups: [[a], [b, c]] = a AND (b OR c)
Filter = list[list[Term]]


class Metric(BaseModel):
    """One value per group, in the order of the 'Report per ...' line."""

    label: str  # the phrase it came from
    op: Literal["count", "percent", "mean"]
    unit: Literal["patients", "stays"] = "patients"
    where: Filter = Field(default_factory=list)  # count: who; percent: numerator; mean: who
    denominator: Filter | None = None  # percent only
    measurement: str | None = None  # mean only: a measurement concept


class Band(BaseModel):
    label: str
    min_age: int
    max_age: int | None = None  # None = open ("80+")


class QuerySpec(BaseModel):
    """What to extract, independent of how the request was worded.

    Filled by the rule parser (and later by an LLM); compiled to SQL by query.py.
    """

    mode: Route
    concepts: dict[str, Concept] = Field(default_factory=dict)
    cohort: Filter = Field(default_factory=list)  # applies to every metric
    bands: list[Band] = Field(default_factory=list)
    group_by: list[GroupDim] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    record_types: list[str] = Field(default_factory=list)  # spe: tables to copy

    @model_validator(mode="after")
    def _check(self) -> "QuerySpec":
        def refs(f: Filter | None) -> list[str]:
            return [t.concept for group in f or [] for t in group]

        used = refs(self.cohort)
        for m in self.metrics:
            used += refs(m.where) + refs(m.denominator)
            if m.op == "mean":
                c = self.concepts.get(m.measurement or "")
                if c is None or c.kind != "measurement":
                    raise ValueError(f"'{m.label}': a mean needs a measurement, got {m.measurement!r}")
            if m.op == "percent" and m.denominator is None:
                raise ValueError(f"'{m.label}': a percentage needs a denominator")
        if missing := [n for n in used if n not in self.concepts]:
            raise ValueError(f"unknown concepts: {missing}")
        if "band" in self.group_by and not self.bands:
            raise ValueError("grouped by age band but no age bands defined")
        if self.mode == "statistics" and not self.metrics:
            raise ValueError("no metrics to report")
        if self.mode == "spe" and not self.record_types:
            raise ValueError("no record types to copy")
        return self


class CodeMapping(BaseModel):
    """Plain-English term -> codes, e.g. 'type 2 diabetes' -> {'icd10': ['E11%']}."""

    terms: dict[str, dict[str, list[str]]] = Field(default_factory=dict)


class Query(BaseModel):
    name: str
    sql: str
    params: list[Any] | dict[str, Any] = Field(default_factory=list)  # dict for :named


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
    grounding: list[LawParagraph] = Field(default_factory=list)
    needs_review: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    route: Route | None = None
    mapping: CodeMapping | None = None
    queries: list[Query] = Field(default_factory=list)
    data: QueryResults | None = None
