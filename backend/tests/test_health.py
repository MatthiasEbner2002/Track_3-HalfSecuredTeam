from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app

# Copy of requests/R01.json from the TRACK-3-ehds repo (real request shape).
R01 = {
    "id": "R01",
    "submitted": "2026-10-29T18:30:00+01:00",
    "applicant": {
        "name": "Dr. Sophie Lechner",
        "organisation": "Danube Institute for Diabetes Research (university research group)",
        "country": "AT",
    },
    "requested_as": "health data request",
    "requested_format": "anonymised statistics",
    "purpose": "We study regional differences in type 2 diabetes to plan a prevention "
    "programme with the regional health authorities.",
    "question": "How many adults have type 2 diabetes in each region and what is the prevalence?",
    "definitions": [
        "Adult = aged 18 or older on 31 December 2025.",
        "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
        "Prevalence = adults with type 2 diabetes / all adults in the region × 100, one decimal.",
        "Report per region: adults, adults with type 2 diabetes and the prevalence.",
    ],
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:  # runs the lifespan
        yield c


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_submit_request_parses_and_is_not_implemented(client: TestClient) -> None:
    # 501 (not 422) proves the body validated against DataRequest.
    response = client.post("/api/requests", json=R01)
    assert response.status_code == 501
    assert response.json() == {"detail": "Not implemented yet"}
