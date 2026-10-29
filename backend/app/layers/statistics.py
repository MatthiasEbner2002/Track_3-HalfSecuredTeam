"""Statistics branch: describe the aggregated result the request needs.

STUB: passes the request through. To replace: extract groups and measures
(e.g. "per region: adults, adults with T2D, prevalence").
"""

from app.models import DataRequest, ResultSpec


def build_spec(request: DataRequest) -> ResultSpec:
    return ResultSpec(route="statistics", request=request)
