"""Turn a request's definitions into a QuerySpec with ordered regex rules.

Every definition sentence must fully match one rule; a sentence no rule
understands raises InterpretError instead of being skipped. The metric list
after "Report per ...:" and the "Cohort = ..." expression are free English:
they are read by finding the defined concept names in them and taking the
operation (count / percentage / mean, negation) from keywords. Any word left
over that is not a known filler word is an error too.

Columns are never guessed from words: each concept kind has a fixed binding
to the data (see query.py).
"""

import re
import sqlite3
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.models import (
    Band,
    CodeMapping,
    Concept,
    DataRequest,
    Filter,
    Metric,
    QuerySpec,
    ResultSpec,
    Route,
    Term,
)


class InterpretError(ValueError):
    """A sentence of the request could not be turned into the QuerySpec."""


class NoDataError(InterpretError):
    """Understood, but a code matches nothing in the data: no rewording fixes that."""


class _Decline(Exception):
    """A rule matched the text but not the meaning; try the next rule."""


# Measurements in the data (data/README.md). Requests may also define their own name for a LOINC.
KNOWN_MEASUREMENTS = {
    "hba1c": "4548-4",
    "bmi": "39156-5",
    "body mass index": "39156-5",
    "systolic blood pressure": "8480-6",
    "systolic bp": "8480-6",
    "blood pressure": "8480-6",
    "egfr": "62238-1",
    "kidney function": "62238-1",
}
# Concepts every request may use without defining them; definitions override them.
BUILTIN = [
    Concept(name="adult", kind="age", min_age=18),
    Concept(name="child", kind="age", max_age=17),
    Concept(name="women", kind="sex", value="female"),
    Concept(name="female", kind="sex", value="female"),
    Concept(name="men", kind="sex", value="male"),
    Concept(name="male", kind="sex", value="male"),
]
GROUP_WORDS = {
    "region": "region", "regions": "region", "federal state": "region", "state": "region",
    "band": "band", "bands": "band", "age band": "band", "age bands": "band", "age group": "band",
    "age groups": "band", "10-year age band": "band",
    "hospital": "hospital", "hospitals": "hospital", "data holder": "hospital", "data holders": "hospital",
    "data_holder": "hospital", "clinic": "hospital", "klinikum": "hospital", "residence": "region",
    "sex": "sex", "gender": "sex",
}
TABLE_WORDS = {
    "conditions": r"conditions?|diagnos[ie]s",
    "medications": r"medications?|prescriptions?|drugs?|medicines?",
    "observations": r"observations?|measurements?|lab values?|laboratory|values|hba1c|bmi|blood pressure|egfr",
    "encounters": r"encounters?|visits?|stays?|admissions?",
    "patients": r"demographics?|patient data|birth dates?",
}
# Words a metric phrase may contain besides concept names.
FILLER = set("""
a an the of and or with who whom whose that which have has having had is are was were be been in on at for
to by per from patients patient people persons person individuals them those these they their thereof its
how many number count counts total all any at least one more value values recorded measured measurement
measurements reading readings result results available known documented diagnosed diagnosis diagnoses
condition conditions currently current active ongoing prescription prescriptions prescribed taking take
treated on using use drug drugs medication medications region band group hospital sex age each
mean average avg share percent percentage proportion prevalence rate fraction among out amongst
also as well both overall still plus there not no without never one decimal decimals place
stays stay encounters encounter admissions visits visit cases case rows record records
latest most recent newest last ever were been received receiving given whom which every neither nor
did do does got still separately distinct unique
""".split()) | {"%"}
NEG = re.compile(r"\b(?:no|not|without|never|non|neither|nor)\b")
PRONOUN = re.compile(r"\b(?:of them|among them|of these|of those|of whom|of which|those|them|they|their|thereof|these|whom)\b")
MONTHS = "january february march april may june july august september october november december".split()

I = re.IGNORECASE
DATE = r"(?:\d{1,2} \w+ \d{4}|\d{4}-\d{2}-\d{2})"
ON_DATE = rf"(?: (?:on|at|as of) (?P<date>{DATE}))?"
ICD = r"[A-Z]\d{2}(?:\.\d+)?\*?"
ATC = r"[A-Z]\d{2}(?:[A-Z]{1,2}(?:\d{1,2})?)?\*?"
SEP = r"(?:\s*,\s*(?:or\s+)?|\s+or\s+|\s*/\s*)"
NAME = r"(?P<name>[^=:]+?)"
START = r"(?:starting|beginning|that starts?|which starts?|starts?) with"
ICD_RANGE = r"[A-Z]\d{2}\s*(?:-|–|to)\s*[A-Z]?\d{2}"
ICD_LIST = rf"(?P<codes>(?:{ICD_RANGE}|{ICD})(?:{SEP}(?:{ICD_RANGE}|{ICD}))*)"
YEAR_ONSET = (r"(?:,? (?:and |with |whose )?(?:an? )?(?:onset(?:_date| date)?(?: is)?|(?:first )?diagnosed|onset year)"
              r" (?:in|during) (?P<year>\d{4}))?")
YEAR_START = (r"(?:,? (?:and )?(?:that |which |whose )?(?:starting|started|start(?:_date| date)?(?: is)?|beginning|begun|took place)?"
              r" ?(?:in|during) (?P<year>\d{4}))?")
ENC_TYPE = r"'?(?P<type>inpatient|outpatient|emergency)'?"
MAIN_DX = (rf"(?:(?:a |the )?(?:main|primary) diagnosis(?: \(primary_icd10\))?(?: code)?|primary_icd10|primary ICD-10(?: code)?)"
           rf" (?:{START}|is) (?P<codes>{ICD}(?:{SEP}{ICD})*)")
OP_WORDS = {
    ">=": ">=", "≥": ">=", "at least": ">=", "of at least": ">=", "at or above": ">=", "greater than or equal to": ">=",
    "equal to or greater than": ">=", "equal to or above": ">=", "=>": ">=",
    "at or below": "<=", "less than or equal to": "<=", "equal to or less than": "<=", "equal to or below": "<=", "=<": "<=",
    ">": ">", "above": ">", "over": ">", "greater than": ">", "more than": ">", "higher than": ">",
    "<=": "<=", "≤": "<=", "at most": "<=",
    "<": "<", "below": "<", "under": "<", "less than": "<", "lower than": "<",
}
OP_RE = "|".join(re.escape(k) for k in sorted(OP_WORDS, key=len, reverse=True))
UNIT = r"(?: ?(?:%|percent|kg/m2|kg/m²|mmhg|mm hg|mm\[hg\]|ml/min\S*))"
OR_SUFFIX = {"more": ">=", "higher": ">=", "above": ">=", "over": ">=", "less": "<=", "lower": "<=", "below": "<="}


GROUP_RE = r"(?:region|regions|age bands?|bands?|age groups?|hospitals?|data holders?|sex|gender)(?: and (?:region|age bands?|bands?|age groups?|hospitals?|data holders?|sex|gender))?"


def R(p: str) -> re.Pattern:
    return re.compile(p, I)


RULES: list[tuple[str, re.Pattern]] = [
    ("age_min", R(rf"{NAME} = (?:patients |people )?(?:aged? )?(?P<min>\d+)(?: years)? (?:or|and) (?:older|over|above)(?: years)?{ON_DATE}")),
    ("age_min", R(rf"{NAME} = (?:patients |people )?aged? (?P<min>\d+)\+{ON_DATE}")),
    ("age_min", R(rf"{NAME} = age (?:>=|≥) ?(?P<min>\d+){ON_DATE}")),
    ("age_max", R(rf"{NAME} = (?:patients |people )?(?:aged? )?(?:under|below|younger than|less than) (?P<under>\d+)(?: years)?(?: old)?{ON_DATE}")),
    ("age_max", R(rf"{NAME} = age < ?(?P<under>\d+){ON_DATE}")),
    ("age_range", R(rf"{NAME} = (?:patients |people )?aged? (?:between )?(?P<min>\d+) ?(?:-|–|to|and) ?(?P<max>\d+)(?: years)?{ON_DATE}")),
    ("condition", R(rf"{NAME} = (?:any |a |an )?(?:conditions?|diagnos[ie]s)(?: rows?)?(?: in conditions)? (?:with |coded with |having |whose )?(?:an? |the )?ICD-10(?: codes?)?(?: is)? (?:{START} |in )?{ICD_LIST}{YEAR_ONSET}")),
    ("condition", R(rf"{NAME} = (?:any |an? )?(?:conditions?|diagnos[ie]s) (?:coded|with (?:the )?codes?|coded as) {ICD_LIST}{YEAR_ONSET}")),
    ("condition", R(rf"{NAME} = (?:any |an? )?ICD-10(?: codes?)? (?:{START} )?{ICD_LIST}(?: \(any sub-?codes?\))?{YEAR_ONSET}")),
    ("main_dx", R(rf"{NAME} = (?:an? |any )?(?:(?P<type>inpatient|outpatient|emergency) )?(?:hospital )?(?:stays?|encounters?|admissions?|visits?) (?:with|whose|where) {MAIN_DX}{YEAR_START}")),
    ("encounter", R(rf"{NAME} = (?:an? |any )?encounters? (?:with|of|whose) type(?: is| =)? {ENC_TYPE}(?:,? (?:and|with)(?: a| an)? {MAIN_DX})?{YEAR_START}")),
    ("encounter", R(rf"{NAME} = (?:an? |any )?{ENC_TYPE} (?:encounters?|stays?|visits?|admissions?)(?:,? (?:with|and|whose) {MAIN_DX})?{YEAR_START}")),
    ("drug", R(rf"{NAME} = (?:any |an? )?(?:(?:medications?|prescriptions?|drugs?) (?:with |whose )?)?(?:an? |the )?ATC(?: codes?)?(?: is)? (?:{START} )?(?P<codes>{ATC}(?:{SEP}{ATC})*)")),
    ("sex", R(rf"{NAME} = (?:patients )?(?:of |with )?(?:sex|gender) (?:is |= )?'?(?P<sex>female|male)'?")),
    ("sex", R(rf"{NAME} = (?P<sex>female|male)(?: patients| sex)?")),
    ("region", R(rf"{NAME} = (?:patients )?(?:(?:living|resident|who live) )?in (?:the )?(?:region )?'?(?P<region>[^']+?)'?")),
    ("region", R(rf"{NAME} = (?:patients with )?region '?(?P<region>[^']+?)'?")),
    ("hospital", R(rf"{NAME} = (?:patients )?(?:held by|treated at|at|of|from|whose data holder is) (?:the )?'?(?P<hospital>Klinikum [^']+?)'?")),
    ("hospital", R(rf"{NAME} = (?:patients with )?data[ _]holder '?(?P<hospital>[^']+?)'?")),
    ("bands", R(rf"(?:Use (?:the )?(?:following )?)?(?:10-year |five-year |\d+-year )?age (?:bands|groups)(?: \(?(?:on |at |as of )?{DATE}\)?)?(?: are)?: (?P<bands>.+)")),
    ("latest", R(rf"Use (?:the )?(?:latest|most recent|newest|last) (?P<name>[^(]+?) \(LOINC (?P<loinc>\d+-\d)\)(?: value)? (?:per|for each|of each|for every) patient(?:[;,] .*\b(?:left out|excluded|ignored|not counted))?")),
    ("latest", R(rf"Use (?:each|every|the) patient's (?:latest|most recent|newest|last) (?P<name>[^(]+?)(?: \(LOINC (?P<loinc>\d+-\d)\))?(?: value| measurement)?(?:[;,] .*\b(?:left out|excluded|ignored|not counted))?")),
    ("latest", R(rf"Use (?:the )?(?:latest|most recent|newest|last) (?P<name>[^(]+?)(?: value| measurement)? (?:per|for each|of each|for every) patient(?:[;,] .*\b(?:left out|excluded|ignored|not counted))?")),
    ("measure", R(rf"{NAME} = (?:an? |any |the )?(?:(?:latest|most recent) )?(?:observations?|measurements?|lab values?)?(?: with| whose)? ?(?:the )?LOINC(?: code)?(?: is)? (?P<loinc>\d+-\d)(?:,? (?:latest|most recent) value(?: per patient)?)?")),
    ("threshold", R(rf"{NAME} = (?:an? |the )?(?:(?:latest|most recent|last) )?(?P<meas>[^=(]+?)(?: \(LOINC (?P<loinc>\d+-\d)\))?(?: value)?(?: (?:is|was|of))? (?P<op>{OP_RE})? ?(?P<val>\d+(?:\.\d+)?){UNIT}?(?: (?P<orw>or (?:more|higher|above|over|less|lower|below)))?{UNIT}?")),
    ("active", R(rf"(?:a )?(?:medication|prescription|drug) is (?:considered )?active (?:on|at) (?P<date>{DATE}) if .+")),
    ("active", R(r"(?:a )?(?:medication|prescription|drug) (?:counts as|is considered|is) active .+")),
    ("active", R(r"(?:an? )?active(?: medications?| prescriptions?| drugs?)?(?: on \S+ \S+ \d{4})? (?:=|means|is) .*\b(?:start|end)_?(?:date)?\b.*")),
    ("active", R(r"Only (?:count )?(?:active|current) (?:medications|prescriptions|drugs)\b.*")),
    ("unit", R(r"Count (?:each |every |all )?(?:(?:inpatient|outpatient|emergency) )?(?:stays?|encounters?|admissions?|visits?)(?: separately)?(?: \(not patients\)|,? (?:and )?not (?:the )?patients,?|,? not unique patients,?)?(?: (?:whose start_date is|whose start date is|that started|starting|started|beginning|with start_date|with a start_date) in (?P<year>\d{4}))?")),
    ("unit_patients", R(r"Count (?:unique |distinct |each )?patients(?: \(not (?:stays|encounters|visits)\)|,? not (?:stays|encounters|visits))?(?:,? (?:each|every) patient once| once)?")),
    ("cohort", R(r"(?:The )?(?:Study )?(?:Cohort|population|study population)(?: = | (?:is|consists of|includes|comprises) )(?P<expr>.+)")),
    ("ratio", R(rf"{NAME} = (?P<num>[^/]+?) / (?P<den>[^/]+?)(?: ?(?:×|x|\*) ?100)?(?:,? (?:with |to |rounded to )?one decimal(?: place)?)?")),
    ("ratio", R(rf"{NAME} = (?P<num>.+?) divided by (?P<den>.+?)(?:,? (?:times|multiplied by|×|x|\*) ?100)?(?:,? (?:in %|as a percentage))?(?:,? (?:with |to |rounded to )?one decimal(?: place)?)?")),
    ("ratio", R(rf"{NAME} = (?P<num>.+?) (?:as a (?:percentage|share|proportion) of|out of|among) (?P<den>.+?)(?:,? (?:in %|× ?100|x ?100|times 100))?(?:,? (?:with |to |rounded to )?one decimal(?: place)?)?")),
    ("ratio_of", R(rf"{NAME} = (?:the )?(?:share|percentage|proportion|%) of (?P<den>.+?) (?P<num>(?:who|with|that|having|without) .+?)(?:,? (?:with |to )?one decimal(?: place)?)?")),
    ("report_size", R(r"(?:Report|Give|State|Provide) (?:the )?(?:size of the cohort|cohort size|size of the study population|(?:exact )?number of (?:patients|people) (?:in the cohort|(?:that|who) would be (?:included|made available|provided|copied))|how many patients (?:would be|are) (?:included|in the cohort|made available))\b.*")),
    ("report", R(rf"Report,?(?: (?:(?:per|by|for each|for every|broken down by|split by)(?: the)?(?: patient'?s)? (?P<group>[^:]+?)|(?:for all patients|overall|in total|for the whole population|a single total|one total|totals?)))?,?: (?P<metrics>.+)")),
    ("report", R(rf"Report (?:(?:per|by|for each)(?: the)?(?: patient'?s)? (?P<group>{GROUP_RE}),? )?(?P<metrics>(?:the |a )?(?:number|count|mean|average|share|percentage|proportion|prevalence|total) .+?)(?: (?:per|by|for each)(?: the)?(?: patient'?s)? (?P<group2>{GROUP_RE}))?")),
    ("noise", R(r"(?:Round|Show|Give|Report|Present) (?:all )?(?:the )?(?:percentages|means|values|results|numbers|averages|shares).*?(?:one|1) decimal(?: places?)?")),
    ("noise", R(r"(?:Percentages|Means|Values|Shares|Averages|All percentages)(?: and \w+)? (?:with|to|rounded to|in) (?:one|1) decimal(?: places?)?")),
    ("noise", R(r"(?:Patients|People) (?:who|that) (?:have )?opted[ -]out .*(?:excluded|left out|removed)")),
    ("noise", R(r"(?:Patients|People|Women|Men|Adults|Children) without an? [\w ]+ (?:value |measurement )?(?:are|is) (?:left out|excluded|not counted|ignored)")),
    ("noise", R(r"(?:Exclude|Leave out|Remove) (?:all )?(?:patients|people) who (?:have )?opted[ -]out\b.*|Opted[ -]out patients are (?:excluded|left out|removed)\b.*")),
    ("noise", R(r"Age(?: on \S+ \S+ \d{4})? = (?:\d{4} minus (?:the )?(?:patient's )?(?:birth year|year of birth)|reference year minus birth year)\b.*")),
    ("noise", R(rf"(?:Use )?{DATE} (?:is|as) the reference date\b.*|Reference date(?: is|:) {DATE}\b.*|Use {DATE} as (?:the )?reference date\b.*")),
    ("noise", R(r"(?:One|1) decimal(?: place)? for (?:all )?(?:percentages|means|shares|averages)(?: and (?:percentages|means|shares|averages))?")),
    ("noise", R(r"(?:Small cells|Counts below 10|Cells (?:with|below) (?:fewer than )?10\b.*?)(?: are| is)? (?:suppressed|shown as <10|reported as <10|masked)\b.*")),
    ("noise", R(r"Counts below 10 are shown as <10\b.*|(?:Apply|Use) the small-cell rule\b.*")),
]


@dataclass
class _Ctx:
    """State while parsing one request."""

    mode: Route
    regions: list[str]
    hospitals: list[str]
    concepts: dict[str, Concept] = field(default_factory=dict)
    ratios: dict[str, tuple[str, str]] = field(default_factory=dict)  # name -> (numerator, denominator)
    active_rule: bool = False
    unit: str = "patients"
    stay_year: int | None = None
    bands: list[Band] = field(default_factory=list)
    cohort_expr: str | None = None
    report: tuple[str | None, str] | None = None  # (group text, metric text)
    report_size: bool = False
    named: set[str] = field(default_factory=set)  # defined concepts some phrase refers to
    _anon: int = 0

    def anon(self, concept: Concept) -> str:
        """Register an inline concept (e.g. 'aged 65 and over' inside a phrase)."""
        for name, c in self.concepts.items():
            if c.model_dump(exclude={"name"}) == concept.model_dump(exclude={"name"}):
                return name
        self._anon += 1
        name = f"_{concept.kind}{self._anon}"
        self.concepts[name] = concept.model_copy(update={"name": name})
        return name


_LEAD = re.compile(r"(?:Report|Age (?:bands|groups)|Use|Count|Note|Cohort|Study population|Population|Reference date)\b", I)


def normalise(sentence: str) -> str:
    """'X: ...', 'X is defined as ...', 'X are patients ...' -> 'X = ...'."""
    s = re.sub(r"\s+", " ", sentence.replace("’", "'").replace("–", "-")).strip()
    if " = " in s or _LEAD.match(s):
        return re.sub(r"^(Cohort|Study population|Population): ", r"\1 = ", s, flags=I)
    if m := re.fullmatch(r"([^:=]{1,60}?): (.+)", s):
        return f"{m[1]} = {m[2]}"
    if m := re.fullmatch(r"([^=]{1,60}?) (?:is|are) (?:defined as|defined by|given by) (.+)", s, I):
        return f"{m[1]} = {m[2]}"
    if m := re.fullmatch(r"([^=]{1,60}?) (?:means|refers to|denotes) (.+)", s, I):
        return f"{m[1]} = {m[2]}"
    starts = r"(?:patients|people|persons|individuals|any|an?|the|aged|those|sex|encounters?|ICD-10|ATC|LOINC|female|male|\d)"
    if m := re.fullmatch(rf"([^=]{{1,60}}?) (?:is|are) ({starts}\b.+)", s, I):
        return f"{m[1]} = {m[2]}"
    return s


def split_sentences(text: str) -> list[str]:
    text = text.strip()
    out = []
    for sentence in re.split(r"(?<=[a-z0-9)'%])\.\s+(?=[A-Z])", text):
        s = sentence.strip().rstrip(".").strip()
        parts = [p.strip() for p in s.split(";")]
        # "ACE inhibitor = ... C09AA; ARB = ... C09CA" holds two definitions
        out += parts if len(parts) > 1 and all(" = " in normalise(p) for p in parts) else [s]
    return [normalise(s) for s in out if s]


def _check_date(m: re.Match) -> None:
    raw = (m.groupdict().get("date") or "").strip()
    if not raw:
        return
    iso = raw
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        day, month, year = raw.split()
        if month.lower() not in MONTHS:
            raise InterpretError(f"could not read the date '{raw}'")
        iso = f"{year}-{MONTHS.index(month.lower()) + 1:02d}-{int(day):02d}"
    if iso != "2025-12-31":
        raise InterpretError(f"only the reference date 31 December 2025 is supported, got '{raw}'")


def _codes(raw: str) -> list[str]:
    out = []
    for c in re.split(SEP, raw):
        c = c.strip().rstrip("*").upper()
        if m := re.fullmatch(r"([A-Z])(\d{2})\s*(?:-|–|TO)\s*([A-Z])?(\d{2})", c):
            if m[3] and m[3] != m[1]:
                raise InterpretError(f"code range across letters: {c}")
            out += [f"{m[1]}{n:02d}" for n in range(int(m[2]), int(m[4]) + 1)]
        elif c:
            out.append(c)
    return out


def _names(raw: str) -> list[str]:
    """'Type 2 diabetes (T2D)' -> ['type 2 diabetes', 't2d']."""
    raw = raw.strip()
    if m := re.fullmatch(r"(.+?)\s*\((.+)\)", raw):
        return [m[1].strip().lower(), m[2].strip().lower()]
    return [raw.lower()]


def _define(ctx: _Ctx, raw_name: str, concept: Concept) -> None:
    names = _names(raw_name)
    for n in names:
        ctx.concepts[n] = concept.model_copy(update={"name": n})


def _parse_bands(raw: str) -> list[Band]:
    bands = []
    for part in re.split(r"\s*[,;]\s*(?:and\s+)?|\s+and\s+(?=\d+\s*(?:-|–|to|\+))|\s+and\s+(?=(?:under|below)\b)", raw.strip()):
        if m := re.fullmatch(r"(\d+)\s*(?:-|–|to)\s*(\d+)", part):
            bands.append(Band(label=part, min_age=int(m[1]), max_age=int(m[2])))
        elif m := re.fullmatch(r"(\d+)\s*(?:\+|or older|and older|and over|or over|and above|years and older|years or older)", part):
            bands.append(Band(label=part, min_age=int(m[1])))
        elif m := re.fullmatch(r"(?:under|<|below)\s*(\d+)", part):
            bands.append(Band(label=part, min_age=0, max_age=int(m[1]) - 1))
        else:
            raise InterpretError(f"could not read the age band '{part}'")
    return bands


def _apply_rule(ctx: _Ctx, rule: str, m: re.Match) -> None:
    g = m.groupdict()
    _check_date(m)
    name = g.get("name")
    if rule == "age_min":
        _define(ctx, name, Concept(name="", kind="age", min_age=int(g["min"])))
    elif rule == "age_max":
        _define(ctx, name, Concept(name="", kind="age", max_age=int(g["under"]) - 1))
    elif rule == "age_range":
        _define(ctx, name, Concept(name="", kind="age", min_age=int(g["min"]), max_age=int(g["max"])))
    elif rule == "condition":
        year = int(g["year"]) if g.get("year") else None
        _define(ctx, name, Concept(name="", kind="diagnosis", codes=_codes(g["codes"]), year=year))
    elif rule in ("main_dx", "encounter"):
        _define(ctx, name, Concept(
            name="", kind="encounter",
            encounter_type=(g.get("type") or "inpatient" if rule == "main_dx" else g["type"]).lower(),
            codes=_codes(g["codes"]) if g.get("codes") else [],
            year=int(g["year"]) if g.get("year") else None,
        ))
    elif rule == "drug":
        _define(ctx, name, Concept(name="", kind="drug", codes=_codes(g["codes"])))
    elif rule == "sex":
        _define(ctx, name, Concept(name="", kind="sex", value=g["sex"].lower()))
    elif rule == "region":
        region = _lookup(g["region"], ctx.regions, "region")
        _define(ctx, name, Concept(name="", kind="region", value=region))
    elif rule == "hospital":
        hospital = _lookup(g["hospital"], ctx.hospitals, "hospital")
        _define(ctx, name, Concept(name="", kind="hospital", value=hospital))
    elif rule == "bands":
        ctx.bands = _parse_bands(g["bands"])
    elif rule in ("latest", "measure"):
        loinc = g.get("loinc") or _measurement_loinc(ctx, g["name"])
        if loinc is None:
            raise InterpretError(f"unknown measurement '{g['name']}' (give its LOINC)")
        _define(ctx, g["name"], Concept(name="", kind="measurement", codes=[loinc]))
    elif rule == "threshold":
        op = OP_WORDS.get(re.sub(r"\s+", " ", (g.get("op") or "").lower()))
        if g.get("orw"):
            suffix = OR_SUFFIX[g["orw"].split()[-1].lower()]
            if op not in (None, suffix):
                raise InterpretError(f"contradicting comparison in '{m[0]}'")
            op = suffix
        loinc = g.get("loinc") or _measurement_loinc(ctx, g["meas"])
        if op is None or loinc is None:
            raise _Decline
        if not g.get("loinc") and g["meas"].lower() not in ctx.concepts:
            _define(ctx, g["meas"], Concept(name="", kind="measurement", codes=[loinc]))
        _define(ctx, name, Concept(name="", kind="measurement", codes=[loinc], op=op, threshold=float(g["val"])))
    elif rule == "active":
        ctx.active_rule = True
    elif rule == "unit_patients":
        ctx.unit = "patients"
    elif rule == "unit":
        ctx.unit = "stays"
        ctx.stay_year = int(g["year"]) if g.get("year") else None
    elif rule == "cohort":
        ctx.cohort_expr = g["expr"]
    elif rule == "ratio":
        ctx.ratios[name.strip().lower()] = (g["num"], g["den"])
    elif rule == "ratio_of":
        ctx.ratios[name.strip().lower()] = (f"{g['den']} {g['num']}", g["den"])
    elif rule == "report_size":
        ctx.report_size = True
    elif rule == "report":
        ctx.report = (g.get("group") or g.get("group2"), g["metrics"])


def _lookup(raw: str, values: list[str], what: str) -> str:
    for v in values:
        if v.lower() == raw.strip().lower():
            return v
    raise InterpretError(f"unknown {what} '{raw}'")


def _measurement_loinc(ctx: _Ctx, raw: str) -> str | None:
    key = raw.strip().lower()
    if (c := ctx.concepts.get(key)) and c.kind == "measurement":
        return c.codes[0]
    return KNOWN_MEASUREMENTS.get(key)


# ---------------------------------------------------------------- phrases


@dataclass
class _Match:
    start: int
    end: int
    name: str  # concept name, ratio name or "" for inline matches
    kind: str  # "concept" | "ratio"


def _variants(name: str) -> set[str]:
    words = name.split()
    last = words[-1]
    irregular = {"woman": "women", "man": "men", "child": "children", "person": "people"}
    plural = irregular.get(last) or (last + "es" if last.endswith(("s", "x", "ch")) else last + "s")
    singular = {v: k for k, v in irregular.items()}.get(last) or (last[:-1] if last.endswith("s") else last)
    return {name, " ".join(words[:-1] + [plural]), " ".join(words[:-1] + [singular])}


def _vocabulary(ctx: _Ctx) -> list[tuple[str, str, str]]:
    """(surface form, concept or ratio name, kind), longest first."""
    vocab: dict[str, tuple[str, str]] = {}
    for c in BUILTIN:
        for v in _variants(c.name):
            vocab.setdefault(v, (c.name, "concept"))
    for meas, loinc in KNOWN_MEASUREMENTS.items():
        vocab.setdefault(meas, (f"~{loinc}", "concept"))
    for name in ctx.concepts:
        if not name.startswith("_"):
            for v in _variants(name):
                vocab[v] = (name, "concept")
    for name in ctx.ratios:
        for v in _variants(name):
            vocab[v] = (name, "ratio")
    return sorted(((s, n, k) for s, (n, k) in vocab.items()), key=lambda x: -len(x[0]))


def _find(ctx: _Ctx, text: str) -> list[_Match]:
    taken = [False] * len(text)
    found = []
    for surface, name, kind in _vocabulary(ctx):
        for m in re.finditer(rf"(?<![\w-]){re.escape(surface)}(?![\w-])", text):
            if not any(taken[m.start():m.end()]):
                found.append(_Match(m.start(), m.end(), name, kind))
                taken[m.start():m.end()] = [True] * (m.end() - m.start())
    return sorted(found, key=lambda x: x.start)


def _resolve(ctx: _Ctx, name: str) -> str:
    """Builtin measurement '~4548-4' -> a concept name, defined on first use."""
    if name.startswith("~"):
        return ctx.anon(Concept(name="", kind="measurement", codes=[name[1:]]))
    if name not in ctx.concepts:
        ctx.concepts[name] = next(c for c in BUILTIN if c.name == name)
    return name


@dataclass
class _Phrase:
    terms: Filter
    ratio: str | None
    measurements: list[str]  # measurement concepts named (for "mean")
    text: str


def _inline(ctx: _Ctx, text: str) -> tuple[str, list[str], int | None]:
    """Pull out inline age limits, regions, hospitals, LOINCs and years. Returns the rest."""
    names: list[str] = []
    year = None

    def age(concept: Concept):
        def sub(_m):
            names.append(ctx.anon(concept))
            return " "
        return sub

    rules = [
        (r"\baged (\d+) ?(?:-|–|to) ?(\d+)", lambda m: Concept(name="", kind="age", min_age=int(m[1]), max_age=int(m[2]))),
        (r"\b(?:aged )?(\d+)(?: years)? (?:and|or) (?:over|older|above)\b|\baged (\d+)\+", lambda m: Concept(name="", kind="age", min_age=int(m[1] or m[2]))),
        (r"\b(?:aged )?(?:under|below|younger than) (\d+)\b(?: years)?", lambda m: Concept(name="", kind="age", max_age=int(m[1]) - 1)),
    ]
    for pattern, make in rules:
        text = re.sub(pattern, lambda m: age(make(m))(m), text)
    for v in ctx.regions:
        if re.search(rf"\b{re.escape(v.lower())}\b", text):
            names.append(ctx.anon(Concept(name="", kind="region", value=v)))
            text = re.sub(rf"\b(?:in |living in )?{re.escape(v.lower())}\b", " ", text)
    for v in ctx.hospitals:
        if v.lower() in text:
            names.append(ctx.anon(Concept(name="", kind="hospital", value=v)))
            text = re.sub(rf"(?:at |in |of )?{re.escape(v.lower())}", " ", text)
    meas_names = {n: c.codes[0] for n, c in ctx.concepts.items() if c.kind == "measurement" and c.op is None}
    meas_names.update({k: v for k, v in KNOWN_MEASUREMENTS.items() if k not in meas_names})
    meas_re = "|".join(re.escape(n) for n in sorted(meas_names, key=len, reverse=True))

    defined = [v for n in ctx.concepts if not n.startswith("_") for v in _variants(n)]
    protected = [(d.start(), d.end()) for v in defined
                 for d in re.finditer(rf"(?<![\w-]){re.escape(v)}(?![\w-])", text)]

    def threshold(m: re.Match) -> str:
        if any(a < m.end() and m.start() < b and b - a > len(m["meas"]) for a, b in protected):
            return m[0]
        op = OP_WORDS.get(re.sub(r"\s+", " ", (m["op"] or "").lower()))
        if m["orw"]:
            op = OR_SUFFIX[m["orw"].split()[-1]]
        if op is None:
            return m[0]
        names.append(ctx.anon(Concept(name="", kind="measurement", codes=[meas_names[m["meas"]]], op=op,
                                      threshold=float(m["val"]))))
        return " "

    text = re.sub(rf"(?:(?:a|an|the) )?(?:(?:latest|most recent|last) )?(?P<meas>{meas_re})(?: \(loinc \d+-\d\))?(?: value)?"
                  rf"(?: (?:of|is|was))? (?P<op>{OP_RE})? ?(?P<val>\d+(?:\.\d+)?){UNIT}?(?: (?P<orw>or (?:more|higher|above|over|less|lower|below)))?{UNIT}?",
                  threshold, text, flags=I)
    text = re.sub(r"\(loinc \d+-\d\)", " ", text)
    if m := re.search(r"\b(?:in|during|started in|starting in) (20\d\d)\b", text):
        year = int(m[1])
        text = text[: m.start()] + " " + text[m.end():]
    return text, names, year


def _phrase(ctx: _Ctx, raw: str) -> _Phrase:
    """Concept names, negation and OR in a free-English phrase."""
    text = " " + raw.lower().replace("’", "'") + " "
    text, inline, year = _inline(ctx, text)
    text = re.sub(r"(?<=\w)'s\b", "", text)
    matches = _find(ctx, text)
    terms: Filter = [[Term(concept=n)] for n in inline]
    ratio = None
    measurements = []
    prev_end = 0
    for m in matches:
        gap = text[prev_end:m.start]
        if m.kind == "ratio":
            if ratio:
                raise InterpretError(f"two ratios in '{raw.strip()}'")
            ratio = m.name
            prev_end = m.end
            continue
        name = _resolve(ctx, m.name)
        ctx.named.add(name)
        concept = ctx.concepts[name]
        if year and concept.kind in ("encounter", "diagnosis") and concept.year is None:
            name = ctx.anon(concept.model_copy(update={"year": year}))
            year = None
        if concept.kind == "drug" and not concept.active and (ctx.active_rule or "active" in gap or "current" in gap):
            name = ctx.anon(concept.model_copy(update={"active": True}))
        if concept.kind == "measurement" and concept.op is None:
            measurements.append(name)
        term = Term(concept=name, negate=bool(NEG.search(gap)))
        if terms and re.fullmatch(r"\s*,?\s*or\s+(?:an?\s+|with\s+|active\s+)*", gap):
            terms[-1].append(term)
        else:
            terms.append([term])
        prev_end = m.end
    if year:
        raise InterpretError(f"a year in '{raw.strip()}' but nothing it could apply to")
    rest = text
    for m in reversed(matches):
        rest = rest[: m.start] + " " + rest[m.end:]
    leftover = [w for w in re.findall(r"[\w%+-]+", rest) if w not in FILLER and not w.isdigit()]
    if leftover:
        raise InterpretError(f"could not interpret {leftover} in '{raw.strip()}'")
    return _Phrase(terms=terms, ratio=ratio, measurements=measurements, text=raw.strip())


def _split_metrics(text: str) -> list[str]:
    """Split the metric list at commas and at 'and' that starts a new metric."""
    opener = r"(?:how many|number|the |their |its |that |those |%|share|percentage|proportion|mean|average|count|total)"
    parts = re.split(rf",\s*(?:and\s+)?|\s+and\s+(?={opener})", text.strip().rstrip("."))
    return [p.strip() for p in parts if p.strip() and not re.fullmatch(r"(?:with )?one decimal(?: place)?", p.strip(), I)]


def _has(f: Filter, name: str) -> bool:
    return any(t.concept == name and not t.negate for g in f for t in g)


def _merge(base: Filter, extra: Filter) -> Filter:
    out = [list(g) for g in base]
    for g in extra:
        if g not in out:
            out.append(g)
    return out


def _metrics(ctx: _Ctx, text: str) -> list[Metric]:
    metrics: list[Metric] = []
    subject: Filter | None = None
    subject_unit = "patients"
    for part in _split_metrics(text):
        low = part.lower()
        p = _phrase(ctx, part)
        # "how many of them ...", "how many have ...", "those with ...": counted within the subject
        pronoun = bool(PRONOUN.search(low) or re.match(r"\s*(?:how many|and|with|without|who|having)\b", low))
        base = subject if (pronoun and subject is not None) else []
        is_mean = re.search(r"\b(?:mean|average|avg)\b", low)
        is_pct = p.ratio or re.search(r"%|\b(?:share|percent|percentage|proportion|prevalence|fraction)\b", low)
        stays = _counts_stays(ctx, low, p)
        if is_mean:
            if len(p.measurements) != 1:
                raise InterpretError(f"'{part}': a mean needs exactly one measurement")
            meas = p.measurements[0]
            own = [g for g in p.terms if not (len(g) == 1 and g[0].concept == meas)]
            who = _merge(subject if (pronoun or not own) and subject is not None else [], own)
            metrics.append(Metric(label=part, op="mean", where=_merge(who, [[Term(concept=meas)]]), measurement=meas))
        elif is_pct:
            if p.ratio:
                num, den = ctx.ratios[p.ratio]
                numerator, denominator = _phrase(ctx, num).terms, _phrase(ctx, den).terms
            elif not p.terms:
                # "that share in %": the previous count over the subject
                if not metrics or subject is None or metrics[-1].where == subject:
                    raise InterpretError(f"'{part}': share of what?")
                numerator, denominator = metrics[-1].where, subject
            else:
                among = re.search(r"\b(?:among|of all|out of|within)\b(.+)$", low)
                of = re.search(r"(?:%|\bshare|\bpercent(?:age)?|\bproportion|\bprevalence|\bfraction|\brate)\s+of\s+(.+)$", low)
                cuts = list(re.finditer(r"\b(?:with|who|that|having|whose|without)\b", of[1])) if of else []
                if among and (den_terms := _phrase(ctx, among[1]).terms):
                    denominator = den_terms
                    numerator = _merge(denominator, _phrase(ctx, low[: among.start()]).terms)
                elif cuts:
                    # "% of A with B": A is the denominator ("% of them who ..." -> the subject)
                    cut = cuts[-1].start()
                    denominator = _phrase(ctx, of[1][:cut]).terms or subject or []
                    numerator = _merge(denominator, _phrase(ctx, of[1][cut:]).terms)
                else:
                    denominator = subject or []
                    numerator = _merge(denominator, p.terms)
            unit = "stays" if stays or (subject_unit == "stays" and not re.search(r"\bpatients?\b", low)) else "patients"
            metrics.append(Metric(label=part, op="percent", unit=unit, where=numerator, denominator=denominator))
        else:
            where = _merge(base, p.terms)
            metrics.append(Metric(label=part, op="count", unit="stays" if stays else "patients", where=where))
        if subject is None and metrics[-1].op == "count":
            subject, subject_unit = metrics[-1].where, metrics[-1].unit
    return metrics


def _counts_stays(ctx: _Ctx, low: str, p: _Phrase) -> bool:
    if re.search(r"\bpatients?\b|\bpeople\b", low):
        return False
    head = re.sub(r"^\s*(?:the )?(?:number of|count of|how many|total)\s+", "", low)
    if re.match(r"(?:\w+ )?(?:stays|encounters|admissions|visits)\b", head):
        return True
    first = p.terms[0][0] if p.terms and p.terms[0] else None
    if first is None or ctx.concepts[first.concept].kind != "encounter":
        return False
    # "inpatient stays", "hip fracture admissions": the stay is the counted thing
    return ctx.unit == "stays" or bool(re.search(r"\b(?:stays|encounters|admissions|visits|episodes)\b", low))


def _group_by(raw: str | None) -> list[str]:
    if not raw:
        return []
    dims = []
    for part in re.split(r"\s*,\s*|\s+and\s+|\s*×\s*|\s+x\s+|\s+by\s+", raw.strip().lower()):
        part = re.sub(r"\(.*?\)|^(?:the |patient'?s |treating |(?:each|every) )+| of (?:residence|the patient)$", "", part).strip()
        if part not in GROUP_WORDS:
            raise InterpretError(f"cannot group by '{part}' (known: region, age band, hospital, sex)")
        dims.append(GROUP_WORDS[part])
    return dims


def _record_types(text: str) -> list[str]:
    """'Provide encounters and medications of patients who ...' -> the part before 'of patients'."""
    low = re.sub(r"\b(?:main|primary) diagnos[ie]s\b", " ", text.lower())
    cohort = re.search(r"\b(?:of|for|from|on|about|belonging to)\s+(?:all\s+|the\s+|each\s+)*(?:patients|adults|women|men|children|people|persons"
                       r"|individuals|those|everyone|a cohort|the cohort)\b", low)
    if cohort and _record_types_in(low[: cohort.start()]):
        low = low[: cohort.start()]
    return _record_types_in(low)


def _record_types_in(low: str) -> list[str]:
    found = sorted(t for t, p in TABLE_WORDS.items() if re.search(rf"\b(?:{p})\b", low))
    if not found and re.search(r"\b(?:records|data|health data)\b", low):
        found = ["conditions", "encounters", "medications", "observations", "patients"]
    return found


def _unused(ctx: _Ctx, used: set[str]) -> Filter:
    """Defined filters the report never names describe the population (R08: breast cancer)."""
    out = []
    seen = set()
    for name, c in ctx.concepts.items():
        if name.startswith("_") or name in used or c.kind == "measurement" and c.op is None:
            continue
        key = c.model_dump_json(exclude={"name"})
        if key not in seen and not any(ctx.concepts[u].model_dump_json(exclude={"name"}) == key for u in used):
            seen.add(key)
            out.append([Term(concept=name)])
    return out


def parse(request: DataRequest, mode: Route, conn: sqlite3.Connection) -> QuerySpec:
    """Definitions (+ question for SPE record types) -> QuerySpec, or InterpretError."""
    ctx = _Ctx(
        mode=mode,
        regions=[r for (r,) in conn.execute("SELECT DISTINCT region FROM patients ORDER BY region")],
        hospitals=[h for (h,) in conn.execute("SELECT DISTINCT data_holder FROM patients ORDER BY data_holder")],
    )
    for definition in request.definitions:
        for sentence in split_sentences(definition):
            for rule, pattern in RULES:
                if m := pattern.fullmatch(sentence):
                    try:
                        _apply_rule(ctx, rule, m)
                    except _Decline:
                        continue
                    break
            else:
                raise InterpretError(f"could not interpret: {sentence}")

    if ctx.unit == "stays" and ctx.stay_year:
        for n, c in ctx.concepts.items():
            if c.kind == "encounter" and c.year is None:
                ctx.concepts[n] = c.model_copy(update={"year": ctx.stay_year})

    cohort: Filter = _phrase(ctx, ctx.cohort_expr).terms if ctx.cohort_expr else []
    group_by: list[str] = []
    metrics: list[Metric] = []
    if mode == "statistics":
        if ctx.report is None:
            raise InterpretError("no 'Report per ...' line")
        group_by = _group_by(ctx.report[0])
        metrics = _metrics(ctx, ctx.report[1])
    elif ctx.report and not cohort:
        # SPE without "Cohort =": the population the report line counts first
        first = _metrics(ctx, ctx.report[1])[0]
        cohort = first.where if first.op == "count" else first.denominator or []

    for r in ctx.ratios.values():
        _phrase(ctx, r[0]), _phrase(ctx, r[1])
    cohort = _merge(cohort, _unused(ctx, ctx.named))

    try:
        return QuerySpec(
            mode=mode,
            concepts=ctx.concepts,
            cohort=cohort,
            bands=ctx.bands,
            group_by=group_by,
            metrics=metrics,
            record_types=_record_types(request.question) if mode == "spe" else [],
        )
    except ValidationError as e:
        raise InterpretError(str(e.errors()[0]["msg"])) from e


def check_codes(spec: QuerySpec, conn: sqlite3.Connection) -> None:
    """Every code prefix must match something in the data."""
    lookups = {
        "diagnosis": "SELECT 1 FROM conditions WHERE icd10 LIKE ? LIMIT 1",
        "drug": "SELECT 1 FROM medications WHERE atc LIKE ? LIMIT 1",
        "measurement": "SELECT 1 FROM observations WHERE loinc LIKE ? LIMIT 1",
        "encounter": "SELECT 1 FROM encounters WHERE primary_icd10 LIKE ? LIMIT 1",
    }
    for c in spec.concepts.values():
        if c.codes and not any(
            conn.execute(lookups[c.kind], (code if c.kind == "measurement" else f"{code}%",)).fetchone()
            for code in c.codes
        ):
            raise NoDataError(f"'{c.name}': codes {c.codes} match nothing in the data")


def to_code_mapping(spec: QuerySpec) -> CodeMapping:
    columns = {"diagnosis": "icd10", "drug": "atc", "measurement": "loinc", "encounter": "primary_icd10"}
    return CodeMapping(terms={
        name: {columns[c.kind]: c.codes} for name, c in spec.concepts.items() if c.codes
    })


def map_codes(spec: ResultSpec) -> CodeMapping:
    """Pipeline hook: codes of the concepts the request defines (empty if not understood)."""
    from app import db

    conn = db.connect()
    try:
        return to_code_mapping(parse(spec.request, spec.route, conn))
    except InterpretError:
        return CodeMapping()
    finally:
        conn.close()
