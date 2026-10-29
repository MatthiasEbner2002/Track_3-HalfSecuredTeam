"""Vocabulary: map plain-English concepts to code prefixes in the data.

Request definitions name conditions, drugs and measurements in plain English
("type 2 diabetes", "metformin", "HbA1c"). The data codes them as ICD-10
(conditions.csv, encounters.primary_icd10), ATC (medications.csv) and LOINC
(observations.csv). This module owns that mapping.
"""

from enum import StrEnum


class CodeSystem(StrEnum):
    ICD10 = "ICD-10"
    ATC = "ATC"
    LOINC = "LOINC"


def lookup(concept: str) -> tuple[CodeSystem, list[str]]:
    """Return the code system and the code prefixes for a plain-English concept."""
    raise NotImplementedError
