"""Vocabulary: map plain-English concepts to code prefixes in the data.

Request definitions name conditions, drugs and measurements in plain English
("type 2 diabetes", "metformin", "HbA1c"). The data codes them as ICD-10
(conditions.csv, encounters.primary_icd10), ATC (medications.csv) and LOINC
(observations.csv). This module owns that mapping.

The mapping is a static dictionary keyed by a normalised lowercase concept
string. Prefix matching is used (e.g. code.startswith(prefix)).

To look up an unknown concept an LLM call can be added here in a later
iteration without changing any other layer.
"""

import re
from enum import StrEnum


class CodeSystem(StrEnum):
    ICD10 = "ICD-10"
    ATC = "ATC"
    LOINC = "LOINC"


# (CodeSystem, list[code_prefixes])
_VOCAB: dict[str, tuple[CodeSystem, list[str]]] = {
    # ── Conditions (ICD-10) ──────────────────────────────────────────────────
    "type 2 diabetes": (CodeSystem.ICD10, ["E11"]),
    "type2 diabetes": (CodeSystem.ICD10, ["E11"]),
    "diabetes type 2": (CodeSystem.ICD10, ["E11"]),
    "atrial fibrillation": (CodeSystem.ICD10, ["I48"]),
    "hypertension": (CodeSystem.ICD10, ["I10"]),
    "essential hypertension": (CodeSystem.ICD10, ["I10"]),
    "breast cancer": (CodeSystem.ICD10, ["C50"]),
    "obesity": (CodeSystem.ICD10, ["E66"]),
    "copd": (CodeSystem.ICD10, ["J44"]),
    "heart failure": (CodeSystem.ICD10, ["I50"]),
    "asthma": (CodeSystem.ICD10, ["J45"]),
    "myocardial infarction": (CodeSystem.ICD10, ["I21", "I22"]),
    "stroke": (CodeSystem.ICD10, ["I63", "I64"]),
    "depression": (CodeSystem.ICD10, ["F32", "F33"]),
    "chronic kidney disease": (CodeSystem.ICD10, ["N18"]),
    "ckd": (CodeSystem.ICD10, ["N18"]),
    # ── Medications (ATC) ────────────────────────────────────────────────────
    "oral anticoagulant": (CodeSystem.ATC, ["B01AA", "B01AF"]),
    "oac": (CodeSystem.ATC, ["B01AA", "B01AF"]),
    "vitamin k antagonist": (CodeSystem.ATC, ["B01AA"]),
    "doac": (CodeSystem.ATC, ["B01AF"]),
    "ace inhibitor": (CodeSystem.ATC, ["C09AA"]),
    "arb": (CodeSystem.ATC, ["C09CA"]),
    "angiotensin receptor blocker": (CodeSystem.ATC, ["C09CA"]),
    "metformin": (CodeSystem.ATC, ["A10BA02"]),
    "sglt2 inhibitor": (CodeSystem.ATC, ["A10BK"]),
    "glp-1": (CodeSystem.ATC, ["A10BJ"]),
    "insulin": (CodeSystem.ATC, ["A10A"]),
    "statin": (CodeSystem.ATC, ["C10AA"]),
    "beta blocker": (CodeSystem.ATC, ["C07"]),
    "antiplatelet": (CodeSystem.ATC, ["B01AC"]),
    "aspirin": (CodeSystem.ATC, ["B01AC06"]),
    # ── Observations (LOINC) ─────────────────────────────────────────────────
    "hba1c": (CodeSystem.LOINC, ["4548-4"]),
    "bmi": (CodeSystem.LOINC, ["39156-5"]),
    "body mass index": (CodeSystem.LOINC, ["39156-5"]),
    "systolic blood pressure": (CodeSystem.LOINC, ["8480-6"]),
    "blood pressure": (CodeSystem.LOINC, ["8480-6"]),
    "egfr": (CodeSystem.LOINC, ["62238-1"]),
    "kidney function": (CodeSystem.LOINC, ["62238-1"]),
}


def _normalise(concept: str) -> str:
    """Lowercase and collapse whitespace."""
    return re.sub(r"\s+", " ", concept.strip().lower())


def lookup(concept: str) -> tuple[CodeSystem, list[str]]:
    """Return (CodeSystem, prefixes) for a plain-English concept.

    Raises KeyError if the concept is not in the vocabulary.
    """
    key = _normalise(concept)
    if key in _VOCAB:
        return _VOCAB[key]
    # Try partial match: if the concept string contains a known key
    for vocab_key, value in _VOCAB.items():
        if vocab_key in key:
            return value
    raise KeyError(f"Unknown concept: {concept!r}")


def try_lookup(concept: str) -> tuple[CodeSystem, list[str]] | None:
    """Like lookup() but returns None instead of raising."""
    try:
        return lookup(concept)
    except KeyError:
        return None
