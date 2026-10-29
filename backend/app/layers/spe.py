"""SPE branch: describe the cohort and record types to copy into an SPE.

STUB: asks for every table. To replace: pick the record types the request
names, and later copy the selected patients into a separate schema with
downloads blocked.
"""

from app.db import TABLES
from app.models import DataRequest, ResultSpec


def build_spec(request: DataRequest) -> ResultSpec:
    return ResultSpec(route="spe", request=request, record_types=list(TABLES))
