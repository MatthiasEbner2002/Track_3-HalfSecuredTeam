"""Generate the end-to-end black-box cases E001..E200 (decision + routing + data).

Each case is a full request in the shape of requests/R01.json plus what the whole
pipeline must return:

  expected.decision  REJECT | APPROVE_STATISTICAL | APPROVE_SPE
  expected.articles  as in requests/expected.json ("54" / "53","69" / "53","66","69" / "53","66","68","73")
  expected.point     the Art. 54 / Art. 53(1) point the purpose falls under (information only)
  expected.answer    statistics {group: [values]}, spe {cohort_size, record_types},
                     {"error": true} if the data layer must refuse, null for rejects

The questions, definitions and data answers are taken from the extraction black-box set
(tests/fixtures/blackbox/cases, answers computed from the db without the engine). This
script pairs them with new applicants and purposes, so that every branch of the decision
table is covered. Pairing is deterministic (fixed seed).

Run:  python3 tests/fixtures/e2e/generate.py   (from backend/)
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLACKBOX = HERE.parent / "blackbox" / "cases"
OUT = HERE / "cases"

HDR, HDA = "health data request", "health data access application"
N_REJECT, N_STAT, N_DOWNGRADE, N_SPE = 70, 60, 25, 45
N_STAT_ERRORS = 10  # of N_STAT: questions the rules must refuse to interpret

NAMES = [
    "Dr. Sophie Lechner", "Mag. Thomas Huber", "Dr. Anna Kowalska", "Prof. Jan Novak", "Dr. Elena Rossi",
    "Dr. Lukas Berger", "Dr. Marie Dubois", "Dr. Pieter de Vries", "Mag. Petra Steiner", "Dr. Felix Wagner",
    "Dr. Laura Moser", "DI Markus Pichler", "Dr. Nina Hofer", "Dr. Daniel Reiter", "Dr. Julia Brandstätter",
]
COUNTRIES = ["AT", "AT", "AT", "DE", "CZ", "IT", "SI", "NL", "FR", "PL"]

# (organisation, purpose, Art. 54 point). Organisation + purpose are all the decision layer sees.
REJECT = [
    ("Alpenland Versicherung AG, pricing department",
     "We want to align private health insurance premiums for customers with this condition with their actual risk.", "54(b)"),
    ("Donau Leben Versicherung AG, underwriting",
     "We decide which applicants for life insurance we decline, based on how common this disease is in their age group and region.", "54(b)"),
    ("Bergbank AG, retail credit risk unit",
     "We want to adjust mortgage interest rates for applicants with this diagnosis to reflect the higher default risk.", "54(b)"),
    ("Hofer Bau GmbH, recruiting",
     "Construction work is physically demanding. We want to screen job applicants for a history of this disease and not hire the affected ones.", "54(b)"),
    ("Berger Logistik GmbH, HR department",
     "We want to reduce sick leave. We will use the results to decide whose fixed-term contracts we do not renew.", "54(b)"),
    ("Wohnbau Kärnten Genossenschaft, tenant selection",
     "We want to exclude applicants with this diagnosis from our new housing project to keep the community healthy.", "54(a)"),
    ("Stadtwohnen GmbH, tenant management",
     "We want to know which age groups of tenants are affected so we can decline their applications for subsidised flats.", "54(a)"),
    ("Kreditschutz Austria, scoring",
     "The figures feed our consumer credit score: people in regions with high prevalence get a lower score and worse loan conditions.", "54(b)"),
    ("Brightline Health Marketing GmbH",
     "Our client launches a new drug for this condition. We want to focus our physician advertising campaign on the regions where it is most common.", "54(c)"),
    ("Cardiopharm Austria GmbH, sales",
     "Our field sales team should visit the GPs in the regions with the most affected patients to promote our product.", "54(c)"),
    ("Vital Apotheken Marketing OG",
     "We plan a direct-mail advertising campaign for food supplements and want to target the districts with the most patients.", "54(c)"),
    ("MediAds Online GmbH",
     "We sell targeted online advertising for private clinics and want to place ads where the prevalence is highest.", "54(c)"),
    ("Nordic Pouch Company, product development",
     "We are developing new nicotine pouch flavours and want to know which age groups and regions are most affected, to position the products as a 'healthier alternative'.", "54(d)"),
    ("Alpenbräu AG, new products",
     "We are developing a sugar-free beer for people with this condition and want to size the target group per region.", "54(d)"),
    ("CloudVape Ltd, product development",
     "We design new e-cigarette flavours and want to know in which age groups smoking-related disease is most common.", "54(d)"),
    ("Spielbank Austria Online, growth team",
     "We develop an online gambling app and want to target regions where depression and loneliness are common, because those users play more.", "54(d)"),
    ("Zucker & Co Süsswaren GmbH, marketing",
     "We launch an energy drink for teenagers and want to advertise it in the regions with the most young patients.", "54(c)"),
    ("Privatklinik Sonnenhof, patient acquisition",
     "We want to send advertising letters for our private treatment programme to the regions with the most patients.", "54(c)"),
    ("Securitas Personalservice GmbH, staffing",
     "Before we place temporary workers we want to know which age groups carry this disease, so we can offer those groups only short-term contracts.", "54(b)"),
    ("Autoversicherung Direkt AG, actuarial",
     "We want to raise car insurance premiums in the age bands with the most patients, since the disease impairs driving.", "54(b)"),
    ("FitLife Private Insurance, product design",
     "We will exclude this diagnosis from coverage in our new tariffs and need the prevalence per region to price the exclusion.", "54(b)"),
    ("Immobilien Steiermark GmbH, letting",
     "We let flats and want to avoid tenants from age groups with high rates of mental illness; the figures tell us whom to turn down.", "54(a)"),
    ("Tabakwaren Handels GmbH, product strategy",
     "We develop a new cigarette brand with lower tar and want to know where the lung-disease burden is highest to launch it there first.", "54(d)"),
    ("Payday Kredit GmbH, risk",
     "We offer short-term loans and want to charge higher interest in regions where chronic illness is common, because of the higher default risk.", "54(b)"),
]

# (organisation, purpose, Art. 53(1) point). No SPE wording: these are answered as statistics.
APPROVE = [
    ("Danube Institute for Diabetes Research (university research group)",
     "Scientific research on the epidemiology of chronic diseases to inform prevention programmes.", "53(1)(e)"),
    ("Austrian Public Health Institute (GÖG)",
     "Public health monitoring of chronic disease burden for the regional health authorities.", "53(1)(a)"),
    ("Statistik Austria, Health Statistics Unit",
     "Production of official health statistics for the national health report.", "53(1)(c)"),
    ("Regional health planning authority Oberösterreich",
     "We plan hospital capacity and care pathways for the coming years and need the regional disease burden.", "53(1)(b)"),
    ("Medical University of Warsaw, Cardiology Research Unit",
     "Scientific research on medication use and treatment gaps in routine care.", "53(1)(e)"),
    ("University of Applied Sciences, nursing programme",
     "Our nursing students learn descriptive statistics on real-world patterns of chronic disease in a teaching module.", "53(1)(d)"),
    ("Public health agency, tobacco control unit",
     "We evaluate our anti-smoking campaign and want to see whether diagnoses among younger age groups are declining.", "53(1)(a)"),
    ("Austrian social health insurance fund, disease management programmes",
     "We evaluate whether our prevention programme reduced hospital admissions, to decide whether to extend it to all regions.", "53(1)(f)"),
    ("Institute for General Practice (university)",
     "We want to compare how often the recommended drugs are used in different age groups, to update a teaching module for GPs.", "53(1)(d)"),
    ("Federal Ministry of Health, planning department",
     "Policy-making: planning the number of specialist outpatient clinics per region for the next health plan.", "53(1)(b)"),
    ("INSERM Clinical Epidemiology Team",
     "Scientific research on regional differences in the quality of chronic disease care, published in a peer-reviewed journal.", "53(1)(e)"),
    ("Charles University, Department of Epidemiology",
     "Epidemiological research on age and sex differences in chronic disease prevalence for a European comparison study.", "53(1)(e)"),
    ("Regional patient safety board",
     "We want to improve the delivery of care by finding regions where guideline-recommended treatment is under-used.", "53(1)(f)"),
    ("Istituto Superiore di Sanità",
     "Public health surveillance of non-communicable diseases for the European health monitoring network.", "53(1)(a)"),
    ("Health technology assessment agency (AIHTA)",
     "Health technology assessment: we estimate the number of patients eligible for a new treatment to inform reimbursement policy.", "53(1)(b)"),
    ("University Medical Center Utrecht, Julius Center",
     "Scientific research on treatment gaps in elderly patients to develop a clinical guideline update.", "53(1)(e)"),
    ("Regional nursing care planning office",
     "We plan community nursing capacity and need the number of patients with chronic conditions per region.", "53(1)(b)"),
    ("Medical faculty, public health curriculum",
     "Teaching material for a master's course in public health, showing real regional prevalence figures.", "53(1)(d)"),
    ("Cardiopharm Austria GmbH, drug safety (pharmacovigilance)",
     "Post-authorisation safety study requested by the medicines agency: we monitor how the treated population is distributed by age and region.", "53(1)(e)"),
    ("National statistics office, population statistics",
     "Official statistics on the prevalence of chronic diseases for the national health report.", "53(1)(c)"),
    ("Institute for Occupational Health (university)",
     "We study chronic disease among working-age adults to recommend better prevention at the workplace to the ministry.", "53(1)(a)"),
    ("Hospital association quality programme",
     "We benchmark the quality of chronic disease care between hospitals to improve treatment for all patients.", "53(1)(f)"),
    ("Regional government, health department",
     "We evaluate the regional screening programme and need prevalence figures by region and age band.", "53(1)(a)"),
    ("European research consortium on ageing (Horizon Europe)",
     "Scientific research on multimorbidity in older adults as part of a publicly funded research project.", "53(1)(e)"),
]

# Access applications that explain why aggregates are not enough (route: SPE).
SPE = [
    ("RhythmAI GmbH, medical device start-up",
     "We train and validate an AI model that detects the disease early. Aggregated statistics are not enough: the model needs patient-level records; only model weights and performance metrics leave the secure environment.", "53(1)(e)"),
    ("Danube Institute for Diabetes Research (university research group)",
     "Scientific research on treatment pathways that requires patient-level longitudinal records; only aggregated results leave the secure processing environment.", "53(1)(e)"),
    ("University hospital, clinical decision support group",
     "We develop and validate a clinical decision-support algorithm. Summary tables are insufficient because the algorithm is evaluated per patient; only model parameters and evaluation metrics are exported.", "53(1)(f)"),
    ("Medical University of Vienna, biostatistics",
     "Survival analysis of time from diagnosis to first hospital admission; statistics are not enough because the model needs individual-level follow-up times.", "53(1)(e)"),
    ("Austrian Public Health Institute (GÖG)",
     "We validate a risk prediction model for the public health authority. Aggregates are not sufficient: the prediction error must be measured for each patient inside the secure environment.", "53(1)(a)"),
    ("Charles University, Department of Epidemiology",
     "Scientific research on multimorbidity trajectories that needs record-level data on the order of diagnoses; only aggregated results are published.", "53(1)(e)"),
    ("Health technology assessment agency (AIHTA)",
     "We train a cost-prediction model for the reimbursement decision; summary tables are not enough because costs are modelled per patient. Only coefficients leave the environment.", "53(1)(b)"),
    ("INSERM Clinical Epidemiology Team",
     "Scientific research on treatment sequences: we need patient-level medication histories, since aggregated statistics cannot show the order of prescriptions.", "53(1)(e)"),
    ("Regional patient safety board",
     "To improve care we validate an early-warning algorithm against each patient's course; aggregates are insufficient. Only performance metrics are exported.", "53(1)(f)"),
    ("Medical University of Graz, cardiology research",
     "Scientific research that trains a prognostic model on individual-level records; only model weights and calibration plots leave the secure processing environment.", "53(1)(e)"),
    ("University of Applied Sciences, health informatics master",
     "Students train and validate prediction models on pseudonymised patient-level data inside the secure environment as part of their teaching course; nothing but metrics is exported.", "53(1)(d)"),
    ("National statistics office, methodology unit",
     "We validate a small-area estimation model for official statistics; aggregated statistics are not sufficient as the model is fitted on record-level data.", "53(1)(c)"),
]

SPE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(aggregates?|statistics|summary tables?) (are|is) (not enough|insufficient|not sufficient)\b",
        r"\b(individual|patient|person|record)[- ]level\b",
        r"\b(train|validat)\w* .{0,30}\b(model|algorithm)s?\b",
    )
]


def _spe_wording(text: str) -> bool:
    return any(p.search(text) for p in SPE_PATTERNS)


def _check_pools() -> None:
    for org, purpose, _ in APPROVE + REJECT:
        assert not _spe_wording(purpose), f"SPE wording in a non-SPE purpose: {org}"
    for org, purpose, _ in SPE:
        assert _spe_wording(purpose), f"no SPE wording: {org}"


def load_blackbox() -> tuple[list[dict], list[dict], list[dict]]:
    stats, spe, errors = [], [], []
    for f in sorted(BLACKBOX.glob("B*.json")):
        case = json.loads(f.read_text(encoding="utf-8"))
        if case["expected"] == {"error": True}:
            errors.append(case)
        elif case["mode"] == "spe":
            spe.append(case)
        else:
            stats.append(case)
    assert stats and spe and errors, "run tests/fixtures/blackbox/generate.py first"
    return stats, spe, errors


def main() -> None:
    _check_pools()
    stats, spe_q, errors = load_blackbox()
    rng = random.Random(2025)

    def cycle(pool: list) -> list:
        """Deterministic shuffled cycle, long enough for any count we draw."""
        out: list = []
        while len(out) < 200:
            block = list(pool)
            rng.shuffle(block)
            out += block
        return out

    rejects, approves, spes = cycle(REJECT), cycle(APPROVE), cycle(SPE)
    stat_qs, spe_qs, err_qs = cycle(stats), cycle(spe_q), cycle(errors)

    cases: list[dict] = []

    def add(requested_as: str, who: tuple[str, str, str], source: dict, expected: dict, note: str) -> None:
        n = len(cases) + 1
        src = source["request"]
        request = {
            "id": f"E{n:03d}",
            "submitted": "2026-10-30T09:00:00+01:00",
            "applicant": {"name": NAMES[n % len(NAMES)], "organisation": who[0], "country": COUNTRIES[n % len(COUNTRIES)]},
            "requested_as": requested_as,
            "requested_format": "pseudonymised data in a secure processing environment" if requested_as == HDA else "anonymised statistics",
            "purpose": who[1],
            "question": src["question"],
            "definitions": src["definitions"],
        }
        if expected["decision"] != "REJECT":  # the route must follow from the text, not from luck
            full_text = " ".join([who[1], src["question"], *src["definitions"]])
            assert _spe_wording(full_text) == (expected["decision"] == "APPROVE_SPE"), request["id"]
        cases.append({"request": request, "expected": {"point": who[2], **expected}, "note": note, "source": src["id"]})

    # 1. rejects: mostly statistics questions as health data requests, some access applications
    for i in range(N_REJECT):
        who = rejects[i]
        if i % 5 == 4:
            add(HDA, who, spe_qs[i], {"decision": "REJECT", "articles": ["54"], "answer": None}, "prohibited purpose, access application")
        else:
            add(HDR, who, stat_qs[i], {"decision": "REJECT", "articles": ["54"], "answer": None}, "prohibited purpose, health data request")

    # 2. approved health data requests -> statistics (Art. 53, 69)
    for i in range(N_STAT - N_STAT_ERRORS):
        add(HDR, approves[i], stat_qs[N_REJECT + i],
            {"decision": "APPROVE_STATISTICAL", "articles": ["53", "69"], "answer": stat_qs[N_REJECT + i]["expected"]},
            "allowed purpose, statistics")
    for i in range(N_STAT_ERRORS):
        add(HDR, approves[N_STAT + i], err_qs[i],
            {"decision": "APPROVE_STATISTICAL", "articles": ["53", "69"], "answer": {"error": True}},
            "allowed purpose, but the question cannot be interpreted from the definitions")

    # 3. access applications without SPE justification -> downgraded to statistics (Art. 53, 66, 69)
    for i in range(N_DOWNGRADE):
        q = stat_qs[N_REJECT + N_STAT + i]
        add(HDA, approves[N_STAT + N_STAT_ERRORS + i], q,
            {"decision": "APPROVE_STATISTICAL", "articles": ["53", "66", "69"], "answer": q["expected"]},
            "allowed purpose, no reason why statistics are not enough: downgrade")

    # 4. access applications with SPE justification -> SPE (Art. 53, 66, 68, 73)
    for i in range(N_SPE):
        q = spe_qs[N_REJECT + i]
        add(HDA, spes[i], q,
            {"decision": "APPROVE_SPE", "articles": ["53", "66", "68", "73"], "answer": q["expected"]},
            "allowed purpose, patient-level data justified")

    assert len(cases) == 200, len(cases)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("E*.json"):
        old.unlink()
    for case in cases:
        (OUT / f"{case['request']['id']}.json").write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from collections import Counter

    tally = Counter((c["expected"]["decision"], ",".join(c["expected"]["articles"])) for c in cases)
    for (decision, articles), n in sorted(tally.items()):
        print(f"{n:4d}  {decision:<20} Art. {articles}")
    print(f"wrote {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()
