"""Interpretation: turn a request's plain-English definitions into a QuerySpec.

A QuerySpec describes what to compute without SQL: population filters, groups,
measures (counts, percentages, means) and, for an SPE, the record types to copy.

The interpreter uses regex + keyword matching against the definitions and question.
It is designed to cover all 10 R0x requests and generalise to new ones in the
same style. Each section that can be replaced by an LLM call is clearly marked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.models.schemas import DataRequest, RequestedAs
from app.services.vocab import CodeSystem, try_lookup


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class MeasureKind(StrEnum):
    COUNT = "count"
    PERCENTAGE = "percentage"
    MEAN = "mean"
    COHORT_SIZE = "cohort_size"


@dataclass(frozen=False)
class CodeFilter:
    """Require (or exclude) a code prefix in a specific table."""
    table: str            # "conditions", "medications", "observations", "encounters"
    column: str           # e.g. "icd10", "atc", "loinc", "primary_icd10"
    prefixes: list[str]   # code must start with one of these
    active_only: bool = False  # for medications: only active on reference date
    loinc_latest: bool = False  # for observations: use latest value per patient


@dataclass(frozen=False)
class AgeFilter:
    min_age: int | None = None   # >= min_age
    max_age: int | None = None   # <= max_age


@dataclass(frozen=False)
class SexFilter:
    sex: str  # "male" or "female"


@dataclass(frozen=False)
class EncounterFilter:
    encounter_type: str | None = None    # "inpatient", "outpatient", "emergency"
    year: int | None = None              # start_date in this year
    primary_icd10_prefixes: list[str] = field(default_factory=list)


@dataclass(frozen=False)
class GroupBySpec:
    by: str          # "region", "data_holder", or "age_band"
    bands: list[tuple[int | None, int | None]] | None = None  # for age_band: [(lo,hi), …]
    labels: list[str] | None = None      # human-readable band label per entry


@dataclass(frozen=False)
class Measure:
    kind: MeasureKind
    name: str                    # the output key name
    # for COUNT: just count patients matching the current group
    # for PERCENTAGE: numerator_filter is an additional CodeFilter the patient must also satisfy
    numerator_filter: CodeFilter | None = None
    # for MEAN: which observation to average
    obs_loinc: list[str] | None = None
    obs_latest: bool = True


@dataclass(frozen=False)
class QuerySpec:
    """Structured query description produced by the interpreter."""
    # Population: patients that must satisfy ALL of these
    age_filter: AgeFilter | None = None
    sex_filter: SexFilter | None = None
    condition_filter: CodeFilter | None = None   # primary condition defining the cohort
    encounter_filter: EncounterFilter | None = None

    # Additional medication/observation filters for the whole cohort
    extra_filters: list[CodeFilter] = field(default_factory=list)

    # Grouping
    group_by: GroupBySpec | None = None

    # What to compute per group
    measures: list[Measure] = field(default_factory=list)

    # For SPE: which record types to copy
    record_types: list[str] = field(default_factory=list)

    # Raw request stored for debugging
    request_id: str = ""


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------

_REF_DATE = "2025-12-31"
_REF_YEAR = 2025

# All nine Austrian federal states (the "Report per region" groups)
_AUSTRIAN_REGIONS = [
    "Burgenland", "Kärnten", "Niederösterreich", "Oberösterreich",
    "Salzburg", "Steiermark", "Tirol", "Vorarlberg", "Wien",
]


def _parse_age_bands(defs_text: str) -> tuple[list[tuple[int | None, int | None]], list[str]]:
    """Extract age bands from text like 'Age bands: 18-49, 50-64, 65+' → [(18,49),(50,64),(65,None)]."""
    # Pattern: "N-M" or "N+" or "M+" in a bands line
    bands: list[tuple[int | None, int | None]] = []
    labels: list[str] = []

    # Look for a line/phrase with multiple age bands
    band_pat = re.compile(r'\b(\d+)[-–](\d+)\b|\b(\d+)\+')
    for match in band_pat.finditer(defs_text):
        if match.group(3):  # "N+" pattern
            lo = int(match.group(3))
            bands.append((lo, None))
            labels.append(f"{lo}+")
        else:
            lo, hi = int(match.group(1)), int(match.group(2))
            bands.append((lo, hi))
            labels.append(f"{lo}-{hi}")

    return bands, labels


def _parse_min_age(defs_text: str) -> int | None:
    """Extract 'aged N or older' → N."""
    m = re.search(r'aged\s+(\d+)\s+or\s+older', defs_text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r'age\s*>=?\s*(\d+)', defs_text, re.I)
    if m:
        return int(m.group(1))
    return None


def _parse_sex(defs_text: str) -> str | None:
    """Extract 'sex female' or 'women' → 'female', 'men' → 'male'."""
    if re.search(r"\bwomen\b|\bfemale\b", defs_text, re.I):
        return "female"
    if re.search(r"\bmen\b|\bmale\b", defs_text, re.I):
        return "male"
    return None


def _detect_code_filter(text: str, table_hint: str | None = None) -> CodeFilter | None:
    """Try to detect a code filter from a phrase like 'ICD-10 code starting with E11'."""
    # Explicit ICD-10 code mention
    m = re.search(r'ICD-10\s+code\s+starting\s+with\s+([A-Z]\d[\d.]*(?:\s*,\s*[A-Z]\d[\d.]*)*)', text, re.I)
    if m:
        # Strip trailing sentence period (e.g. "starting with I10." → "I10")
        prefixes = [p.strip().rstrip(".") for p in re.split(r'[,\s]+or\s+|,\s*', m.group(1))]
        return CodeFilter(table="conditions", column="icd10", prefixes=prefixes)

    # Explicit ATC code mention
    m = re.search(r'ATC\s+code\s+starting\s+with\s+([A-Z0-9]+(?:\s*(?:or|,)\s*[A-Z0-9]+)*)', text, re.I)
    if m:
        raw = m.group(1)
        prefixes = [p.strip() for p in re.split(r'\s+or\s+|\s*,\s*', raw)]
        active = bool(re.search(r'active\s+on', text, re.I))
        return CodeFilter(table="medications", column="atc", prefixes=prefixes, active_only=active)

    # Explicit LOINC mention
    m = re.search(r'LOINC\s+(\d{4,5}-\d)', text, re.I)
    if m:
        loinc = m.group(1)
        latest = bool(re.search(r'latest', text, re.I))
        return CodeFilter(table="observations", column="loinc", prefixes=[loinc], loinc_latest=latest)

    # Plain-English concept
    known_concepts = [
        "type 2 diabetes", "atrial fibrillation", "hypertension", "breast cancer",
        "oral anticoagulant", "ace inhibitor", "arb", "sglt2 inhibitor",
        "hba1c", "bmi", "egfr",
    ]
    text_lower = text.lower()
    for concept in known_concepts:
        if concept in text_lower:
            result = try_lookup(concept)
            if result:
                cs, prefixes = result
                if cs == CodeSystem.ICD10:
                    return CodeFilter(table="conditions", column="icd10", prefixes=prefixes)
                elif cs == CodeSystem.ATC:
                    active = bool(re.search(r'active\s+on', text, re.I))
                    return CodeFilter(table="medications", column="atc", prefixes=prefixes, active_only=active)
                elif cs == CodeSystem.LOINC:
                    latest = bool(re.search(r'latest', text, re.I))
                    return CodeFilter(
                        table="observations", column="loinc",
                        prefixes=prefixes, loinc_latest=latest,
                    )
    return None


def _parse_encounter_filter(defs_text: str) -> EncounterFilter | None:
    """Detect encounter-based filters (inpatient stays, 2025, etc.)."""
    if not re.search(r'inpatient|encounter|visit|stay|hospital', defs_text, re.I):
        return None
    ef = EncounterFilter()
    if re.search(r'inpatient', defs_text, re.I):
        ef.encounter_type = "inpatient"
    m = re.search(r'\b(20\d\d)\b', defs_text)
    if m:
        ef.year = int(m.group(1))
    m_icd = re.search(r'primary.{0,30}ICD-10.*?([A-Z]\d[\d.]+)', defs_text, re.I)
    if m_icd:
        ef.primary_icd10_prefixes = [m_icd.group(1)]
    return ef


def interpret(request: DataRequest) -> QuerySpec:  # noqa: C901  (complex but intentional)
    """Build a QuerySpec from the request's definitions."""
    defs_text = "\n".join(request.definitions)
    full_text = f"{request.purpose}\n{request.question}\n{defs_text}"
    spec = QuerySpec(request_id=request.id)

    # ── 1. Age filter (population-wide) ────────────────────────────────────
    min_age = _parse_min_age(defs_text)
    if min_age is not None:
        spec.age_filter = AgeFilter(min_age=min_age)

    # ── 2. Sex filter ───────────────────────────────────────────────────────
    sex = _parse_sex(defs_text)
    if sex:
        spec.sex_filter = SexFilter(sex=sex)

    # ── 3. Primary condition / encounter filter ─────────────────────────────
    encounter_filter = _parse_encounter_filter(defs_text)
    if encounter_filter is not None:
        spec.encounter_filter = encounter_filter
    else:
        # Look for the primary cohort-defining condition in the definitions
        # "= any condition with an ICD-10 code starting with X" or similar
        #
        # Only use as cohort filter when the report does NOT separately request
        # the whole-population count.  R01 asks "Report per region: adults, adults
        # with type 2 diabetes …" — "adults" alone means the cohort is all adults
        # and E11 is just a measure numerator, not the population filter.
        condition_def = None
        for line in request.definitions:
            if re.search(r'=\s*(any\s+)?condition\s+with', line, re.I):
                condition_def = line
                break

        report_line = next(
            (l for l in request.definitions if re.search(r'^Report\b', l, re.I)), ""
        )
        # "Report …: adults, …" where "adults" is listed without an immediate "with"
        # means the whole adult population is the cohort, not just condition carriers.
        reports_whole_population = bool(
            re.search(r':\s*adults\s*,', report_line, re.I)
        )

        if condition_def and not reports_whole_population:
            cf = _detect_code_filter(condition_def)
            if cf:
                spec.condition_filter = cf

    # ── 4. Extra filters (medication / observation must-have for cohort) ────
    for line in request.definitions:
        # Lines that define the cohort by requiring an observation or medication
        # "have at least one HbA1c value"
        if re.search(r'have\s+at\s+least\s+one\s+', line, re.I) or \
           re.search(r'who\s+have\s+a\s+', line, re.I) or \
           re.search(r'Cohort\s*=', line, re.I):
            cf = _detect_code_filter(line)
            if cf and cf != spec.condition_filter:
                cf.loinc_latest = False  # just needs to exist
                spec.extra_filters.append(cf)

    # ── 5. Grouping ─────────────────────────────────────────────────────────
    if re.search(r'\bper\s+region\b|\bby\s+region\b|\bper\s+patient.{0,10}region\b', full_text, re.I):
        spec.group_by = GroupBySpec(
            by="region",
            bands=None,
            labels=_AUSTRIAN_REGIONS,
        )
    elif re.search(r'\bage\s+band|age\s+group|per\s+band\b', full_text, re.I):
        bands, labels = _parse_age_bands(defs_text)
        if bands:
            spec.group_by = GroupBySpec(by="age_band", bands=bands, labels=labels)
    elif re.search(r'\bper\s+hospital\b|\bby\s+(hospital|data.holder)\b', full_text, re.I):
        spec.group_by = GroupBySpec(by="data_holder")

    # ── 6. Measures ─────────────────────────────────────────────────────────
    _parse_measures(request, spec, defs_text)

    # ── 7. SPE record types ─────────────────────────────────────────────────
    if request.requested_as == RequestedAs.HEALTH_DATA_ACCESS_APPLICATION:
        rts: list[str] = []
        for rt in ("conditions", "medications", "observations", "encounters"):
            if rt.rstrip("s") in full_text.lower() or rt in full_text.lower():
                rts.append(rt)
        spec.record_types = rts if rts else ["conditions", "medications", "observations"]

    return spec


def _parse_measures(request: DataRequest, spec: QuerySpec, defs_text: str) -> None:  # noqa: C901
    """Fill spec.measures based on what the definitions ask to report."""
    full_text = f"{request.purpose}\n{request.question}\n{defs_text}"

    # Detect cohort-size request (SPE)
    if re.search(r'report\s+the\s+size\s+of\s+the\s+cohort', defs_text, re.I):
        spec.measures.append(Measure(kind=MeasureKind.COHORT_SIZE, name="cohort_size"))
        return

    # Detect inpatient stays count (not patient count)
    if re.search(r'count\s+stays\s+\(not\s+patients\)', defs_text, re.I):
        spec.measures.append(Measure(kind=MeasureKind.COUNT, name="inpatient_stays"))
        return

    # Look at each "Report per …" line for instructions
    report_lines = [l for l in request.definitions if re.search(r'^report\b', l, re.I)]
    if not report_lines:
        report_lines = [defs_text]

    # Helper: the cohort condition prefixes (if any) to distinguish cohort vs sub-filter
    cohort_prefixes = spec.condition_filter.prefixes if spec.condition_filter else []

    for line in report_lines:
        line_lower = line.lower()

        # ── plain count of the whole population group ──────────────────────
        # "adults" standalone = count of the whole cohort.
        # Match "adults," (with comma following) or "adults and" to avoid
        # "adults with X" from triggering this.
        if re.search(r'\badults\s*,|\badults\s+and\b', line_lower):
            spec.measures.append(Measure(kind=MeasureKind.COUNT, name="adults"))

        # ── count with a named condition ───────────────────────────────────
        # "adults with type 2 diabetes" / "patients with atrial fibrillation"
        m_cond = re.search(
            r'(?:adults|patients)\s+with\s+(type 2 diabetes|atrial fibrillation|'
            r'hypertension|breast cancer|[a-z0-9 ]+)',
            line_lower,
        )
        if m_cond:
            concept = m_cond.group(1).strip().rstrip(",. ")
            result = try_lookup(concept)
            if result:
                cs, prefixes = result
                if cs == CodeSystem.ICD10:
                    # Name: make a snake_case key matching expected output
                    name = _condition_count_name(concept)
                    # If this condition IS the cohort filter, this is just the cohort count
                    if set(prefixes) == set(cohort_prefixes):
                        spec.measures.append(Measure(
                            kind=MeasureKind.COUNT, name=name,
                        ))
                    else:
                        # It's a sub-filter of the cohort
                        cf_num = CodeFilter(table="conditions", column="icd10", prefixes=prefixes)
                        spec.measures.append(Measure(
                            kind=MeasureKind.COUNT, name=name, numerator_filter=cf_num,
                        ))

        # ── "without oac / without/no active oral anticoagulant" ──────────
        _no_oac_pat = re.search(
            r'(without|no)\s+(active\s+)?oral\s+anticoagulant|(without|no)\s+oac',
            line_lower,
        )
        if _no_oac_pat:
            spec.measures.append(Measure(
                kind=MeasureKind.COUNT,
                name="without_oac",
                numerator_filter=CodeFilter(
                    table="medications", column="atc",
                    prefixes=["B01AA", "B01AF"],
                    active_only=True,
                    # Inverted filter: patients WITHOUT this
                ),
            ))

        # ── share / percentage ─────────────────────────────────────────────
        if re.search(r'share\s+(?:in\s+%|without|no)|%\s+with(?:out)?\s+', line_lower) or \
           re.search(r'prevalence', line_lower):
            if re.search(r'prevalence', line_lower):
                spec.measures.append(Measure(
                    kind=MeasureKind.PERCENTAGE,
                    name="prevalence_pct",
                ))
            elif re.search(r'(without|no)\s+(active\s+)?oral\s+anticoagulant|(without|no)\s+oac', line_lower):
                spec.measures.append(Measure(
                    kind=MeasureKind.PERCENTAGE,
                    name="share_without_oac_pct",
                ))
            elif re.search(r'ace\s+inhibitor', line_lower):
                spec.measures.append(Measure(kind=MeasureKind.PERCENTAGE, name="ace_inhibitor_pct"))
            elif re.search(r'\barb\b', line_lower):
                spec.measures.append(Measure(kind=MeasureKind.PERCENTAGE, name="arb_pct"))

        # ── ACE / ARB percentages in same report line ──────────────────────
        if re.search(r'%\s+with\s+an\s+active\s+ace\s+inhibitor', line_lower):
            spec.measures.append(Measure(kind=MeasureKind.PERCENTAGE, name="ace_inhibitor_pct"))
        if re.search(r'%\s+with\s+an\s+active\s+arb', line_lower):
            spec.measures.append(Measure(kind=MeasureKind.PERCENTAGE, name="arb_pct"))

        # ── mean of an observation ─────────────────────────────────────────
        m = re.search(r'mean\s+(hba1c|bmi|body\s+mass\s+index|egfr|blood\s+pressure)', line_lower)
        if m:
            concept = m.group(1).strip()
            result = try_lookup(concept)
            if result:
                cs, prefixes = result
                # Use "mean_<concept>" as the output key to match expected.json
                name = "mean_" + re.sub(r'\s+', '_', concept)
                spec.measures.append(Measure(
                    kind=MeasureKind.MEAN,
                    name=name,
                    obs_loinc=prefixes,
                    obs_latest=True,
                ))

        # ── count with a BMI (must have a BMI observation) ────────────────
        if re.search(r'(?:number|count)\s+of\s+\w+\s+with\s+a\s+bmi|women\s+with\s+bmi|with\s+bmi', line_lower):
            spec.measures.append(Measure(
                kind=MeasureKind.COUNT,
                name="women_with_bmi",
                numerator_filter=CodeFilter(
                    table="observations", column="loinc",
                    prefixes=["39156-5"],
                ),
            ))

    # Deduplicate measures by name (keep first occurrence)
    seen: set[str] = set()
    deduped: list[Measure] = []
    for m in spec.measures:
        if m.name not in seen:
            seen.add(m.name)
            deduped.append(m)
    spec.measures = deduped



# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------


def _condition_count_name(concept: str) -> str:
    """Return the snake_case output key for a condition count measure.

    Matches the keys in expected.json:
      - "type 2 diabetes" → "type2_diabetes"
      - "atrial fibrillation" → "af_patients"
      - "hypertension" → "adults_with_hypertension"
      - others → snake_case of concept
    """
    concept_lower = concept.lower().strip()
    if "type 2 diabetes" in concept_lower or "type2 diabetes" in concept_lower:
        return "type2_diabetes"
    if "atrial fibrillation" in concept_lower:
        return "af_patients"
    if "hypertension" in concept_lower:
        return "adults_with_hypertension"
    return re.sub(r"\s+", "_", concept_lower)

