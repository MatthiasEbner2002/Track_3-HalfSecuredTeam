"""Query engine: QuerySpec -> SQL -> raw result table.

Runs against the SQLite connection opened at startup. Opted-out patients are
excluded everywhere. The raw table is unmasked and must go through
disclosure.py before it leaves the backend.

The engine generates all SQL from the QuerySpec; no SQL is written by hand
per request.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.db import get_conn
from app.services.interpret import (
    AgeFilter,
    CodeFilter,
    EncounterFilter,
    GroupBySpec,
    Measure,
    MeasureKind,
    QuerySpec,
    SexFilter,
    _AUSTRIAN_REGIONS,
)

_REF_DATE = "2025-12-31"
_REF_YEAR = 2025


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run(spec: QuerySpec) -> list[dict[str, Any]]:
    """Execute the query and return one row per requested group (empty groups included)."""
    conn = get_conn()

    # Special case: cohort size (no grouping needed)
    if spec.measures and spec.measures[0].kind == MeasureKind.COHORT_SIZE:
        return _run_cohort_size(conn, spec)

    # Special case: inpatient stay count
    if spec.measures and spec.measures[0].name == "inpatient_stays":
        return _run_encounter_count(conn, spec)

    if spec.group_by is None:
        # No grouping: single row
        return _run_single_group(conn, spec)

    if spec.group_by.by == "region":
        return _run_by_region(conn, spec)

    if spec.group_by.by == "age_band":
        return _run_by_age_band(conn, spec)

    if spec.group_by.by == "data_holder":
        return _run_by_data_holder(conn, spec)

    return []


def get_cohort_patient_ids(spec: QuerySpec) -> list[str]:
    """Return the patient IDs for an SPE cohort (opted-out excluded)."""
    conn = get_conn()
    sql, params = _build_cohort_sql(spec)
    rows = conn.execute(sql, params).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# SQL builders
# ---------------------------------------------------------------------------


def _opted_out_clause() -> str:
    """SQL fragment that excludes opted-out patients (joined on p)."""
    return "p.opted_out = 0"


def _age_clause(af: AgeFilter) -> tuple[str, list[Any]]:
    """SQL fragment for age filter on patients table aliased as p."""
    parts: list[str] = []
    params: list[Any] = []
    if af.min_age is not None:
        # age = REF_YEAR - strftime('%Y', birth_date)
        parts.append(f"({_REF_YEAR} - CAST(strftime('%Y', p.birth_date) AS INTEGER)) >= ?")
        params.append(af.min_age)
    if af.max_age is not None:
        parts.append(f"({_REF_YEAR} - CAST(strftime('%Y', p.birth_date) AS INTEGER)) <= ?")
        params.append(af.max_age)
    return " AND ".join(parts), params


def _sex_clause(sf: SexFilter) -> tuple[str, list[Any]]:
    return "p.sex = ?", [sf.sex]


def _condition_join(cf: CodeFilter, alias: str) -> tuple[str, str, list[Any]]:
    """Return (JOIN clause, WHERE fragment, params) for a condition filter."""
    prefix_conds = " OR ".join([f"{alias}.icd10 LIKE ?" for _ in cf.prefixes])
    params = [f"{p}%" for p in cf.prefixes]
    join = f"JOIN conditions AS {alias} ON {alias}.patient_id = p.patient_id"
    where = f"({prefix_conds})"
    return join, where, params


def _medication_exists_join(cf: CodeFilter, alias: str) -> tuple[str, str, list[Any]]:
    """Return (JOIN clause, WHERE fragment, params) for a medication EXISTS filter."""
    prefix_conds = " OR ".join([f"{alias}.atc LIKE ?" for _ in cf.prefixes])
    params: list[Any] = [f"{p}%" for p in cf.prefixes]
    join = f"JOIN medications AS {alias} ON {alias}.patient_id = p.patient_id"
    where_parts = [f"({prefix_conds})"]
    if cf.active_only:
        where_parts.append(
            f"({alias}.start_date <= '{_REF_DATE}' AND ({alias}.end_date IS NULL OR {alias}.end_date = '' OR {alias}.end_date >= '{_REF_DATE}'))"
        )
    return join, " AND ".join(where_parts), params


def _observation_exists_join(cf: CodeFilter, alias: str) -> tuple[str, str, list[Any]]:
    """Return (JOIN clause, WHERE fragment, params) for an observation EXISTS filter."""
    loinc_conds = " OR ".join([f"{alias}.loinc LIKE ?" for _ in cf.prefixes])
    params: list[Any] = [f"{p}%" for p in cf.prefixes]
    join = f"JOIN observations AS {alias} ON {alias}.patient_id = p.patient_id"
    where = f"({loinc_conds})"
    return join, where, params


def _build_cohort_sql(spec: QuerySpec) -> tuple[str, list[Any]]:
    """Build the SQL that returns (patient_id,) for the cohort (no aggregation)."""
    joins: list[str] = []
    where: list[str] = []
    params: list[Any] = []

    where.append(_opted_out_clause())

    if spec.age_filter:
        clause, p = _age_clause(spec.age_filter)
        where.append(clause)
        params.extend(p)

    if spec.sex_filter:
        clause, p = _sex_clause(spec.sex_filter)
        where.append(clause)
        params.extend(p)

    if spec.condition_filter:
        cf = spec.condition_filter
        j, w, p = _condition_join(cf, "cond")
        joins.append(j)
        where.append(w)
        params.extend(p)

    for i, ef in enumerate(spec.extra_filters):
        alias = f"ef{i}"
        if ef.table == "observations":
            j, w, p = _observation_exists_join(ef, alias)
        elif ef.table == "medications":
            j, w, p = _medication_exists_join(ef, alias)
        else:
            j, w, p = _condition_join(ef, alias)
        joins.append(j)
        where.append(w)
        params.extend(p)

    if spec.encounter_filter:
        enc = spec.encounter_filter
        j = "JOIN encounters AS enc ON enc.patient_id = p.patient_id"
        joins.append(j)
        if enc.encounter_type:
            where.append("enc.type = ?")
            params.append(enc.encounter_type)
        if enc.year:
            where.append(f"strftime('%Y', enc.start_date) = '{enc.year}'")

    join_sql = "\n    ".join(joins)
    where_sql = " AND ".join(where)
    sql = f"""
    SELECT DISTINCT p.patient_id
    FROM patients AS p
    {join_sql}
    WHERE {where_sql}
    """
    return sql.strip(), params


# ---------------------------------------------------------------------------
# Group-specific runners
# ---------------------------------------------------------------------------


def _run_cohort_size(conn: sqlite3.Connection, spec: QuerySpec) -> list[dict[str, Any]]:
    """Return a single row: {group: 'cohort', _count_cohort_size: N}."""
    sql, params = _build_cohort_sql(spec)
    count_sql = f"SELECT COUNT(*) FROM ({sql})"
    row = conn.execute(count_sql, params).fetchone()
    count = row[0] if row else 0
    return [{"group": "cohort", "_count_cohort_size": count}]


def _run_encounter_count(conn: sqlite3.Connection, spec: QuerySpec) -> list[dict[str, Any]]:
    """Count inpatient stays (not patients) by region."""
    ef = spec.encounter_filter
    where: list[str] = ["e.type = ?", "p.opted_out = 0"]
    params: list[Any] = [ef.encounter_type if ef and ef.encounter_type else "inpatient"]
    if ef and ef.year:
        where.append(f"strftime('%Y', e.start_date) = '{ef.year}'")

    rows_sql = f"""
    SELECT p.region AS grp, COUNT(*) AS n
    FROM encounters AS e
    JOIN patients AS p ON p.patient_id = e.patient_id
    WHERE {' AND '.join(where)}
    GROUP BY p.region
    """
    rows = conn.execute(rows_sql, params).fetchall()
    counts: dict[str, int] = {r[0]: r[1] for r in rows}
    result = []
    for region in _AUSTRIAN_REGIONS:
        result.append({"group": region, "_count_inpatient_stays": counts.get(region, 0)})
    return result


def _run_single_group(conn: sqlite3.Connection, spec: QuerySpec) -> list[dict[str, Any]]:
    cohort_sql, cohort_params = _build_cohort_sql(spec)
    row: dict[str, Any] = {"group": "total"}
    _compute_measures(conn, spec, cohort_sql, cohort_params, row)
    return [row]


def _run_by_region(conn: sqlite3.Connection, spec: QuerySpec) -> list[dict[str, Any]]:
    regions = _AUSTRIAN_REGIONS
    result = []
    for region in regions:
        cohort_sql, cohort_params = _build_cohort_sql_with_region(spec, region)
        row: dict[str, Any] = {"group": region}
        _compute_measures(conn, spec, cohort_sql, cohort_params, row)
        result.append(row)
    return result


def _run_by_age_band(conn: sqlite3.Connection, spec: QuerySpec) -> list[dict[str, Any]]:
    gb = spec.group_by
    assert gb and gb.bands and gb.labels
    result = []
    for (lo, hi), label in zip(gb.bands, gb.labels):
        cohort_sql, cohort_params = _build_cohort_sql_with_age_band(spec, lo, hi)
        row: dict[str, Any] = {"group": label}
        _compute_measures(conn, spec, cohort_sql, cohort_params, row)
        result.append(row)
    return result


def _run_by_data_holder(conn: sqlite3.Connection, spec: QuerySpec) -> list[dict[str, Any]]:
    holders = ["Klinikum Nord", "Klinikum Süd", "Klinikum West"]
    result = []
    for holder in holders:
        cohort_sql, cohort_params = _build_cohort_sql_with_data_holder(spec, holder)
        row: dict[str, Any] = {"group": holder}
        _compute_measures(conn, spec, cohort_sql, cohort_params, row)
        result.append(row)
    return result


# ---------------------------------------------------------------------------
# Cohort SQL variants (region / age band / data holder added as extra filter)
# ---------------------------------------------------------------------------


def _build_cohort_sql_with_region(spec: QuerySpec, region: str) -> tuple[str, list[Any]]:
    sql, params = _build_cohort_sql(spec)
    # Inject a WHERE clause for region (p.region is already in the base query)
    new_sql = sql + "\n    AND p.region = ?"
    params = params + [region]
    return new_sql, params


def _build_cohort_sql_with_age_band(
    spec: QuerySpec, lo: int | None, hi: int | None
) -> tuple[str, list[Any]]:
    sql, params = _build_cohort_sql(spec)
    parts = []
    extra_params: list[Any] = []
    if lo is not None:
        parts.append(f"({_REF_YEAR} - CAST(strftime('%Y', p.birth_date) AS INTEGER)) >= ?")
        extra_params.append(lo)
    if hi is not None:
        parts.append(f"({_REF_YEAR} - CAST(strftime('%Y', p.birth_date) AS INTEGER)) <= ?")
        extra_params.append(hi)
    if parts:
        sql = sql + "\n    AND " + " AND ".join(parts)
        params = params + extra_params
    return sql, params


def _build_cohort_sql_with_data_holder(spec: QuerySpec, holder: str) -> tuple[str, list[Any]]:
    sql, params = _build_cohort_sql(spec)
    sql = sql + "\n    AND p.data_holder = ?"
    params = params + [holder]
    return sql, params


# ---------------------------------------------------------------------------
# Measure computation
# ---------------------------------------------------------------------------


def _compute_measures(
    conn: sqlite3.Connection,
    spec: QuerySpec,
    cohort_sql: str,
    cohort_params: list[Any],
    row: dict[str, Any],
) -> None:
    """Fill `row` with raw (unmasked) metric values for each measure in spec."""
    for measure in spec.measures:
        if measure.kind == MeasureKind.COUNT:
            _compute_count(conn, spec, measure, cohort_sql, cohort_params, row)
        elif measure.kind == MeasureKind.PERCENTAGE:
            _compute_percentage(conn, spec, measure, cohort_sql, cohort_params, row)
        elif measure.kind == MeasureKind.MEAN:
            _compute_mean(conn, spec, measure, cohort_sql, cohort_params, row)


def _count_cohort(conn: sqlite3.Connection, cohort_sql: str, params: list[Any]) -> int:
    sql = f"SELECT COUNT(*) FROM ({cohort_sql})"
    r = conn.execute(sql, params).fetchone()
    return r[0] if r else 0


def _compute_count(
    conn: sqlite3.Connection,
    spec: QuerySpec,
    measure: Measure,
    cohort_sql: str,
    cohort_params: list[Any],
    row: dict[str, Any],
) -> None:
    key = f"_count_{measure.name}"

    if measure.numerator_filter is None:
        # Count the whole cohort
        row[key] = _count_cohort(conn, cohort_sql, cohort_params)
        return

    cf = measure.numerator_filter
    # "without_oac" is a special inverted count
    if measure.name == "without_oac":
        # Patients in cohort WITHOUT active OAC
        has_oac_sql, has_oac_params = _build_has_medication_sql(cohort_sql, cf)
        total = _count_cohort(conn, cohort_sql, cohort_params)
        with_oac = conn.execute(
            f"SELECT COUNT(*) FROM ({has_oac_sql})", cohort_params + has_oac_params
        ).fetchone()[0]
        row[key] = total - with_oac
        # Store total for share calculation
        row["_af_patients"] = total
        return

    # Positive count: patients in cohort who also have the filter
    if cf.table == "conditions":
        prefix_conds = " OR ".join([f"cond2.icd10 LIKE ?" for _ in cf.prefixes])
        filter_params = [f"{p}%" for p in cf.prefixes]
        sql = f"""
        SELECT COUNT(DISTINCT c.patient_id) FROM ({cohort_sql}) AS c
        JOIN conditions AS cond2 ON cond2.patient_id = c.patient_id
        WHERE ({prefix_conds})
        """
        count = conn.execute(sql, cohort_params + filter_params).fetchone()[0]
        row[key] = count
    elif cf.table == "medications":
        prefix_conds = " OR ".join([f"med2.atc LIKE ?" for _ in cf.prefixes])
        filter_params: list[Any] = [f"{p}%" for p in cf.prefixes]
        active_clause = ""
        if cf.active_only:
            active_clause = f" AND (med2.start_date <= '{_REF_DATE}' AND (med2.end_date IS NULL OR med2.end_date = '' OR med2.end_date >= '{_REF_DATE}'))"
        sql = f"""
        SELECT COUNT(DISTINCT c.patient_id) FROM ({cohort_sql}) AS c
        JOIN medications AS med2 ON med2.patient_id = c.patient_id
        WHERE ({prefix_conds}){active_clause}
        """
        count = conn.execute(sql, cohort_params + filter_params).fetchone()[0]
        row[key] = count
    elif cf.table == "observations":
        prefix_conds = " OR ".join([f"obs2.loinc LIKE ?" for _ in cf.prefixes])
        filter_params = [f"{p}%" for p in cf.prefixes]
        sql = f"""
        SELECT COUNT(DISTINCT c.patient_id) FROM ({cohort_sql}) AS c
        JOIN observations AS obs2 ON obs2.patient_id = c.patient_id
        WHERE ({prefix_conds})
        """
        count = conn.execute(sql, cohort_params + filter_params).fetchone()[0]
        row[key] = count
    else:
        row[key] = 0


def _build_has_medication_sql(cohort_sql: str, cf: CodeFilter) -> tuple[str, list[Any]]:
    """Build SQL that returns patients in the cohort who HAVE a matching active medication."""
    prefix_conds = " OR ".join([f"m.atc LIKE ?" for _ in cf.prefixes])
    params: list[Any] = [f"{p}%" for p in cf.prefixes]
    active_clause = ""
    if cf.active_only:
        active_clause = f" AND (m.start_date <= '{_REF_DATE}' AND (m.end_date IS NULL OR m.end_date = '' OR m.end_date >= '{_REF_DATE}'))"
    sql = f"""
    SELECT DISTINCT c.patient_id FROM ({cohort_sql}) AS c
    JOIN medications AS m ON m.patient_id = c.patient_id
    WHERE ({prefix_conds}){active_clause}
    """
    return sql, params


def _compute_percentage(
    conn: sqlite3.Connection,
    spec: QuerySpec,
    measure: Measure,
    cohort_sql: str,
    cohort_params: list[Any],
    row: dict[str, Any],
) -> None:
    name = measure.name

    if name == "prevalence_pct":
        # denominator = whole cohort (all adults in the region)
        denom = _count_cohort(conn, cohort_sql, cohort_params)
        # numerator = adults with the condition
        # First check spec.condition_filter, then look for a COUNT measure with
        # numerator_filter (e.g. type2_diabetes in R01 where cohort is all adults)
        cf = spec.condition_filter
        if cf is None:
            # Find the first condition COUNT measure that has a numerator_filter
            for m in spec.measures:
                if m.kind == MeasureKind.COUNT and m.numerator_filter is not None:
                    if m.numerator_filter.table == "conditions":
                        cf = m.numerator_filter
                        break
        if cf:
            prefix_conds = " OR ".join([f"cond3.icd10 LIKE ?" for _ in cf.prefixes])
            filter_params = [f"{p}%" for p in cf.prefixes]
            num_sql = f"""
            SELECT COUNT(DISTINCT c.patient_id) FROM ({cohort_sql}) AS c
            JOIN conditions AS cond3 ON cond3.patient_id = c.patient_id
            WHERE ({prefix_conds})
            """
            num = conn.execute(num_sql, cohort_params + filter_params).fetchone()[0]
        else:
            num = denom
        row[f"_pct_{name}_num"] = num
        row[f"_pct_{name}_den"] = denom

    elif name == "share_without_oac_pct":
        # Use already-stored _af_patients if available
        total = row.get("_af_patients", _count_cohort(conn, cohort_sql, cohort_params))
        without = row.get("_count_without_oac", 0)
        row[f"_pct_{name}_num"] = without
        row[f"_pct_{name}_den"] = total

    elif name in ("ace_inhibitor_pct", "arb_pct"):
        # denominator = whole cohort (adults with hypertension in the band)
        denom = _count_cohort(conn, cohort_sql, cohort_params)
        atc_prefixes = {"ace_inhibitor_pct": ["C09AA"], "arb_pct": ["C09CA"]}[name]
        prefix_conds = " OR ".join([f"med3.atc LIKE ?" for _ in atc_prefixes])
        filter_params = [f"{p}%" for p in atc_prefixes]
        num_sql = f"""
        SELECT COUNT(DISTINCT c.patient_id) FROM ({cohort_sql}) AS c
        JOIN medications AS med3 ON med3.patient_id = c.patient_id
        WHERE ({prefix_conds})
        AND (med3.start_date <= '{_REF_DATE}'
             AND (med3.end_date IS NULL OR med3.end_date = '' OR med3.end_date >= '{_REF_DATE}'))
        """
        num = conn.execute(num_sql, cohort_params + filter_params).fetchone()[0]
        row[f"_pct_{name}_num"] = num
        row[f"_pct_{name}_den"] = denom

    else:
        row[f"_pct_{name}_num"] = 0
        row[f"_pct_{name}_den"] = 0


def _compute_mean(
    conn: sqlite3.Connection,
    spec: QuerySpec,
    measure: Measure,
    cohort_sql: str,
    cohort_params: list[Any],
    row: dict[str, Any],
) -> None:
    name = measure.name
    loincs = measure.obs_loinc or []
    if not loincs:
        row[f"_mean_{name}_val"] = 0.0
        row[f"_mean_{name}_n"] = 0
        return

    loinc_conds = " OR ".join([f"obs.loinc = ?" for _ in loincs])
    params: list[Any] = list(loincs)

    if measure.obs_latest:
        # Use the latest value per patient per LOINC
        # Note: loinc_conds uses alias "o" (matches the CTE FROM alias)
        loinc_conds_o = " OR ".join([f"o.loinc = ?" for _ in loincs])
        latest_sql = f"""
        WITH latest AS (
            SELECT o.patient_id, o.loinc, o.value,
                   ROW_NUMBER() OVER (PARTITION BY o.patient_id, o.loinc ORDER BY o.date DESC) AS rn
            FROM observations AS o
            WHERE ({loinc_conds_o})
        )
        SELECT AVG(l.value) AS mean_val, COUNT(*) AS n
        FROM ({cohort_sql}) AS c
        JOIN latest AS l ON l.patient_id = c.patient_id AND l.rn = 1
        """
        r = conn.execute(latest_sql, params + cohort_params).fetchone()
    else:
        sql = f"""
        SELECT AVG(obs.value) AS mean_val, COUNT(DISTINCT c.patient_id) AS n
        FROM ({cohort_sql}) AS c
        JOIN observations AS obs ON obs.patient_id = c.patient_id
        WHERE ({loinc_conds})
        """
        r = conn.execute(sql, params + cohort_params).fetchone()

    mean_val = r[0] if r and r[0] is not None else 0.0
    n = r[1] if r and r[1] is not None else 0
    row[f"_mean_{name}_val"] = float(mean_val)
    row[f"_mean_{name}_n"] = int(n)
