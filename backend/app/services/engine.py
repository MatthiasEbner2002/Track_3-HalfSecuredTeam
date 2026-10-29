"""Query engine: QuerySpec -> SQL -> raw result table.

Runs against the DuckDB connection opened at startup. Opted-out patients are
excluded everywhere. The raw table is unmasked and must go through
disclosure.py before it leaves the backend.
"""

from typing import Any

from app.services.interpret import QuerySpec


def to_sql(spec: QuerySpec) -> str:
    """Generate the SQL for a QuerySpec."""
    raise NotImplementedError


def run(spec: QuerySpec) -> list[dict[str, Any]]:
    """Execute the query and return one row per requested group (empty groups included)."""
    raise NotImplementedError
