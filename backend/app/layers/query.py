"""Generate SQL from the result spec and the code mapping.

STUB: selects everything from every table. To replace: build the cohort and
aggregate queries from the definitions, excluding opted-out patients.
"""

from app.db import TABLES
from app.models import CodeMapping, Query, QueryPlan, ResultSpec


def generate(spec: ResultSpec, mapping: CodeMapping) -> QueryPlan:
    return QueryPlan(queries=[Query(name=table, sql=f"SELECT * FROM {table}") for table in TABLES])
