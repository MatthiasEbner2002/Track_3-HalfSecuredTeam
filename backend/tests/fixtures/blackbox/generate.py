"""Generate the black-box test cases B001..B100 with answers computed directly from the db.

This script does NOT use the engine (app.layers.*). Every expected answer is computed
here from plain SQL + Python, following the answer contract:

  statistics: {group_key: [values in "Report per" order]}, small-cell rule applied
  spe:        {"cohort_size": int, "record_types": [sorted table names]}
  error:      {"error": true}

Run:  python3 tests/fixtures/blackbox/generate.py   (from backend/)
It also checks that B001..B006 reproduce requests/expected.json.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
DB = REPO / "data" / "ehds.db"
REQ = REPO / "requests"
OUT = HERE / "cases"

conn = sqlite3.connect(DB)
REF = "2025-12-31"

# ---------------------------------------------------------------- base data
BASE: dict[str, dict] = {}
for pid, sex, bd, region, holder in conn.execute(
    "SELECT patient_id, sex, birth_date, region, data_holder FROM patients WHERE opted_out = 0"
):
    BASE[pid] = {"sex": sex, "age": 2025 - int(bd[:4]), "region": region, "hospital": holder}
ALL = set(BASE)
REGIONS = sorted(r for (r,) in conn.execute("SELECT DISTINCT region FROM patients"))
HOSPITALS = sorted(h for (h,) in conn.execute("SELECT DISTINCT data_holder FROM patients"))
SEXES = sorted(s for (s,) in conn.execute("SELECT DISTINCT sex FROM patients"))
assert len(REGIONS) == 9 and len(HOSPITALS) == 3 and SEXES == ["female", "male"]

WARNINGS: list[str] = []


def _pids(sql, params=()):
    return {r[0] for r in conn.execute(sql, params)} & ALL


# ---------------------------------------------------------------- concept helpers
def dx(*prefixes, year=None, onset_from=None, onset_to=None):
    out = set()
    for p in prefixes:
        sql = "SELECT patient_id FROM conditions WHERE icd10 LIKE ?"
        params = [p + "%"]
        if year is not None:
            sql += " AND substr(onset_date,1,4) = ?"
            params.append(str(year))
        if onset_from:
            sql += " AND onset_date >= ?"
            params.append(onset_from)
        if onset_to:
            sql += " AND onset_date <= ?"
            params.append(onset_to)
        hit = _pids(sql, params)
        out |= hit
    return out


def med(*prefixes, active=True):
    out = set()
    for p in prefixes:
        sql = "SELECT patient_id FROM medications WHERE atc LIKE ?"
        if active:
            sql += " AND start_date <= ? AND (end_date IS NULL OR end_date >= ?)"
            out |= _pids(sql, (p + "%", REF, REF))
        else:
            out |= _pids(sql, (p + "%",))
    return out


_LATEST: dict[str, dict[str, float]] = {}


def latest(loinc):
    if loinc not in _LATEST:
        d = {}
        for pid, v, date in conn.execute(
            "SELECT patient_id, value, date FROM observations WHERE loinc = ? ORDER BY patient_id, date",
            (loinc,),
        ):
            if pid in BASE:
                d[pid] = v  # ordered by date -> last one wins
        _LATEST[loinc] = d
    return _LATEST[loinc]


def has_obs(loinc):
    return set(latest(loinc))


def lat_ge(loinc, thr):
    return {p for p, v in latest(loinc).items() if v >= thr}


def lat_lt(loinc, thr):
    return {p for p, v in latest(loinc).items() if v < thr}


def age_ge(n):
    return {p for p, b in BASE.items() if b["age"] >= n}


def age_lt(n):
    return {p for p, b in BASE.items() if b["age"] < n}


def age_between(lo, hi):
    return {p for p, b in BASE.items() if lo <= b["age"] <= hi}


def attr(name, value):
    return {p for p, b in BASE.items() if b[name] == value}


def female():
    return attr("sex", "female")


def male():
    return attr("sex", "male")


def stays(type=None, year=None, primary=None):
    """List of (encounter_id, patient_id) for non-opted-out patients."""
    sql = "SELECT encounter_id, patient_id FROM encounters WHERE 1=1"
    params = []
    if type is not None:
        types = type if isinstance(type, (list, tuple)) else [type]
        sql += " AND type IN (%s)" % ",".join("?" * len(types))
        params += list(types)
    if year is not None:
        sql += " AND substr(start_date,1,4) = ?"
        params.append(str(year))
    if primary is not None:
        prims = primary if isinstance(primary, (list, tuple)) else [primary]
        sql += " AND (%s)" % " OR ".join("primary_icd10 LIKE ?" for _ in prims)
        params += [p + "%" for p in prims]
    return [(e, p) for e, p in conn.execute(sql, params) if p in BASE]


def stay_pids(**kw):
    return {p for _, p in stays(**kw)}


def only(lst, pids):
    return [(e, p) for e, p in lst if p in pids]


# ---------------------------------------------------------------- rounding
def _q(d: Decimal) -> float:
    return float(d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def pct_value(n, d, ctx):
    exact = _q(Decimal(n) * 100 / Decimal(d))
    alt1 = _q(Decimal(repr(n / d * 100)))
    alt2 = _q(Decimal(repr(n * 100 / d)))
    if not (exact == alt1 == alt2):
        WARNINGS.append(f"{ctx}: pct {n}/{d} rounds differently exact={exact} alt={alt1},{alt2}")
    return exact


def mean_value(vals, ctx):
    exact = _q(sum(Decimal(repr(v)) for v in vals) / len(vals))
    alt = _q(Decimal(repr(sum(vals) / len(vals))))
    if exact != alt:
        WARNINGS.append(f"{ctx}: mean rounds differently exact={exact} float={alt}")
    return exact


# ---------------------------------------------------------------- metrics
class Count:
    def __init__(self, s):
        self.s = s

    def value(self, g, ctx):
        n = len(self.s & g)
        return n if n >= 10 else "<10"


class Stays:
    def __init__(self, lst):
        self.lst = lst

    def value(self, g, ctx):
        n = sum(1 for _, p in self.lst if p in g)
        return n if n >= 10 else "<10"


class Pct:
    def __init__(self, num, den):
        self.num, self.den = num, den

    def value(self, g, ctx):
        den = self.den & g
        if len(den) < 10:
            return "suppressed"
        return pct_value(len(self.num & den), len(den), ctx)


class PctStays:
    def __init__(self, num, den):
        self.num, self.den = set(num), den

    def value(self, g, ctx):
        den = [x for x in self.den if x[1] in g]
        if len(den) < 10:
            return "suppressed"
        num = sum(1 for x in den if x in self.num)
        return pct_value(num, len(den), ctx)


class Mean:
    def __init__(self, loinc, s):
        self.loinc, self.s = loinc, s

    def value(self, g, ctx):
        lv = latest(self.loinc)
        vals = [lv[p] for p in (self.s & g) if p in lv]
        if len(vals) < 10:
            return "suppressed"
        return mean_value(vals, ctx)


# ---------------------------------------------------------------- grouping
def _band_fn(labels):
    rng = []
    for lab in labels:
        if lab.endswith("+"):
            rng.append((int(lab[:-1]), 10**6, lab))
        else:
            lo, hi = lab.split("-")
            rng.append((int(lo), int(hi), lab))

    def f(pid):
        a = BASE[pid]["age"]
        for lo, hi, lab in rng:
            if lo <= a <= hi:
                return lab
        return None

    return list(labels), f


def dim(d):
    if d == "region":
        return REGIONS, lambda p: BASE[p]["region"]
    if d == "hospital":
        return HOSPITALS, lambda p: BASE[p]["hospital"]
    if d == "sex":
        return SEXES, lambda p: BASE[p]["sex"]
    return _band_fn(d)  # list of band labels


def stat(dims, *metrics, ctx=""):
    if not dims:
        return {"all": [m.value(ALL, ctx) for m in metrics]}
    resolved = [dim(d) for d in dims]
    groups: dict[str, set] = {}

    def keys(i):
        if i == len(resolved):
            yield []
            return
        for k in resolved[i][0]:
            for rest in keys(i + 1):
                yield [k] + rest

    for ks in keys(0):
        groups["|".join(ks)] = set()
    for p in BASE:
        ks = [f(p) for _, f in resolved]
        if None in ks:
            continue
        groups["|".join(ks)].add(p)
    return {k: [m.value(g, f"{ctx}/{k}") for m in metrics] for k, g in groups.items()}


def spe(cohort, *types):
    return {"cohort_size": len(cohort), "record_types": sorted(types)}


ERROR = {"error": True}

# ---------------------------------------------------------------- request text
APPLICANTS = [
    ("Dr. Sophie Lechner", "Danube Institute for Diabetes Research (university research group)", "AT"),
    ("Prof. Jan Novak", "Charles University, Department of Epidemiology", "CZ"),
    ("Dr. Elena Rossi", "Istituto Superiore di Sanità", "IT"),
    ("Mag. Thomas Huber", "Austrian Public Health Institute (GÖG)", "AT"),
    ("Dr. Anna Kowalska", "Medical University of Warsaw, Cardiology Research Unit", "PL"),
    ("Dr. Lukas Berger", "Statistik Austria, Health Statistics Unit", "AT"),
    ("Dr. Marie Dubois", "INSERM Clinical Epidemiology Team", "FR"),
    ("Dr. Pieter de Vries", "University Medical Center Utrecht, Julius Center", "NL"),
]
PURPOSES = [
    "Scientific research on the epidemiology of chronic diseases to inform prevention programmes.",
    "Public health monitoring of chronic disease burden for the regional health authorities.",
    "Health services research to improve the quality of care for patients with chronic conditions.",
    "Production of official health statistics for the national health report.",
    "Policy-making: planning hospital capacity and care pathways for the coming years.",
    "Scientific research on medication use and treatment gaps in routine care.",
]
SPE_PURPOSES = [
    "Training and validating a risk prediction model in a secure processing environment; only model weights and performance metrics are exported.",
    "Scientific research on treatment pathways that requires patient-level longitudinal records; only aggregated results leave the secure environment.",
    "Developing and testing a clinical decision-support algorithm; we will only export model parameters and evaluation metrics.",
]

ADULT = "Adult = aged 18 or older on 31 December 2025."
ACTIVE = (
    "A medication is active on 31 December 2025 if start_date is on or before that day "
    "and end_date is empty or on or after that day."
)

CASES: list[tuple[str, dict, dict]] = []  # (id, request, {"mode", "expected"})


def add(question, defs, expected, mode="statistics", requested_as=None):
    n = len(CASES) + 1
    cid = f"B{n:03d}"
    if requested_as is None:
        requested_as = "health data request" if mode == "statistics" else "health data access application"
    a = APPLICANTS[n % len(APPLICANTS)]
    spe_like = requested_as == "health data access application"
    purpose = (SPE_PURPOSES if spe_like else PURPOSES)[n % (3 if spe_like else len(PURPOSES))]
    req = {
        "id": cid,
        "submitted": "2026-10-29T18:30:00+01:00",
        "applicant": {"name": a[0], "organisation": a[1], "country": a[2]},
        "requested_as": requested_as,
        "requested_format": "pseudonymised data in a secure processing environment" if spe_like else "anonymised statistics",
        "purpose": purpose,
        "question": question,
        "definitions": defs,
    }
    CASES.append((cid, req, {"mode": mode, "expected": expected}))


def add_sample(rid, mode, expected):
    req = json.loads((REQ / f"{rid}.json").read_text(encoding="utf-8"))
    n = len(CASES) + 1
    cid = f"B{n:03d}"
    req["id"] = cid
    CASES.append((cid, req, {"mode": mode, "expected": expected}))


# ================================================================ B001-B006: samples
adults = age_ge(18)
t2d = dx("E11")
add_sample("R01", "statistics", stat(["region"], Count(adults), Count(adults & t2d), Pct(t2d, adults), ctx="R01"))
af = dx("I48")
oac = med("B01AA", "B01AF")
r03_band = ["65-74", "75-84", "85+"]
add_sample("R03", "statistics", stat([r03_band], Count(af), Count(af - oac), Pct(af - oac, af), ctx="R03"))
add_sample("R05", "spe", spe(adults & t2d & has_obs("4548-4"), "conditions", "medications", "observations"))
add_sample("R07", "statistics", stat(["region"], Stays(stays("inpatient", 2025)), ctx="R07"))
bc_w = dx("C50") & female()
add_sample("R08", "statistics", stat(["region"], Count(bc_w & has_obs("39156-5")), Mean("39156-5", bc_w), ctx="R08"))
htn_ad = adults & dx("I10")
add_sample(
    "R09",
    "statistics",
    stat([["18-49", "50-64", "65+"]], Count(htn_ad), Pct(med("C09AA"), htn_ad), Pct(med("C09CA"), htn_ad), ctx="R09"),
)

# ================================================================ own cases
# shared sets
htn = dx("I10")
obes = dx("E66")
asthma = dx("J45")
dep = dx("F32")
ckd = dx("N18")
copd = dx("J44")
hf = dx("I50")
stroke = dx("I63")
bc = dx("C50")
HBA, BMI, SBP, EGFR = "4548-4", "39156-5", "8480-6", "62238-1"

# --- 1. simple counts with each grouping
add(
    "How many patients have depression in each region?",
    ["Depression = any condition with an ICD-10 code starting with F32.", "Report per region: patients with depression."],
    stat(["region"], Count(dep)),
)
add(
    "How many adults with asthma does each hospital hold?",
    [ADULT, "Asthma = any condition with an ICD-10 code starting with J45.", "Report per hospital: adults with asthma."],
    stat(["hospital"], Count(adults & asthma)),
)
add(
    "How many patients have heart failure, by sex?",
    ["Heart failure = any condition with an ICD-10 code starting with I50.", "Report per sex: patients with heart failure."],
    stat(["sex"], Count(hf)),
)
add(
    "How many patients have breast cancer in total?",
    ["Breast cancer = any condition with an ICD-10 code starting with C50.", "Report: patients with breast cancer."],
    stat([], Count(bc)),
)
add(
    "How many patients have had a stroke?",
    ["Stroke = any condition with an ICD-10 code starting with I63.", "Report the number of patients with a stroke."],
    stat([], Count(stroke)),
)
add(
    "How many patients with chronic kidney disease are there per age band?",
    [
        "Chronic kidney disease = any condition with an ICD-10 code starting with N18.",
        "Age bands on 31 December 2025: 18-64, 65-79, 80+.",
        "Report per age band: patients with chronic kidney disease.",
    ],
    stat([["18-64", "65-79", "80+"]], Count(ckd)),
)

# --- 2. prevalence / percentages
add(
    "What is the prevalence of obesity among adults in each region?",
    [
        ADULT,
        "Obesity = any condition with an ICD-10 code starting with E66.",
        "Prevalence = adults with obesity / all adults in the region × 100, one decimal.",
        "Report per region: adults, adults with obesity and the prevalence.",
    ],
    stat(["region"], Count(adults), Count(adults & obes), Pct(obes, adults)),
)
statin = med("C10AA")
add(
    "What share of adults with hypertension has an active statin, per hospital?",
    [
        ADULT,
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "Statin = ATC code starting with C10AA.",
        ACTIVE,
        "Report per hospital: adults with hypertension, % with an active statin, one decimal.",
    ],
    stat(["hospital"], Count(htn_ad), Pct(statin, htn_ad)),
)
add(
    "How common is asthma in each age band?",
    [
        "Asthma = any condition with an ICD-10 code starting with J45.",
        "Age bands on 31 December 2025: 0-17, 18-39, 40-64, 65+.",
        "Prevalence = patients with asthma / all patients in the band × 100, one decimal.",
        "Report per band: patients, patients with asthma and the prevalence.",
    ],
    stat([["0-17", "18-39", "40-64", "65+"]], Count(ALL), Count(asthma), Pct(asthma, ALL)),
)
ssri = med("N06AB")
add(
    "What proportion of patients with depression has an active SSRI, by data holder?",
    [
        "Depression = any condition with an ICD-10 code starting with F32.",
        "SSRI = ATC code starting with N06AB.",
        ACTIVE,
        "Report per data holder: patients with depression and % with an active SSRI, one decimal.",
    ],
    stat(["hospital"], Count(dep), Pct(ssri, dep)),
)
phen = med("B01AA")
add(
    "Among patients with atrial fibrillation, what share takes phenprocoumon, per region?",
    [
        "Atrial fibrillation = any condition with an ICD-10 code starting with I48.",
        "Phenprocoumon = ATC code starting with B01AA.",
        ACTIVE,
        "Report per region: patients with atrial fibrillation and % with active phenprocoumon, one decimal.",
    ],
    stat(["region"], Count(af), Pct(phen, af)),
)
add(
    "What share of women has depression in each region?",
    [
        "Women = sex 'female'.",
        "Depression = any condition with an ICD-10 code starting with F32.",
        "Share = women with depression / all women in the region × 100, one decimal.",
        "Report per region: women, women with depression and the share.",
    ],
    stat(["region"], Count(female()), Count(female() & dep), Pct(dep, female())),
)
add(
    "Overall, how many adults take a statin?",
    [
        ADULT,
        "Statin = ATC code starting with C10AA.",
        ACTIVE,
        "Report: adults, adults with an active statin and the share in %, one decimal.",
    ],
    stat([], Count(adults), Count(adults & statin), Pct(statin, adults)),
)
sema = med("A10BJ")
sglt2 = med("A10BK")
add(
    "How many patients have an active SGLT2 inhibitor prescription, by region?",
    ["SGLT2 inhibitor = ATC code starting with A10BK.", ACTIVE, "Report per region: patients with an active SGLT2 inhibitor."],
    stat(["region"], Count(sglt2)),
)

# --- 3. means of the latest measurement (all four LOINCs)
add(
    "What is the average HbA1c of adults with type 2 diabetes by 10-year age band?",
    [
        ADULT,
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Age bands on 31 December 2025: 18-29, 30-39, 40-49, 50-59, 60-69, 70-79, 80+.",
        "Use the latest HbA1c (LOINC 4548-4) per patient.",
        "Report per band: adults with type 2 diabetes who have a HbA1c value and their mean HbA1c, one decimal.",
    ],
    stat(
        [["18-29", "30-39", "40-49", "50-59", "60-69", "70-79", "80+"]],
        Count(adults & t2d & has_obs(HBA)),
        Mean(HBA, adults & t2d),
    ),
)
add(
    "What is the mean systolic blood pressure of patients with hypertension in each region?",
    [
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "Use the latest systolic blood pressure (LOINC 8480-6) per patient; patients without a value are left out.",
        "Report per region: patients with hypertension who have a systolic value and their mean systolic blood pressure, one decimal.",
    ],
    stat(["region"], Count(htn & has_obs(SBP)), Mean(SBP, htn)),
)
add(
    "What is the mean eGFR of patients with chronic kidney disease per hospital?",
    [
        "Chronic kidney disease = any condition with an ICD-10 code starting with N18.",
        "Use the latest eGFR (LOINC 62238-1) per patient; patients without an eGFR are left out.",
        "Report per hospital: patients with chronic kidney disease who have an eGFR and their mean eGFR, one decimal.",
    ],
    stat(["hospital"], Count(ckd & has_obs(EGFR)), Mean(EGFR, ckd)),
)
add(
    "What is the mean BMI of adults with obesity, by sex?",
    [
        ADULT,
        "Obesity = any condition with an ICD-10 code starting with E66.",
        "Use the latest BMI (LOINC 39156-5) per patient; patients without a BMI are left out.",
        "Report per sex: adults with obesity who have a BMI and their mean BMI, one decimal.",
    ],
    stat(["sex"], Count(adults & obes & has_obs(BMI)), Mean(BMI, adults & obes)),
)
add(
    "What is the mean BMI of adults per age band?",
    [
        ADULT,
        "Age bands on 31 December 2025: 18-39, 40-59, 60-79, 80+.",
        "Use the latest BMI (LOINC 39156-5) per patient; patients without a BMI are left out.",
        "Report per age band: adults with a BMI and their mean BMI, one decimal.",
    ],
    stat([["18-39", "40-59", "60-79", "80+"]], Count(adults & has_obs(BMI)), Mean(BMI, adults)),
)
add(
    "What is the mean BMI of men with COPD, by region?",
    [
        "COPD = any condition with an ICD-10 code starting with J44.",
        "Men = sex 'male'.",
        "Use the latest BMI (LOINC 39156-5) per patient; patients without a BMI are left out.",
        "Report per region: number of men with COPD who have a BMI and their mean BMI, one decimal.",
    ],
    stat(["region"], Count(copd & male() & has_obs(BMI)), Mean(BMI, copd & male())),
)
add(
    "What is the mean eGFR of patients with chronic kidney disease, by region?",
    [
        "Chronic kidney disease = any condition with an ICD-10 code starting with N18.",
        "Use the latest eGFR (LOINC 62238-1) per patient; patients without an eGFR are left out.",
        "Report per region: patients with chronic kidney disease who have an eGFR and their mean eGFR, one decimal.",
    ],
    stat(["region"], Count(ckd & has_obs(EGFR)), Mean(EGFR, ckd)),
)

# --- 4. negation
loop = med("C03CA")
add(
    "How many patients with heart failure have no active loop diuretic, per hospital?",
    [
        "Heart failure = any condition with an ICD-10 code starting with I50.",
        "Loop diuretic = ATC code starting with C03CA.",
        ACTIVE,
        "Report per hospital: patients with heart failure, how many of them have no active loop diuretic and that share in %, one decimal.",
    ],
    stat(["hospital"], Count(hf), Count(hf - loop), Pct(hf - loop, hf)),
)
metf = med("A10BA")
add(
    "How many adults with type 2 diabetes are not on metformin, by region?",
    [
        ADULT,
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Metformin = ATC code starting with A10BA.",
        ACTIVE,
        "Report per region: adults with type 2 diabetes, how many of them have no active metformin and that share in %, one decimal.",
    ],
    stat(["region"], Count(adults & t2d), Count(adults & t2d - metf), Pct(t2d - metf, adults & t2d)),
)
add(
    "How many adults with hypertension do not take any ACE inhibitor or ARB, per age band?",
    [
        ADULT,
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "RAS blocker = ATC code starting with C09AA or C09CA.",
        ACTIVE,
        "Age bands on 31 December 2025: 18-49, 50-64, 65+.",
        "Report per band: adults with hypertension, how many of them have no active RAS blocker and that share in %, one decimal.",
    ],
    stat(
        [["18-49", "50-64", "65+"]],
        Count(htn_ad),
        Count(htn_ad - med("C09AA", "C09CA")),
        Pct(htn_ad - med("C09AA", "C09CA"), htn_ad),
    ),
)

# --- 5. AND of several concepts
add(
    "How many patients have both hypertension and type 2 diabetes, per region?",
    [
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Report per region: patients who have both hypertension and type 2 diabetes.",
    ],
    stat(["region"], Count(htn & t2d)),
)
add(
    "How many patients have hypertension, type 2 diabetes and chronic kidney disease, by hospital?",
    [
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Chronic kidney disease = any condition with an ICD-10 code starting with N18.",
        "Cardiorenal-metabolic patient = patient with hypertension and type 2 diabetes and chronic kidney disease.",
        "Report per hospital: cardiorenal-metabolic patients.",
    ],
    stat(["hospital"], Count(htn & t2d & ckd)),
)
add(
    "Among adults with type 2 diabetes and obesity, what share has an active SGLT2 inhibitor, by sex?",
    [
        ADULT,
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Obesity = any condition with an ICD-10 code starting with E66.",
        "SGLT2 inhibitor = ATC code starting with A10BK.",
        ACTIVE,
        "Cohort = adults with type 2 diabetes and obesity.",
        "Report per sex: patients in the cohort and % of them with an active SGLT2 inhibitor, one decimal.",
    ],
    stat(["sex"], Count(adults & t2d & obes), Pct(sglt2, adults & t2d & obes)),
)
add(
    "How many patients with heart failure and atrial fibrillation take both a beta blocker and an anticoagulant?",
    [
        "Heart failure = any condition with an ICD-10 code starting with I50.",
        "Atrial fibrillation = any condition with an ICD-10 code starting with I48.",
        "Beta blocker = ATC code starting with C07AB.",
        "Oral anticoagulant = ATC code starting with B01AA or B01AF.",
        ACTIVE,
        "Report: patients with heart failure and atrial fibrillation, and how many of them have both an active beta blocker and an active oral anticoagulant.",
    ],
    stat([], Count(hf & af), Count(hf & af & med("C07AB") & oac)),
)
glp1 = med("A10BJ")
add(
    "How many adults with type 2 diabetes receive neither an SGLT2 inhibitor nor a GLP-1 agonist, by hospital?",
    [
        ADULT,
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "SGLT2 inhibitor = ATC code starting with A10BK.",
        "GLP-1 agonist = ATC code starting with A10BJ.",
        ACTIVE,
        "Report per hospital: adults with type 2 diabetes, how many of them have neither an active SGLT2 inhibitor nor an active GLP-1 agonist, and that share in %, one decimal.",
    ],
    stat(
        ["hospital"],
        Count(adults & t2d),
        Count(adults & t2d - sglt2 - glp1),
        Pct(adults & t2d - sglt2 - glp1, adults & t2d),
    ),
)

# --- 6. OR via several prefixes
lung = dx("J44", "J45")
add(
    "How many patients have a chronic lung disease, by age band?",
    [
        "Chronic lung disease = any condition with an ICD-10 code starting with J44 or J45.",
        "Age bands on 31 December 2025: 0-17, 18-44, 45-64, 65+.",
        "Report per age band: patients with chronic lung disease.",
    ],
    stat([["0-17", "18-44", "45-64", "65+"]], Count(lung)),
)
dmmed = med("A10BA", "A10BK", "A10BJ")
add(
    "What share of patients with type 2 diabetes has any active oral or injectable glucose-lowering drug other than insulin, per region?",
    [
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Non-insulin diabetes drug = ATC code starting with A10BA, A10BK or A10BJ.",
        ACTIVE,
        "Report per region: patients with type 2 diabetes and % with an active non-insulin diabetes drug, one decimal.",
    ],
    stat(["region"], Count(t2d), Pct(dmmed, t2d)),
)
cvd = dx("I48", "I50", "I63")
add(
    "How many patients have cardiovascular disease, by sex?",
    [
        "Cardiovascular disease = any condition with an ICD-10 code starting with I48, I50 or I63.",
        "Report per sex: patients with cardiovascular disease.",
    ],
    stat(["sex"], Count(cvd)),
)
add(
    "How many patients have the type 2 diabetes code E11.6x (with complications), by region?",
    [
        "Complicated type 2 diabetes = any condition with an ICD-10 code starting with E11.6.",
        "Report per region: patients with complicated type 2 diabetes.",
    ],
    stat(["region"], Count(dx("E11.6"))),
)

# --- 7. measurement thresholds
poor = lat_ge(HBA, 8.0)
add(
    "How many patients with type 2 diabetes have poor glycaemic control, per hospital?",
    [
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Use the latest HbA1c (LOINC 4548-4) per patient.",
        "Poor control = latest HbA1c of 8.0 or higher.",
        "Report per hospital: patients with type 2 diabetes who have a HbA1c value, how many of them have poor control and that share in %, one decimal.",
    ],
    stat(["hospital"], Count(t2d & has_obs(HBA)), Count(t2d & poor), Pct(poor, t2d & has_obs(HBA))),
)
uncontrolled = lat_ge(SBP, 140)
add(
    "How many patients with hypertension have a systolic blood pressure of 140 or more, by age band?",
    [
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "Use the latest systolic blood pressure (LOINC 8480-6) per patient.",
        "Uncontrolled hypertension = latest systolic blood pressure of 140 or higher.",
        "Age bands on 31 December 2025: 18-49, 50-64, 65-79, 80+.",
        "Report per age band: patients with hypertension who have a systolic value, % with uncontrolled hypertension, one decimal.",
    ],
    stat([["18-49", "50-64", "65-79", "80+"]], Count(htn & has_obs(SBP)), Pct(uncontrolled, htn & has_obs(SBP))),
)
add(
    "How many patients with type 2 diabetes have an eGFR below 45, by region?",
    [
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Use the latest eGFR (LOINC 62238-1) per patient.",
        "Reduced kidney function = latest eGFR below 45.",
        "Report per region: patients with type 2 diabetes and reduced kidney function.",
    ],
    stat(["region"], Count(t2d & lat_lt(EGFR, 45))),
)
add(
    "What share of adults with a BMI measurement has a BMI of 30 or more, by sex?",
    [
        ADULT,
        "Use the latest BMI (LOINC 39156-5) per patient.",
        "BMI 30 or more = latest BMI of 30 or higher.",
        "Report per sex: adults with a BMI value and % with BMI 30 or more, one decimal.",
    ],
    stat(["sex"], Count(adults & has_obs(BMI)), Pct(lat_ge(BMI, 30), adults & has_obs(BMI))),
)

# --- 8. stays / encounters
add(
    "How many emergency visits were there in 2024 at each hospital?",
    [
        "Emergency visit = encounter with type 'emergency'.",
        "Count visits (not patients) whose start_date is in 2024.",
        "Report per hospital: emergency visits.",
    ],
    stat(["hospital"], Stays(stays("emergency", 2024))),
)
add(
    "How many inpatient stays had heart failure as main diagnosis, by region?",
    [
        "Heart failure stay = inpatient stay whose main diagnosis (primary_icd10) starts with I50.",
        "Count stays (not patients).",
        "Report per region: heart failure stays.",
    ],
    stat(["region"], Stays(stays("inpatient", primary="I50"))),
)
add(
    "How many inpatient stays in 2025 had a hip fracture as main diagnosis, by age band?",
    [
        "Hip fracture stay = inpatient stay whose main diagnosis (primary_icd10) starts with S72.",
        "Count stays (not patients) whose start_date is in 2025.",
        "Age bands on 31 December 2025: 0-64, 65-74, 75-84, 85+.",
        "Report per age band: hip fracture stays.",
    ],
    stat([["0-64", "65-74", "75-84", "85+"]], Stays(stays("inpatient", 2025, "S72"))),
)
add(
    "How many patients had at least one inpatient stay in 2025, by region?",
    [
        "Inpatient stay = encounter with type 'inpatient'.",
        "Hospitalised patient = patient with at least one inpatient stay whose start_date is in 2025.",
        "Report per region: hospitalised patients.",
    ],
    stat(["region"], Count(stay_pids(type="inpatient", year=2025))),
)
ip24 = stays("inpatient", 2024)
add(
    "What share of inpatient stays in 2024 had pneumonia as main diagnosis, per hospital?",
    [
        "Inpatient stay = encounter with type 'inpatient'.",
        "Pneumonia stay = inpatient stay whose main diagnosis (primary_icd10) starts with J18.",
        "Count stays (not patients) whose start_date is in 2024.",
        "Report per hospital: inpatient stays, pneumonia stays and the share of pneumonia stays in %, one decimal.",
    ],
    stat(["hospital"], Stays(ip24), Stays(stays("inpatient", 2024, "J18")), PctStays(stays("inpatient", 2024, "J18"), ip24)),
)
add(
    "How many emergency visits did patients with asthma make in 2025, by region?",
    [
        "Asthma = any condition with an ICD-10 code starting with J45.",
        "Emergency visit = encounter with type 'emergency'.",
        "Count visits (not patients) of patients with asthma whose start_date is in 2025.",
        "Report per region: emergency visits of patients with asthma.",
    ],
    stat(["region"], Stays(only(stays("emergency", 2025), asthma))),
)
add(
    "How many patients had an inpatient stay with atrial fibrillation as main diagnosis, per hospital?",
    [
        "AF stay = inpatient stay whose main diagnosis (primary_icd10) starts with I48.",
        "Report per hospital: patients with at least one AF stay.",
    ],
    stat(["hospital"], Count(stay_pids(type="inpatient", primary="I48"))),
)
add(
    "How many inpatient stays started in 2025, by hospital and sex?",
    [
        "Inpatient stay = encounter with type 'inpatient'.",
        "Count stays (not patients) whose start_date is in 2025.",
        "Report per hospital and sex: inpatient stays.",
    ],
    stat(["hospital", "sex"], Stays(stays("inpatient", 2025))),
)
add(
    "How many inpatient stays in total had a main diagnosis of urinary tract infection?",
    [
        "UTI stay = inpatient stay whose main diagnosis (primary_icd10) starts with N39.",
        "Count stays (not patients).",
        "Report: UTI stays.",
    ],
    stat([], Stays(stays("inpatient", primary="N39"))),
)
add(
    "How many inpatient stays for appendicitis started in 2024, per region?",
    [
        "Appendicitis stay = inpatient stay whose main diagnosis (primary_icd10) starts with K35.",
        "Count stays (not patients) whose start_date is in 2024.",
        "Report per region: appendicitis stays.",
    ],
    stat(["region"], Stays(stays("inpatient", 2024, "K35"))),
)

# --- 9. diagnosis date windows
add(
    "How many patients received a new depression diagnosis in 2025, by region?",
    [
        "New depression = any condition with an ICD-10 code starting with F32 and an onset_date in 2025.",
        "Report per region: patients with new depression.",
    ],
    stat(["region"], Count(dx("F32", year=2025))),
)
add(
    "How many patients were newly diagnosed with type 2 diabetes in 2024, by sex?",
    [
        "New type 2 diabetes = any condition with an ICD-10 code starting with E11 and an onset_date in 2024.",
        "Report per sex: patients with new type 2 diabetes.",
    ],
    stat(["sex"], Count(dx("E11", year=2024))),
)

# --- 10. age and sex filters
add(
    "How many children have asthma in each region?",
    [
        "Child = aged under 18 on 31 December 2025.",
        "Asthma = any condition with an ICD-10 code starting with J45.",
        "Report per region: children with asthma.",
    ],
    stat(["region"], Count(age_lt(18) & asthma)),
)
add(
    "How many women with breast cancer are there per age band?",
    [
        "Breast cancer = any condition with an ICD-10 code starting with C50.",
        "Women = sex 'female'.",
        "Age bands on 31 December 2025: 18-49, 50-69, 70+.",
        "Report per age band: women with breast cancer.",
    ],
    stat([["18-49", "50-69", "70+"]], Count(bc_w)),
)
bb = med("C07AB")
add(
    "What share of older patients with heart failure has an active beta blocker, per hospital?",
    [
        "Older patient = aged 65 or older on 31 December 2025.",
        "Heart failure = any condition with an ICD-10 code starting with I50.",
        "Beta blocker = ATC code starting with C07AB.",
        ACTIVE,
        "Report per hospital: older patients with heart failure and % with an active beta blocker, one decimal.",
    ],
    stat(["hospital"], Count(age_ge(65) & hf), Pct(bb, age_ge(65) & hf)),
)
add(
    "How many patients living in Wien have depression, by sex?",
    [
        "Viennese patient = patient whose region is 'Wien'.",
        "Depression = any condition with an ICD-10 code starting with F32.",
        "Report per sex: Viennese patients with depression.",
    ],
    stat(["sex"], Count(attr("region", "Wien") & dep)),
)

# --- 11. two dimensions
add(
    "How many patients have depression, by region and sex?",
    ["Depression = any condition with an ICD-10 code starting with F32.", "Report per region and sex: patients with depression."],
    stat(["region", "sex"], Count(dep)),
)
add(
    "How many adults have hypertension, by hospital and age band?",
    [
        ADULT,
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "Age bands on 31 December 2025: 18-49, 50-64, 65+.",
        "Report per hospital and age band: adults with hypertension.",
    ],
    stat(["hospital", ["18-49", "50-64", "65+"]], Count(htn_ad)),
)
add(
    "What is the prevalence of obesity among adults, by region and age band?",
    [
        ADULT,
        "Obesity = any condition with an ICD-10 code starting with E66.",
        "Age bands on 31 December 2025: 18-44, 45-64, 65+.",
        "Prevalence = adults with obesity / all adults in the group × 100, one decimal.",
        "Report per region and age band: adults, adults with obesity and the prevalence.",
    ],
    stat(["region", ["18-44", "45-64", "65+"]], Count(adults), Count(adults & obes), Pct(obes, adults)),
)
add(
    "What is the mean systolic blood pressure of adults with hypertension, by sex and age band?",
    [
        ADULT,
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "Age bands on 31 December 2025: 18-49, 50-64, 65+.",
        "Use the latest systolic blood pressure (LOINC 8480-6) per patient; patients without a value are left out.",
        "Report per sex and age band: adults with hypertension who have a systolic value and their mean systolic blood pressure, one decimal.",
    ],
    stat(["sex", ["18-49", "50-64", "65+"]], Count(htn_ad & has_obs(SBP)), Mean(SBP, htn_ad)),
)
add(
    "What is the mean BMI of adults with type 2 diabetes, by region and sex?",
    [
        ADULT,
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Use the latest BMI (LOINC 39156-5) per patient; patients without a BMI are left out.",
        "Report per region and sex: adults with type 2 diabetes who have a BMI and their mean BMI, one decimal.",
    ],
    stat(["region", "sex"], Count(adults & t2d & has_obs(BMI)), Mean(BMI, adults & t2d)),
)

# --- 12. paraphrased wording (same semantics, different phrasing)
add(
    "In which federal states is type 2 diabetes most common among people aged 65 or older?",
    [
        "Older person = aged 65 or older on 31 December 2025.",
        "Type 2 diabetes is identified by any diagnosis whose ICD-10 code begins with E11.",
        "Prevalence = older persons with type 2 diabetes / all older persons in the region × 100, one decimal.",
        "Report per region: older persons, older persons with type 2 diabetes and the prevalence.",
    ],
    stat(["region"], Count(age_ge(65)), Count(age_ge(65) & t2d), Pct(t2d, age_ge(65))),
)
add(
    "Average latest body mass index of patients with depression, by sex.",
    [
        "Depression = any condition with an ICD-10 code starting with F32.",
        "BMI is the most recent value of LOINC 39156-5 for each patient; patients without a BMI are left out.",
        "Report per sex: patients with depression who have a BMI and their average BMI, one decimal.",
    ],
    stat(["sex"], Count(dep & has_obs(BMI)), Mean(BMI, dep)),
)
add(
    "Count every outpatient encounter that started during 2025, by age group.",
    [
        "Outpatient encounter = encounter with type 'outpatient'.",
        "Count encounters (not patients) whose start_date falls in 2025.",
        "Age bands on 31 December 2025: 0-17, 18-64, 65+.",
        "Report per age band: outpatient encounters.",
    ],
    stat([["0-17", "18-64", "65+"]], Stays(stays("outpatient", 2025))),
)
add(
    "Which share of men aged 50 to 69 with hypertension is treated with an ARB, per region?",
    [
        "Men = sex 'male'.",
        "Age 50 to 69 = aged 50 to 69 on 31 December 2025.",
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "ARB = ATC code starting with C09CA.",
        ACTIVE,
        "Report per region: men aged 50 to 69 with hypertension and the share with an active ARB in %, one decimal.",
    ],
    stat(["region"], Count(male() & age_between(50, 69) & htn), Pct(med("C09CA"), male() & age_between(50, 69) & htn)),
)
add(
    "Total number of patients with diabetes or obesity in the dataset.",
    [
        "Metabolic disease = a diagnosis with an ICD-10 code starting with E11 or E66.",
        "Report: patients with metabolic disease.",
    ],
    stat([], Count(dx("E11", "E66"))),
)
add(
    "For people aged 75 and above with chronic kidney disease, what is the average kidney function per hospital?",
    [
        "Very old patient = aged 75 or older on 31 December 2025.",
        "CKD: ICD-10 codes beginning with N18.",
        "Kidney function = the latest eGFR (LOINC 62238-1) of each patient; patients without one are left out.",
        "Report per hospital: very old patients with CKD who have an eGFR and their mean eGFR, one decimal.",
    ],
    stat(["hospital"], Count(age_ge(75) & ckd & has_obs(EGFR)), Mean(EGFR, age_ge(75) & ckd)),
)
add(
    "How many people with depression were prescribed an SSRI at any time, per region?",
    [
        "Depression = any condition with an ICD-10 code starting with F32.",
        "SSRI ever = any prescription with an ATC code starting with N06AB, regardless of start or end date.",
        "Report per region: patients with depression and how many of them have SSRI ever.",
    ],
    stat(["region"], Count(dep), Count(dep & med("N06AB", active=False))),
)
add(
    "What fraction of adults with asthma currently uses a fixed-dose inhaler combination, by sex?",
    [
        ADULT,
        "Asthma = any condition with an ICD-10 code starting with J45.",
        "ICS/LABA inhaler = ATC code starting with R03AK.",
        ACTIVE,
        "Report per sex: adults with asthma and the percentage with an active ICS/LABA inhaler, one decimal.",
    ],
    stat(["sex"], Count(adults & asthma), Pct(med("R03AK"), adults & asthma)),
)
add(
    "Emergency department visits by men in 2025, per hospital.",
    [
        "Men = sex 'male'.",
        "Emergency visit = encounter with type 'emergency'.",
        "Count visits (not patients) of men whose start_date is in 2025.",
        "Report per hospital: emergency visits of men.",
    ],
    stat(["hospital"], Stays(only(stays("emergency", 2025), male()))),
)
add(
    "How many patients had an inpatient stay whose main diagnosis was diabetes in 2023 or later, by region?",
    [
        "Diabetes stay = inpatient stay whose main diagnosis (primary_icd10) starts with E11.",
        "Report per region: patients with at least one diabetes stay.",
    ],
    stat(["region"], Count(stay_pids(type="inpatient", primary="E11"))),
)

# --- 13. downgraded access applications (answered as statistics from the "Report per" line)
add(
    "Provide pseudonymised records of all adults with asthma and their inhaler prescriptions.",
    [
        ADULT,
        "Asthma = any condition with an ICD-10 code starting with J45.",
        "ICS/LABA = ATC code starting with R03AK.",
        ACTIVE,
        "Report per region: adults with asthma, % with an active ICS/LABA, one decimal.",
    ],
    stat(["region"], Count(adults & asthma), Pct(med("R03AK"), adults & asthma)),
    mode="statistics",
    requested_as="health data access application",
)
add(
    "Provide pseudonymised records of patients with COPD and their medications.",
    [
        "COPD = any condition with an ICD-10 code starting with J44.",
        "Tiotropium = ATC code starting with R03BB.",
        ACTIVE,
        "Age bands on 31 December 2025: 0-59, 60-74, 75+.",
        "Report per band: patients with COPD, % with active tiotropium, one decimal.",
    ],
    stat([["0-59", "60-74", "75+"]], Count(copd), Pct(med("R03BB"), copd)),
    mode="statistics",
    requested_as="health data access application",
)
add(
    "Provide pseudonymised HbA1c values of patients with type 2 diabetes.",
    [
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Use the latest HbA1c (LOINC 4548-4) per patient; patients without a HbA1c are left out.",
        "Report per hospital: patients with type 2 diabetes who have a HbA1c value and their mean HbA1c, one decimal.",
    ],
    stat(["hospital"], Count(t2d & has_obs(HBA)), Mean(HBA, t2d)),
    mode="statistics",
    requested_as="health data access application",
)
add(
    "Provide pseudonymised encounter records of patients with heart failure.",
    [
        "Heart failure stay = inpatient stay whose main diagnosis (primary_icd10) starts with I50.",
        "Count stays (not patients) whose start_date is in 2025.",
        "Report per sex: heart failure stays.",
    ],
    stat(["sex"], Stays(stays("inpatient", 2025, "I50"))),
    mode="statistics",
    requested_as="health data access application",
)

# --- 14. SPE cohorts
add(
    "Provide pseudonymised diagnoses and medications for adults with heart failure. Only model performance metrics will be exported.",
    [
        ADULT,
        "Heart failure = any condition with an ICD-10 code starting with I50.",
        "Cohort = adults with heart failure.",
        "Report the size of the cohort you would make available.",
    ],
    spe(adults & hf, "conditions", "medications"),
    mode="spe",
)
add(
    "Provide pseudonymised encounters for patients with COPD aged 40 or older.",
    [
        "COPD = any condition with an ICD-10 code starting with J44.",
        "Cohort = patients with COPD aged 40 or older on 31 December 2025.",
        "Report the size of the cohort you would make available.",
    ],
    spe(copd & age_ge(40), "encounters"),
    mode="spe",
)
add(
    "Provide pseudonymised conditions, medications, observations and encounters for patients with chronic kidney disease who have at least one eGFR value.",
    [
        "Chronic kidney disease = any condition with an ICD-10 code starting with N18.",
        "Cohort = patients with chronic kidney disease who have at least one eGFR value (LOINC 62238-1).",
        "Report the size of the cohort you would make available.",
    ],
    spe(ckd & has_obs(EGFR), "conditions", "medications", "observations", "encounters"),
    mode="spe",
)
add(
    "Provide pseudonymised conditions and medications of women with breast cancer.",
    [
        "Breast cancer = any condition with an ICD-10 code starting with C50.",
        "Women = sex 'female'.",
        "Cohort = women with breast cancer.",
        "Report the size of the cohort you would make available.",
    ],
    spe(bc_w, "conditions", "medications"),
    mode="spe",
)
add(
    "Provide pseudonymised encounters and medications of patients who had an inpatient stay with heart failure as main diagnosis in 2025.",
    [
        "Heart failure stay = inpatient stay whose main diagnosis (primary_icd10) starts with I50 and whose start_date is in 2025.",
        "Cohort = patients with at least one heart failure stay.",
        "Report the size of the cohort you would make available.",
    ],
    spe(stay_pids(type="inpatient", year=2025, primary="I50"), "encounters", "medications"),
    mode="spe",
)
add(
    "Provide pseudonymised medications and observations of adults with type 2 diabetes who have an active SGLT2 inhibitor.",
    [
        ADULT,
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "SGLT2 inhibitor = ATC code starting with A10BK.",
        ACTIVE,
        "Cohort = adults with type 2 diabetes and an active SGLT2 inhibitor.",
        "Report the size of the cohort you would make available.",
    ],
    spe(adults & t2d & sglt2, "medications", "observations"),
    mode="spe",
)
add(
    "Provide pseudonymised conditions and medications of children with asthma.",
    [
        "Child = aged under 18 on 31 December 2025.",
        "Asthma = any condition with an ICD-10 code starting with J45.",
        "Cohort = children with asthma.",
        "Report the size of the cohort you would make available.",
    ],
    spe(age_lt(18) & asthma, "conditions", "medications"),
    mode="spe",
)
add(
    "Provide pseudonymised conditions and medications of patients aged 65 or older with atrial fibrillation who have no active oral anticoagulant.",
    [
        "Atrial fibrillation = any condition with an ICD-10 code starting with I48.",
        "Oral anticoagulant = ATC code starting with B01AA or B01AF.",
        ACTIVE,
        "Cohort = patients aged 65 or older on 31 December 2025 with atrial fibrillation and no active oral anticoagulant.",
        "Report the size of the cohort you would make available.",
    ],
    spe(age_ge(65) & af - oac, "conditions", "medications"),
    mode="spe",
)
add(
    "Provide pseudonymised observations and medications of adults with obesity whose latest BMI is 35 or higher.",
    [
        ADULT,
        "Obesity = any condition with an ICD-10 code starting with E66.",
        "Use the latest BMI (LOINC 39156-5) per patient.",
        "Cohort = adults with obesity whose latest BMI is 35 or higher.",
        "Report the size of the cohort you would make available.",
    ],
    spe(adults & obes & lat_ge(BMI, 35), "observations", "medications"),
    mode="spe",
)
add(
    "Provide pseudonymised conditions, medications and encounters of patients newly diagnosed with depression in 2025.",
    [
        "New depression = any condition with an ICD-10 code starting with F32 and an onset_date in 2025.",
        "Cohort = patients with new depression.",
        "Report the size of the cohort you would make available.",
    ],
    spe(dx("F32", year=2025), "conditions", "medications", "encounters"),
    mode="spe",
)
add(
    "Provide pseudonymised conditions and encounters of men aged 50 to 69 with hypertension and type 2 diabetes.",
    [
        "Men = sex 'male'.",
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Cohort = men aged 50 to 69 on 31 December 2025 with hypertension and type 2 diabetes.",
        "Report the size of the cohort you would make available.",
    ],
    spe(male() & age_between(50, 69) & htn & t2d, "conditions", "encounters"),
    mode="spe",
)

# --- 15. errors (engine must raise InterpretError)
add(
    "How many patients have depression in each district?",
    ["Depression = any condition with an ICD-10 code starting with F32.", "Report per district: patients with depression."],
    ERROR,
)
add(
    "What is the mean hypertension per region?",
    [
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "Report per region: patients with hypertension and their mean hypertension, one decimal.",
    ],
    ERROR,
)
add(
    "What share of patients with asthma uses a statin, per region?",
    [
        "Asthma = any condition with an ICD-10 code starting with J45.",
        ACTIVE,
        "Report per region: patients with asthma and % with an active statin, one decimal.",
    ],
    ERROR,
)
add(
    "How many patients have gout, by region?",
    ["Gout = any condition with an ICD-10 code starting with M10.", "Report per region: patients with gout."],
    ERROR,
)
add(
    "What is the mean cholesterol of patients with type 2 diabetes, by region?",
    [
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Use the latest total cholesterol per patient.",
        "Report per region: patients with type 2 diabetes who have a cholesterol value and their mean cholesterol, one decimal.",
    ],
    ERROR,
)
add(
    "How many patients have depression, by region?",
    [
        "Depression = any condition with an ICD-10 code starting with F32.",
        "Please also take the patients' lifestyle into account as you see fit.",
        "Report per region: patients with depression.",
    ],
    ERROR,
)
add(
    "How many patients with chronic pain take paracetamol, by hospital?",
    [
        "Paracetamol = ATC code starting with N02BE.",
        ACTIVE,
        "Report per hospital: patients with an active paracetamol.",
    ],
    ERROR,
)
add(
    "How many adults with type 2 diabetes are there per insurance company?",
    [
        ADULT,
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Report per insurance company: adults with type 2 diabetes.",
    ],
    ERROR,
)
add(
    "What is the mean ACE inhibitor use of patients with hypertension, by sex?",
    [
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "ACE inhibitor = ATC code starting with C09AA.",
        ACTIVE,
        "Report per sex: patients with hypertension and their mean ACE inhibitor, one decimal.",
    ],
    ERROR,
)
add(
    "What is the mean LDL cholesterol of adults with hypertension, by region?",
    [
        ADULT,
        "Hypertension = any condition with an ICD-10 code starting with I10.",
        "Use the latest LDL cholesterol (LOINC 13457-7) per patient; patients without a value are left out.",
        "Report per region: adults with hypertension who have an LDL value and their mean LDL cholesterol, one decimal.",
    ],
    ERROR,
)
add(
    "How many adults with obesity are there per region?",
    [
        ADULT,
        "Obesity = any condition with an ICD-10 code starting with E66.",
        "Report per region: adults with obesity and patients with sleep apnoea.",
    ],
    ERROR,
)


# ================================================================ validate + write
def check_samples():
    exp = json.loads((REQ / "expected.json").read_text(encoding="utf-8"))
    ok = True
    for i, rid in enumerate(["R01", "R03", "R05", "R07", "R08", "R09"]):
        cid, _, case = CASES[i]
        ans = exp[rid]["answer"]
        got = case["expected"]
        if rid == "R05":
            match = got["cohort_size"] == ans["cohort_size"]
        else:
            conv = {k: (list(v.values()) if isinstance(v, dict) else [v]) for k, v in ans.items()}
            match = conv == got
        print(f"{cid} ({rid}) reproduces expected.json: {'yes' if match else 'NO'}")
        ok &= match
    return ok


def main():
    assert len(CASES) == 100, len(CASES)
    ok = check_samples()
    if WARNINGS:
        print("rounding warnings:")
        for w in WARNINGS:
            print("  ", w)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("B*.json"):
        old.unlink()
    for cid, req, case in CASES:
        data = {"request": req, "mode": case["mode"], "expected": case["expected"]}
        (OUT / f"{cid}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(CASES)} cases to {OUT}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
