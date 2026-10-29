"""Map plain-English terms in the definitions to ICD-10, ATC and LOINC codes.

STUB: maps nothing. To replace: e.g. "type 2 diabetes" -> {"icd10": ["E11%"]}.
"""

from app.models import CodeMapping, ResultSpec


def map_codes(spec: ResultSpec) -> CodeMapping:
    return CodeMapping()
