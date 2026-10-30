"""Rule parser: wordings beyond the ten samples, checked against equivalent plain wordings."""

import pytest

from app.layers.mapping import InterpretError
from app.layers.query import extract
from app.models import DataRequest


def ask(conn, *definitions, mode="statistics", question="q"):
    """The answer with the metric values as a list per group: wordings differ, so the metric keys do too."""
    req = DataRequest(id="T", applicant={"name": "x", "organisation": "y", "country": "AT"},
                      requested_as="r", purpose="research", question=question, definitions=list(definitions))
    data = extract(req, mode, conn)
    return {k: list(v.values()) for k, v in data.items()} if mode == "statistics" else data


def test_definition_built_from_other_definitions(conn):
    plain = ask(conn, "Asthma = ICD-10 J45.", "Hypertension = ICD-10 I10.",
                "Report per region: patients with asthma and hypertension.")
    composite = ask(conn, "Asthma = ICD-10 J45.", "Hypertension = ICD-10 I10.",
                    "Both = patient with asthma and hypertension.", "Report per region: patients with both.")
    assert composite == plain


def test_unused_composite_restricts_the_cohort(conn):
    plain = ask(conn, "Report per sex: patients aged 65 or older living in Wien.")
    composite = ask(conn, "Viennese = patient whose region is 'Wien'.", "Senior = aged 65 or older.",
                    "Report per sex: seniors.")
    assert composite == plain


def test_stays_of_a_sub_population(conn):
    plain = ask(conn, "Asthma = ICD-10 J45.", "Visit = encounter of type outpatient.",
                "Count visits (not patients) whose start_date is in 2025.",
                "Report per region: visits.", "Cohort = patients with asthma.")
    of = ask(conn, "Asthma = ICD-10 J45.", "Visit = encounter of type outpatient.",
             "Count visits (not patients) of patients with asthma whose start_date falls in 2025.",
             "Report per region: visits.")
    assert of == plain


def test_reference_date_after_an_inline_age(conn):
    assert ask(conn, "COPD = ICD-10 J44.", "Report per sex: patients with COPD aged 40 or older on 31 December 2025.") == \
        ask(conn, "COPD = ICD-10 J44.", "Report per sex: patients with COPD aged 40 or older.")


def test_other_dates_are_refused(conn):
    with pytest.raises(InterpretError, match="reference date"):
        ask(conn, "COPD = ICD-10 J44.", "Report per sex: patients with COPD aged 40 or older on 1 January 2024.")


def test_short_measurement_name_and_measurement_wordings(conn):
    plain = ask(conn, "Use the latest systolic blood pressure (LOINC 8480-6) per patient.",
                "Report per sex: patients with a systolic blood pressure value, mean systolic blood pressure.")
    short = ask(conn, "Systolic blood pressure = the most recent value of LOINC 8480-6 for each patient.",
                "Report per sex: patients with a systolic value, mean systolic blood pressure.")
    assert short == plain


def test_drug_regardless_of_dates_is_not_restricted_to_active(conn):
    rule = ("A medication is active on 31 December 2025 if start_date is on or before that day "
            "and end_date is empty or on or after that day.")
    ever = ask(conn, "Statin = ATC C10AA, regardless of start or end date.", rule, "Report per sex: patients with a statin.")
    active = ask(conn, "Statin = ATC C10AA.", rule, "Report per sex: patients with a statin.")
    assert ever["female"][0] > active["female"][0]


def test_persons_and_people(conn):
    a = ask(conn, "Older person = aged 65 or older.", "Report per sex: older persons.")
    b = ask(conn, "Older person = aged 65 or older.", "Report per sex: older people.")
    assert a == b


def test_identified_by_wording(conn):
    assert ask(conn, "Asthma is identified by any diagnosis whose ICD-10 code begins with J45.",
               "Report per sex: patients with asthma.") == \
        ask(conn, "Asthma = ICD-10 J45.", "Report per sex: patients with asthma.")
