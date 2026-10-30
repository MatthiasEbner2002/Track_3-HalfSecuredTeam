"""Compile a QuerySpec to SQLite SQL and turn the rows into the answer.

Each concept kind has one fixed binding to the data; nothing is matched by
column name. Opted-out patients are always excluded. Percentages and means are
computed in Python with Decimal (half up), never with SQLite round().

Answer shapes:
  statistics: {group_key: {metric_key: value}}; group_key is the group value,
              "A|B" for two dimensions, "all" without grouping; metric_key is
              the phrase from the "Report per" line as a snake_case slug, in
              that order
  spe:        {"cohort_size": n, "record_types": [...]}
"""

import itertools
import re
import sqlite3
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app import db
from app.config import REFERENCE_DATE
from app.layers import small_cell
from app.layers import llm_mapping
from app.layers.mapping import InterpretError, NoDataError, check_codes, parse
from app.models import (
    CodeMapping,
    Concept,
    DataRequest,
    Filter,
    Query,
    QueryPlan,
    QuerySpec,
    ResultSpec,
    Route,
)

REF_YEAR = int(REFERENCE_DATE[:4])
GROUP_COLUMNS = {"region": "region", "hospital": "data_holder", "sex": "sex", "band": "band"}
SCALE = 1000  # means are summed as integers in thousandths, so the sum is exact


class _Sql:
    """Collects named parameters; :p1, :p2 ... so the order of p() calls does not matter."""

    def __init__(self) -> None:
        self.params: dict[str, Any] = {}

    def p(self, value: Any) -> str:
        key = f"p{len(self.params) + 1}"
        self.params[key] = value
        return f":{key}"


def _like_any(sql: _Sql, column: str, codes: list[str]) -> str:
    return "(" + " OR ".join(f"{column} LIKE {sql.p(code + '%')}" for code in codes) + ")"


def _encounter_cond(sql: _Sql, c: Concept, alias: str) -> str:
    parts = []
    if c.encounter_type:
        parts.append(f"{alias}.type = {sql.p(c.encounter_type)}")
    if c.codes:
        parts.append(_like_any(sql, f"{alias}.primary_icd10", c.codes))
    if c.year:
        parts.append(f"substr({alias}.start_date, 1, 4) = {sql.p(str(c.year))}")
    return " AND ".join(parts) or "1"


def _latest_cte(sql: _Sql, loinc: str) -> str:
    """Newest value per patient for one LOINC (at most one value per patient, test and day)."""
    return ("SELECT patient_id, value FROM (SELECT patient_id, value, ROW_NUMBER() OVER"
            " (PARTITION BY patient_id ORDER BY date DESC) AS rn FROM observations"
            f" WHERE loinc = {sql.p(loinc)}) WHERE rn = 1")


def _patient_set(sql: _Sql, c: Concept, latest: dict[str, str]) -> str | None:
    """Patient IDs with the concept, as a non-correlated query (computed once, not per row)."""
    if c.kind == "diagnosis":
        year = f" AND substr(onset_date, 1, 4) = {sql.p(str(c.year))}" if c.year else ""
        return f"SELECT patient_id FROM conditions WHERE {_like_any(sql, 'icd10', c.codes)}{year}"
    if c.kind == "drug":
        active = ""
        if c.active:
            active = (f" AND start_date <= {sql.p(REFERENCE_DATE)}"
                      f" AND (end_date IS NULL OR end_date >= {sql.p(REFERENCE_DATE)})")
        return f"SELECT patient_id FROM medications WHERE {_like_any(sql, 'atc', c.codes)}{active}"
    if c.kind == "measurement":
        if c.op is None:
            return f"SELECT patient_id FROM observations WHERE loinc = {sql.p(c.codes[0])}"
        return f"SELECT patient_id FROM {latest[c.codes[0]]} WHERE value {c.op} {sql.p(c.threshold)}"
    if c.kind == "encounter":
        return f"SELECT patient_id FROM encounters e WHERE {_encounter_cond(sql, c, 'e')}"
    return None


def _row_expr(sql: _Sql, c: Concept) -> str:
    """Age, sex, region, hospital: read straight from the patient row."""
    if c.kind == "age":
        parts = []
        if c.min_age is not None:
            parts.append(f"age >= {sql.p(c.min_age)}")
        if c.max_age is not None:
            parts.append(f"age <= {sql.p(c.max_age)}")
        return "(" + " AND ".join(parts) + ")"
    column = {"sex": "sex", "region": "region", "hospital": "data_holder"}[c.kind]
    return f"({column} = {sql.p(c.value)})"


def _bool(f: Filter | None, flag: dict[str, str]) -> str:
    """AND of OR-groups over flag columns (or expressions)."""
    if not f:
        return "1"
    groups = []
    for g in f:
        terms = [f"NOT {flag[t.concept]}" if t.negate else flag[t.concept] for t in g]
        groups.append("(" + " OR ".join(terms) + ")")
    return " AND ".join(groups)


def _concepts(f: Filter | None) -> list[str]:
    return [t.concept for g in f or [] for t in g]


def compile_spec(spec: QuerySpec) -> QueryPlan:
    """QuerySpec -> SQL. Statistics: one aggregate row per group; SPE: the cohort's patient IDs.

    Each concept becomes one CTE of patient IDs and one 0/1 column in `flagged`;
    each latest measurement one CTE joined once. No correlated subqueries.
    """
    sql = _Sql()
    band_case = "NULL"
    if spec.bands:
        whens = []
        for band in spec.bands:
            upper = f" AND age <= {sql.p(band.max_age)}" if band.max_age is not None else ""
            whens.append(f"WHEN age >= {sql.p(band.min_age)}{upper} THEN {sql.p(band.label)}")
        band_case = "CASE " + " ".join(whens) + " END"
    ctes = [
        "base AS (SELECT patient_id, region, data_holder, sex,"
        f" {REF_YEAR} - CAST(substr(birth_date, 1, 4) AS INTEGER) AS age FROM patients WHERE opted_out = 0)",
        f"banded AS (SELECT *, {band_case} AS band FROM base)",
    ]

    # patient-level flags: every concept used, except encounters counted as stays
    stay_metrics = [m for m in spec.metrics if m.unit == "stays"]
    stay_level = {n for m in stay_metrics for n in _concepts(m.where) + _concepts(m.denominator)
                  if spec.concepts[n].kind == "encounter"}
    used = dict.fromkeys(_concepts(spec.cohort))
    for m in spec.metrics:
        names = _concepts(m.where) + _concepts(m.denominator)
        used.update(dict.fromkeys(n for n in names if not (m.unit == "stays" and n in stay_level)))
    means = list(dict.fromkeys(m.measurement for m in spec.metrics if m.op == "mean" and m.measurement))

    # composite concepts need their parts computed too; parts come first
    with_parts: dict[str, None] = {}

    def add(name: str) -> None:
        c = spec.concepts[name]
        if c.kind == "composite":
            for part in _concepts(c.where):
                add(part)
        with_parts.setdefault(name)

    for name in used:
        add(name)

    latest: dict[str, str] = {}
    for loinc in dict.fromkeys(spec.concepts[n].codes[0] for n in [*with_parts, *means]
                               if spec.concepts[n].kind == "measurement"
                               and (spec.concepts[n].op is not None or n in means)):
        latest[loinc] = f"l{len(latest)}"
        ctes.append(f"{latest[loinc]} AS ({_latest_cte(sql, loinc)})")

    expr: dict[str, str] = {}
    for i, name in enumerate(with_parts):
        c = spec.concepts[name]
        if c.kind == "composite":
            expr[name] = f"({_bool(c.where, expr)})"
        elif (subquery := _patient_set(sql, c, latest)) is not None:
            ctes.append(f"s{i} AS ({subquery})")
            expr[name] = f"(b.patient_id IN (SELECT patient_id FROM s{i}))"
        else:
            expr[name] = _row_expr(sql, c)
    flag = {name: f"t{i}" for i, name in enumerate(used)}
    columns = [f"{expr[name]} AS {col}" for name, col in flag.items()]
    value = {}
    joins = ""
    for i, name in enumerate(means):
        value[name] = f"v{i}"
        alias = latest[spec.concepts[name].codes[0]]
        columns.append(f"{alias}.value AS v{i}")
        joins += f" LEFT JOIN {alias} ON {alias}.patient_id = b.patient_id"
    where = " WHERE band IS NOT NULL" if "band" in spec.group_by else ""
    ctes.append(f"flagged AS (SELECT b.*{''.join(', ' + c for c in columns)} FROM banded b{joins}{where})")
    head = "WITH " + ",\n".join(ctes)

    cohort = _bool(spec.cohort, flag)
    if spec.mode == "spe":
        cohort_sql = f"{head}\nSELECT patient_id FROM flagged WHERE {cohort} ORDER BY patient_id"
        return QueryPlan(queries=[Query(name="cohort", sql=cohort_sql, params=sql.params)])

    groups = [f"{GROUP_COLUMNS[d]} AS g{i}" for i, d in enumerate(spec.group_by)]
    group_by = f" GROUP BY {', '.join(f'g{i}' for i in range(len(groups)))}" if groups else ""
    select_groups = "".join(g + ", " for g in groups)

    sums = []
    for i, m in enumerate(spec.metrics):
        if m.unit == "stays":
            continue
        cond = _bool(m.where, flag)
        if m.op == "count":
            sums.append(f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) AS m{i}")
        elif m.op == "percent":
            sums.append(f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END) AS m{i}_num")
            sums.append(f"SUM(CASE WHEN {_bool(m.denominator, flag)} THEN 1 ELSE 0 END) AS m{i}_den")
        else:
            v = value[m.measurement]
            sums.append(f"SUM(CASE WHEN {cond} AND {v} IS NOT NULL THEN 1 ELSE 0 END) AS m{i}_n")
            sums.append(f"SUM(CASE WHEN {cond} AND {v} IS NOT NULL"
                        f" THEN CAST(ROUND({v} * {SCALE}) AS INTEGER) ELSE 0 END) AS m{i}_sum")
    queries = []
    if sums:
        queries.append(Query(
            name="patients",
            sql=f"{head}\nSELECT {select_groups}{', '.join(sums)} FROM flagged WHERE {cohort}{group_by}",
            params=dict(sql.params),
        ))
    if stay_metrics:
        # stays: encounter conditions on the stay, everything else on its patient
        stay_flag = {n: f"k.{col}" for n, col in flag.items()}
        stay_flag.update({n: f"({_encounter_cond(sql, spec.concepts[n], 'e')})" for n in stay_level})
        sums = []
        for i, m in enumerate(spec.metrics):
            if m.unit != "stays":
                continue
            if m.op == "percent":
                sums.append(f"SUM(CASE WHEN {_bool(m.where, stay_flag)} THEN 1 ELSE 0 END) AS m{i}_num")
                sums.append(f"SUM(CASE WHEN {_bool(m.denominator, stay_flag)} THEN 1 ELSE 0 END) AS m{i}_den")
            else:
                sums.append(f"SUM(CASE WHEN {_bool(m.where, stay_flag)} THEN 1 ELSE 0 END) AS m{i}")
        k_groups = [f"k.{GROUP_COLUMNS[d]} AS g{i}" for i, d in enumerate(spec.group_by)]
        queries.append(Query(
            name="stays",
            sql=(f"{head}\nSELECT {''.join(g + ', ' for g in k_groups)}{', '.join(sums)}"
                 f" FROM encounters e JOIN flagged k ON k.patient_id = e.patient_id"
                 f" WHERE {_bool(spec.cohort, stay_flag)}{group_by}"),
            params=dict(sql.params),
        ))
    return QueryPlan(queries=queries)


def _half_up(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def metric_keys(spec: QuerySpec) -> list[str]:
    """One snake_case key per metric from its label ("% with an active ARB" -> "pct_with_an_active_arb")."""
    keys: list[str] = []
    for m in spec.metrics:
        key = re.sub(r"[^a-z0-9]+", "_", m.label.lower().replace("%", " pct ")).strip("_")
        key = re.sub(r"^the_", "", key) or "value"
        base, n = key, 2
        while key in keys:
            key, n = f"{base}_{n}", n + 1
        keys.append(key)
    return keys


def _group_values(spec: QuerySpec, conn: sqlite3.Connection) -> list[list[str]]:
    out = []
    for dim in spec.group_by:
        if dim == "band":
            out.append([b.label for b in spec.bands])
        else:
            col = GROUP_COLUMNS[dim]
            out.append([v for (v,) in conn.execute(f"SELECT DISTINCT {col} FROM patients ORDER BY {col}")])
    return out


def answer(spec: QuerySpec, plan: QueryPlan, rows: dict[str, list[dict]], conn: sqlite3.Connection) -> dict:
    """Rows -> answer, with the full group list and the small-cell rule."""
    if spec.mode == "spe":
        return {"cohort_size": len(rows["cohort"]), "record_types": spec.record_types}

    by_group: dict[tuple, dict] = {}
    for name in ("patients", "stays"):
        for row in rows.get(name, []):
            key = tuple(row.get(f"g{i}") for i in range(len(spec.group_by)))
            by_group.setdefault(key, {}).update(row)

    keys = metric_keys(spec)
    result = {}
    for key in itertools.product(*_group_values(spec, conn)):
        row = by_group.get(key, {})
        values: list[Any] = []
        for i, m in enumerate(spec.metrics):
            if m.op == "count":
                values.append(small_cell.count(row.get(f"m{i}") or 0))
            elif m.op == "percent":
                num, den = row.get(f"m{i}_num") or 0, row.get(f"m{i}_den") or 0
                pct = _half_up(Decimal(num) * 100 / Decimal(den)) if den else None
                values.append(small_cell.derived(pct, min(num, den)))  # numerator or denominator < 10
            else:
                n, total = row.get(f"m{i}_n") or 0, row.get(f"m{i}_sum") or 0
                mean = _half_up(Decimal(total) / SCALE / n) if n else None
                values.append(small_cell.derived(mean, n))
        result["|".join(key) if key else "all"] = dict(zip(keys, values))
    return result


def interpret(request: DataRequest, mode: Route, conn: sqlite3.Connection) -> tuple[QuerySpec, str]:
    """Rules first; where they give up, Bob (stage 2). Returns the spec and who made it ("rules" / "llm")."""
    try:
        spec = parse(request, mode, conn)
        check_codes(spec, conn)
        return spec, "rules"
    except NoDataError:
        raise
    except InterpretError as e:
        if not llm_mapping.available():
            raise
        return llm_mapping.interpret(request, mode, conn, reason=str(e)), "llm"


def extract(request: DataRequest, mode: Route, conn: sqlite3.Connection) -> dict:
    """Request -> QuerySpec -> SQL -> answer. Raises InterpretError if not understood."""
    spec, _source = interpret(request, mode, conn)
    plan = compile_spec(spec)
    rows = {q.name: db.execute(conn, q.sql, q.params) for q in plan.queries}
    return answer(spec, plan, rows, conn)


def generate(spec: ResultSpec, mapping: CodeMapping) -> QueryPlan:
    """Pipeline hook: the SQL for an approved request."""
    conn = db.connect()
    try:
        return compile_spec(interpret(spec.request, spec.route, conn)[0])
    finally:
        conn.close()
