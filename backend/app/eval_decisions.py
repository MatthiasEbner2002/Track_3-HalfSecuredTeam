"""Evaluate the decision layer on the 10 task requests and the held-out set.

    python -m app.eval_decisions            # one run, prints every decision
    python -m app.eval_decisions --runs 3   # also checks that 3 runs agree

The held-out requests (tests/data/heldout_requests.json) were written from the
regulation's own examples, not from the task requests, to catch overfitting.
"""

import argparse
import json
import time

from app.config import REPO_ROOT, REQUESTS_DIR
from app.layers import decision, language
from app.models import DataRequest

HELDOUT = REPO_ROOT / "backend" / "tests" / "data" / "heldout_requests.json"


def cases() -> list[tuple[DataRequest, bool, str | None]]:
    """(request, should be accepted, expected point or None if any point is fine)."""
    expected = json.loads((REQUESTS_DIR / "expected.json").read_text())
    out = []
    for path in sorted(REQUESTS_DIR.glob("R*.json")):
        request = DataRequest.model_validate_json(path.read_text())
        out.append((request, expected[request.id]["decision"] != "REJECT", None))
    for item in json.loads(HELDOUT.read_text()):
        out.append((DataRequest.model_validate(item), item["expect"] == "ACCEPT", item["point"]))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    correct = points_ok = points_total = unverified = inconsistent = 0
    slowest = 0.0
    all_cases = cases()
    for request, accept, point in all_cases:
        results = []
        for _ in range(args.runs):
            start = time.time()
            results.append(decision.decide(language.localise(request)[0]))
            slowest = max(slowest, time.time() - start)
        d = results[0]
        inconsistent += len({r.model_dump_json() for r in results}) > 1
        ok = d.accepted == accept
        correct += ok
        cited = d.grounding[0].id if d.grounding else "-"
        if point:
            points_total += 1
            points_ok += cited == point
        unverified += d.needs_review
        flag = "OK " if ok else "BAD"
        point_note = "" if not point or cited == point else f" (expected {point})"
        review = " REVIEW" if d.needs_review else ""
        print(f"{request.id} {flag} {'accept' if d.accepted else 'REJECT'} {cited}{point_note}{review}")
        print(f"     {d.reason}\n")

    n = len(all_cases)
    print(f"decisions {correct}/{n}  points {points_ok}/{points_total}  needs review {unverified}/{n}  "
          f"slowest {slowest:.1f}s" + (f"  inconsistent {inconsistent}/{n}" if args.runs > 1 else ""))


if __name__ == "__main__":
    main()
