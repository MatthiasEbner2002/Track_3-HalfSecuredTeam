"""Interpretation: turn a request's plain-English definitions into a QuerySpec.

A QuerySpec describes what to compute without SQL: population filters, groups,
measures (counts, percentages, means) and, for an SPE, the record types to copy.
Queries are generated from the definitions; nothing is written by hand per request.
"""

from dataclasses import dataclass

from app.models.schemas import DataRequest


@dataclass(frozen=True)
class QuerySpec:
    """Structured query description. Fields are added with the interpreter."""


def interpret(request: DataRequest) -> QuerySpec:
    """Build a QuerySpec from the request's definitions."""
    raise NotImplementedError
