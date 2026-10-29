import json, re, sys, glob

# Each rule: name, compiled pattern (must match the WHOLE sentence), what it yields.
R = lambda p: re.compile(p, re.I)
RULES = [
  ("age_filter",   R(r"(?P<name>[\w\s]+?) = aged (?P<min>\d+) or older on (?P<date>\d+ \w+ \d{4})")),
  ("condition",    R(r"(?P<name>[\w\s\-]+?) = any condition with an ICD-10 code starting with (?P<codes>[A-Z]\d{2}(?:\.\d+)?(?:(?:, | or )[A-Z]\d{2}(?:\.\d+)?)*)")),
  ("drug",         R(r"(?P<name>[\w\s\-]+?) = ATC code starting with (?P<codes>[A-Z]\d{2}[A-Z]{0,2}\d{0,2}(?:(?:, | or )[A-Z]\d{2}[A-Z]{0,2}\d{0,2})*)")),
  ("sex_filter",   R(r"(?P<name>[\w\s]+?) = sex '(?P<sex>female|male)'")),
  ("encounter",    R(r"(?P<name>[\w\s]+?) = encounter with type '(?P<type>inpatient|outpatient|emergency)'")),
  ("age_bands",    R(r"Age bands on (?P<date>\d+ \w+ \d{4}): (?P<bands>\d+-\d+(?:, \d+-\d+)*(?:, \d+\+)?)")),
  ("latest_obs",   R(r"Use the latest (?P<name>[\w\s]+?) \(LOINC (?P<loinc>\d+-\d)\) per patient(?:; patients without an? [\w\s]+ are left out)?")),
  ("active_rule",  R(r"A medication is active on (?P<date>\d+ \w+ \d{4}) if start_date is on or before that day and end_date is empty or on or after that day")),
  ("count_unit",   R(r"Count (?P<unit>stays|encounters) \(not patients\) whose start_date is in (?P<year>\d{4})")),
  ("cohort",       R(r"Cohort = (?P<expr>.+)")),
  ("ratio",        R(r"(?P<name>[\w\s]+?) = (?P<num>[\w\s]+?) / (?P<den>[\w\s]+?) × 100, one decimal")),
  ("report_cohort",R(r"Report the size of the cohort.*")),
  ("report",       R(r"Report (?:per (?P<group>[\w\s]+?)|for each (?P<each>patient)(?: in (?P<region>[\w\s]+?))?): (?P<metrics>.+)")),
]

def split(sentence):
    # "ACE inhibitor = ... C09AA; ARB = ... C09CA." holds two definitions
    s = sentence.strip().rstrip(".")
    parts = [p.strip() for p in s.split(";")]
    return parts if all(" = " in p for p in parts) else [s]

unmatched, hits = [], 0
for f in sorted(glob.glob(sys.argv[1] + "/R*.json")):
    req = json.load(open(f))
    print(f"\n== {req['id']}")
    for d in req["definitions"]:
        for part in split(d):
            found = [(n, m) for n, p in RULES if (m := p.fullmatch(part))]
            if not found:
                unmatched.append((req["id"], part)); print(f"  NO MATCH     {part}")
                continue
            n, m = found[0]; hits += 1
            g = {k: v for k, v in m.groupdict().items() if v}
            print(f"  {n:<13}{g}")
print(f"\nmatched {hits}, unmatched {len(unmatched)}")
